"""Step 3 regressions: live, read-only, provenance-based application state."""
from __future__ import annotations

import contextlib
from io import StringIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cli
from application_state import (
    application_status, application_status_for_path, scan_application_workspace,
    format_application_status, _selected_job,
)
from application_workspace import ApplicationWorkspaceError, initialize_application
from latex_workflow import prepare_latex_documents, validate_latex_documents

RESUME = r"""\documentclass{article}
\begin{document}
My technical resume describes backend service maintenance, Python development,
API integrations and team collaboration across multiple software projects.
These efforts were carried out in development settings with clear role boundaries.
\end{document}
"""
COVER = r"""\documentclass{article}
\begin{document}
I am applying for this engineering position based on experience with Python,
backend APIs and software development projects. I enjoy collaborating on
well-tested software and would welcome an opportunity to contribute to the team.
\end{document}
"""


class ApplicationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.params = dict(company_name="Acme Inc", position_title="AI Developer", application_date="2026-11-20")
        self.name = "acme_inc/2026-11-20-ai_developer"
        self.job = self.root / "applications" / self.name
        (self.root / "templates").mkdir()
        (self.root / "templates/resume_template.tex").write_text(RESUME)
        (self.root / "templates/cover_letter_template.tex").write_text(COVER)

    def status(self):
        return application_status(self.root, **self.params)

    def initialize(self):
        initialize_application(self.root, **self.params,
                               job_description_text="Build backend software and Python API services.",
                               job_description_source="Candidate-supplied posting")

    def stage(self):
        self.initialize()
        prepare_latex_documents(self.root, **self.params)

    def check(self, key, expected):
        result = self.status()
        self.assertEqual(result["stages"][key]["status"], expected, result)
        self.assertFalse(result["submission_ready"])
        return result

    def test_empty_workspace_read_only(self):
        self.check("source_capture", "missing")
        self.assertFalse((self.root / "applications").exists())
        found = scan_application_workspace(self.root)
        self.assertEqual(found, {"applications": [], "anomalies": []})
        self.assertFalse((self.root / "applications").exists())

    def test_capture_is_complete_but_not_drafts(self):
        self.initialize()
        report = self.check("source_capture", "complete")
        self.assertEqual(report["stages"]["documents_staged"]["status"], "missing")
        self.assertEqual(report["stages"]["independent_reviews"]["status"], "not_verified")
        self.assertEqual(report["stages"]["candidate_signoff"]["status"], "not_verified")
        self.assertEqual(report["overall"], "in_progress")

    def test_staged_documents_not_assumed_tailored_or_validated(self):
        self.stage()
        report = self.check("documents_staged", "complete")
        self.assertIn("tailoring not established", report["stages"]["documents_staged"]["reason"])
        self.assertEqual(report["stages"]["latex_validation"]["status"], "missing")
        self.assertEqual(report["stages"]["claim_audit"]["status"], "missing")

    def test_untracked_latex_and_mismatched_source_are_not_verified(self):
        self.initialize()
        (self.job / "resume.tex").write_text(RESUME)
        self.check("documents_staged", "invalid")
        (self.job / "resume.tex").unlink()
        prepare_latex_documents(self.root, **self.params)
        (self.root / "templates/resume_template.tex").write_text(RESUME + "\n% template revision\n")
        self.check("documents_staged", "invalid")

    def test_matrix_file_is_existential_evidence_not_review(self):
        self.initialize()
        (self.job / "qualification_matrix.md").write_text("## Real candidate matrix\n")
        self.check("qualification_matrix", "complete")
        self.assertEqual(self.status()["stages"]["independent_reviews"]["status"], "not_verified")
        (self.job / "qualification_matrix.md").write_text("")
        self.check("qualification_matrix", "invalid")

    def test_symlinked_matrix_cannot_pass(self):
        self.initialize()
        target = self.root / "elsewhere.md"
        target.write_text("## Untrusted content\n")
        (self.job / "qualification_matrix.md").symlink_to(target)
        # Safeguard may fail at directory boundary rather than returning status.
        with self.assertRaises(ApplicationWorkspaceError):
            self.status()
        self.assertEqual(target.read_text(), "## Untrusted content\n")

    def test_validation_failure_detected_even_if_pdfs_exist(self):
        self.stage()
        build = self.job / "build"
        build.mkdir()
        (build / "validation.json").write_text(json.dumps({"status":"validation_failed"}))
        self.check("latex_validation", "blocked")
        self.assertEqual(self.status()["overall"], "needs_attention")

    def test_validation_claimed_ok_without_pdfs_is_stale(self):
        self.stage()
        build = self.job / "build"
        build.mkdir()
        (build / "validation.json").write_text(json.dumps({"status":"ok", "documents":{}}))
        self.check("latex_validation", "stale")

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "Needs TeX toolchain")
    def test_real_latex_current_then_stale_on_edit(self):
        self.stage()
        outcome = validate_latex_documents(self.root, **self.params)
        self.assertEqual(outcome["status"], "ok", outcome)
        report = self.check("latex_validation", "complete")
        self.assertEqual(report["overall"], "in_progress")
        self.assertEqual(report["stages"]["claim_audit"]["status"], "missing")
        (self.job / "resume.tex").write_text(RESUME + "\n% later edit\n")
        self.check("latex_validation", "stale")

    def test_audit_freshness_is_not_synonymous_with_candidate_approval(self):
        self.stage()
        build = self.job / "build"
        build.mkdir()
        (build / "claim_audit.json").write_text("{}")
        with patch("application_state.check_claim_audit", return_value={"status":"review_required", "fresh":True}):
            result = self.check("claim_audit", "complete")
        self.assertEqual(result["stages"]["candidate_signoff"]["status"], "not_verified")
        self.assertEqual(result["stages"]["independent_reviews"]["status"], "not_verified")
        with patch("application_state.check_claim_audit", return_value={"status":"blocked", "fresh":True}):
            self.check("claim_audit", "blocked")
        with patch("application_state.check_claim_audit", return_value={"status":"stale", "fresh":False}):
            self.check("claim_audit", "stale")

    def test_invalid_audit_cannot_promote_stage(self):
        self.stage()
        build = self.job / "build"
        build.mkdir()
        (build / "claim_audit.json").write_text("not json")
        self.check("claim_audit", "invalid")

    def test_noncanonical_sibling_warns_but_canonical_job_not_lost(self):
        self.stage()
        (self.root / "applications/company-a/2026-11-20-Senior_Software_Engineer_Applied_AI").mkdir(parents=True)
        result = scan_application_workspace(self.root)
        self.assertEqual(len(result["applications"]), 1)
        self.assertIn("company-a", result["anomalies"][0]["path"])
        self.assertNotIn("company-a", format_application_status(result["applications"][0]))

    def test_bad_identifier_and_symlink_company_rejected(self):
        with self.assertRaises(ApplicationWorkspaceError):
            _selected_job("company-a/2026-11-20-role")
        with self.assertRaises(ApplicationWorkspaceError):
            _selected_job("company/../role")
        (self.root / "applications").mkdir()
        (self.root / "applications/acme_inc").symlink_to(self.root / "templates", target_is_directory=True)
        warnings = scan_application_workspace(self.root)["anomalies"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("unsafe", warnings[0]["issue"])

    def test_cli_single_status_and_json_do_not_start_model(self):
        self.initialize()
        with patch.object(cli, "_run_to_completion", side_effect=AssertionError("must not invoke model")):
            with patch("pathlib.Path.cwd", return_value=self.root):
                with contextlib.redirect_stdout(StringIO()) as out:
                    self.assertEqual(cli.main(["--application-status", self.name, "--application-status-json"]), 0)
                self.assertEqual(json.loads(out.getvalue())["position_directory"],
                                 "/workspace/applications/" + self.name)
        self.assertFalse((self.job / "build").exists())

    def test_cli_all_status_lists_noncanonical_paths(self):
        self.initialize()
        (self.root / "applications/company-a").mkdir()
        with patch("pathlib.Path.cwd", return_value=self.root):
            with contextlib.redirect_stdout(StringIO()) as out:
                self.assertEqual(cli.main(["--application-status"]), 0)
        self.assertIn("WARNING: applications/company-a", out.getvalue())
        self.assertIn("source_capture: complete", out.getvalue())

    def test_cli_status_rejects_path_traversal_and_flag_mismatch(self):
        with patch("pathlib.Path.cwd", return_value=self.root):
            with contextlib.redirect_stderr(StringIO()):
                self.assertEqual(cli.main(["--application-status", "../x"]), 2)
                self.assertEqual(cli.main(["--application-status-json"]), 2)

    def test_attempted_actions_scope_without_claiming_success(self):
        targets = set()
        cli._record_attempted_application({"name":"initialize_application", "args": self.params}, targets)
        self.assertEqual(targets, {self.name})
        result = application_status_for_path(self.root, self.name)
        self.assertEqual(result["overall"], "not_initialized")
        cli._record_attempted_application({"name":"write_file", "args":{
            "file_path":"/workspace/applications/company-a/2026-11-20-Role/resume.tex"}},targets)
        self.assertEqual(targets, {self.name})
        cli._record_attempted_application({"name":"write_file", "args":{
            "file_path":"/workspace/applications/acme_inc/2026-11-20-ai_developer/qualification_matrix.md"}},targets)
        self.assertEqual(targets, {self.name})

    def test_normal_turn_reports_attempted_application_without_false_success(self):
        from contextlib import contextmanager
        self.initialize()
        fake_langsmith = ModuleType("langsmith")
        @contextmanager
        def tracing_context(**kwargs):
            self.assertFalse(kwargs["enabled"])
            yield
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: object()
        def fake_invoke(_graph, _payload, **kwargs):
            cli._record_attempted_application(
                {"name": "prepare_latex_documents", "args": self.params},
                kwargs["application_targets"],
            )
            # No actual staging was done; the summary must not claim it.
            return {"messages": [SimpleNamespace(type="ai", content="Will write a resume", tool_calls=[])]}
        with patch.dict(sys.modules, {"langsmith": fake_langsmith, "agent": fake_agent}):
            with patch("pathlib.Path.cwd", return_value=self.root):
                with patch.object(cli, "_run_to_completion", fake_invoke):
                    with contextlib.redirect_stdout(StringIO()) as stdout:
                        self.assertEqual(cli.main(["--message", "Build application"]), 0)
        output = stdout.getvalue()
        self.assertIn("source_capture: complete", output)
        self.assertIn("documents_staged: missing", output)
        self.assertIn("submission_ready=false", output)
        self.assertIn("Assistant: Will write a resume", output)

    def test_status_scan_never_creates_status_blob(self):
        self.stage()
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())
        scan_application_workspace(self.root)
        application_status_for_path(self.root, self.name)
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        self.assertFalse((self.job / "build/application_state.json").exists())


if __name__ == "__main__":
    unittest.main()
