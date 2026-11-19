"""Patch B regressions: staging, immutable templates, HITL-gated compile API."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application_workspace import initialize_application
from latex_workflow import LatexWorkflowError, prepare_latex_documents, validate_latex_documents


RESUME_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\newcommand{\person}{Applicant}
\begin{document}
\section*{Experience}
{{RESUME_CONTENT}}
\end{document}
"""
LETTER_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\begin{document}
{{LETTER_CONTENT}}
\end{document}
"""


class LatexWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "templates").mkdir()
        (self.root / "templates/resume_template.tex").write_text(RESUME_TEMPLATE)
        (self.root / "templates/cover_letter_template.tex").write_text(LETTER_TEMPLATE)
        self.params = dict(company_name="Acme Inc", position_title="AI Developer", application_date="2026-11-19")
        initialize_application(
            self.root, **self.params,
            job_description_text="Build production services using Python and manage API integrations.",
            job_description_source="Candidate supplied position description",
        )
        self.position = self.root / "applications/acme_inc/2026-11-19-ai_developer"

    def stage(self):
        return prepare_latex_documents(self.root, **self.params)

    def validate(self):
        return validate_latex_documents(self.root, **self.params)

    def populate(self, *, cover_text=None, resume_text=None):
        if resume_text is None:
            resume_text = ("Built Python services and maintained APIs for several internal applications. " * 5)
        if cover_text is None:
            cover_text = ("I am interested in this engineering role and have experience with Python APIs. " * 7)
        (self.position / "resume.tex").write_text(RESUME_TEMPLATE.replace("{{RESUME_CONTENT}}", resume_text))
        (self.position / "cover_letter.tex").write_text(LETTER_TEMPLATE.replace("{{LETTER_CONTENT}}", cover_text))

    def test_stage_copies_exact_templates_and_manifest(self):
        outcome = self.stage()
        self.assertEqual(outcome["action"], "created")
        self.assertEqual((self.position / "resume.tex").read_text(), RESUME_TEMPLATE)
        self.assertEqual((self.position / "cover_letter.tex").read_text(), LETTER_TEMPLATE)
        manifest = json.loads((self.position / ".latex_source_manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(len(manifest["sources"]["resume"]["sha256"]), 64)
        self.assertFalse((self.root / "resume.tex").exists())

    def test_stage_preserves_existing_approved_edits(self):
        self.stage()
        self.populate()
        outcome = self.stage()
        self.assertEqual(outcome["action"], "preserved")
        self.assertIn("Built Python services", (self.position / "resume.tex").read_text())

    def test_never_overwrite_legacy_or_partial_draft(self):
        (self.position / "resume.tex").write_text("legacy draft")
        with self.assertRaisesRegex(LatexWorkflowError, "without a manifest"):
            self.stage()
        self.assertEqual((self.position / "resume.tex").read_text(), "legacy draft")
        self.assertFalse((self.position / ".latex_source_manifest.json").exists())

    def test_missing_templates_fail_without_writing(self):
        (self.root / "templates/cover_letter_template.tex").unlink()
        with self.assertRaises(LatexWorkflowError):
            self.stage()
        self.assertFalse((self.position / "resume.tex").exists())

    def test_missing_description_blocks_stage(self):
        (self.position / "job_description.md").unlink()
        with self.assertRaisesRegex(LatexWorkflowError, "job_description.md missing"):
            self.stage()
        self.assertFalse((self.position / "resume.tex").exists())

    def test_missing_template_manifest_blocks_compilation(self):
        self.stage()
        (self.position / ".latex_source_manifest.json").unlink()
        with self.assertRaises(LatexWorkflowError):
            self.validate()
        self.assertFalse((self.position / "build").exists())

    def test_changed_template_requires_reapproval(self):
        self.stage()
        self.populate()
        with (self.root / "templates/resume_template.tex").open("a") as stream:
            stream.write("% changed original\n")
        with self.assertRaisesRegex(LatexWorkflowError, "does not match approved staging"):
            self.validate()

    def test_preamble_layout_is_preserved(self):
        self.stage()
        self.populate()
        draft = self.position / "resume.tex"
        draft.write_text(draft.read_text().replace(r"\usepackage[margin=1in]{geometry}", ""))
        with self.assertRaisesRegex(LatexWorkflowError, "preamble changed"):
            self.validate()

    def test_unresolved_markers_block_without_approving_compile(self):
        self.stage()
        self.populate()
        (self.position / "cover_letter.tex").write_text(LETTER_TEMPLATE)
        with patch("latex_workflow.subprocess.run") as runner:
            with self.assertRaisesRegex(LatexWorkflowError, "unresolved template placeholder"):
                self.validate()
            runner.assert_not_called()
        self.assertFalse((self.position / "build").exists())

    def test_dangerous_latex_rejected_before_compiler(self):
        self.stage()
        self.populate()
        path = self.position / "resume.tex"
        path.write_text(path.read_text().replace(r"\end{document}", r"\immediate\write18{touch /tmp/unsafe}\end{document}"))
        with patch("latex_workflow.subprocess.run") as runner:
            with self.assertRaisesRegex(LatexWorkflowError, "dangerous TeX"):
                self.validate()
            runner.assert_not_called()

    def test_template_symlink_refused(self):
        original = self.root / "templates/resume_template.tex"
        original.unlink()
        original.symlink_to(self.root / "templates/cover_letter_template.tex")
        with self.assertRaisesRegex(LatexWorkflowError, "symlink"):
            self.stage()

    def test_draft_symlink_refused(self):
        self.stage()
        target = self.position / "resume.tex"
        target.unlink()
        target.symlink_to(self.root / "templates/resume_template.tex")
        with self.assertRaisesRegex(LatexWorkflowError, "symlink"):
            self.validate()

    def test_compiler_absence_reports_failure_not_success(self):
        self.stage()
        self.populate()
        with patch("latex_workflow.shutil.which", return_value=None):
            outcome = self.validate()
        self.assertEqual(outcome["status"], "validation_failed")
        self.assertIn("latexmk executable missing", outcome["documents"]["resume"]["errors"][0])
        self.assertFalse((self.position / "build/resume.pdf").exists())
        self.assertTrue((self.position / "build/validation.json").exists())

    def test_compiler_is_invoked_with_fixed_flags_without_shell(self):
        from types import SimpleNamespace
        self.stage()
        self.populate()
        with patch("latex_workflow.shutil.which", return_value="/usr/bin/latexmk"), \
             patch("latex_workflow.subprocess.run", return_value=SimpleNamespace(returncode=1)) as runner:
            result = self.validate()
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(runner.call_count, 2)
        args, kwargs = runner.call_args
        self.assertIn("-no-shell-escape", args[0])
        self.assertIn("-norc", args[0])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"]["openout_any"], "p")
        self.assertEqual(kwargs["env"]["openin_any"], "p")
        self.assertNotEqual(kwargs["cwd"], self.position)
        self.assertIn("resume-agent-latex-", str(kwargs["cwd"]))

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "requires latexmk and pdflatex")
    def test_real_compile_inspects_both_pdfs_and_text(self):
        self.stage()
        self.populate()
        outcome = self.validate()
        self.assertEqual(outcome["status"], "ok", str(outcome))
        self.assertEqual(outcome["documents"]["cover_letter"]["page_count"], 1)
        self.assertTrue((self.position / "build/resume.pdf").read_bytes().startswith(b"%PDF-"))
        self.assertTrue((self.position / "build/cover_letter_page1.png").read_bytes().startswith(b"\x89PNG"))
        self.assertIn("Python APIs", (self.position / "build/cover_letter.txt").read_text())
        self.assertIn("visual_review_required", outcome)

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "requires latexmk and pdflatex")
    def test_failed_compilation_exposes_logs_and_invalidates_stale_pdf(self):
        self.stage()
        self.populate()
        self.assertEqual(self.validate()["status"], "ok")
        resume = self.position / "resume.tex"
        resume.write_text(resume.read_text().replace("Built Python services", r"\notarealmacro{Built Python services}"))
        result = self.validate()
        self.assertEqual(result["status"], "validation_failed")
        self.assertFalse(result["documents"]["resume"]["ok"])
        self.assertFalse((self.position / "build/resume.pdf").exists())
        self.assertTrue((self.position / "build/resume.log").exists())
        self.assertTrue((self.position / "build/cover_letter.pdf").exists())

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "requires latexmk and pdflatex")
    def test_multi_page_cover_letter_fails_and_publishes_no_pdf(self):
        self.stage()
        letter = ("I am qualified for this position and ready to contribute meaningful work. " * 7)
        self.populate(cover_text=letter + "\n\\newpage\n" + letter)
        result = self.validate()
        self.assertEqual(result["status"], "validation_failed")
        self.assertIn("fit one page", " ".join(result["documents"]["cover_letter"]["errors"]))
        self.assertFalse((self.position / "build/cover_letter.pdf").exists())
        self.assertTrue((self.position / "build/resume.pdf").exists())

    def test_build_symlink_is_denied(self):
        self.stage()
        self.populate()
        build = self.position / "build"
        build.symlink_to(self.root / "templates", target_is_directory=True)
        with self.assertRaisesRegex(LatexWorkflowError, "symlink"):
            self.validate()
        self.assertFalse((self.root / "templates/validation.json").exists())


if __name__ == "__main__":
    unittest.main()

# Historical-source behavior is tested in its own fixture to avoid changing
# the older default-template regression coverage.
class HistoricalSourceTests(unittest.TestCase):
    def setUp(self):
        LatexWorkflowTests.setUp(self)
        (self.root / "candidate/base-documents").mkdir(parents=True)
        self.resume_original = RESUME_TEMPLATE.replace(
            "{{RESUME_CONTENT}}",
            "Maintained Python backend systems and deployed local ML prototypes. " * 6,
        )
        self.cover_original = LETTER_TEMPLATE.replace(
            "{{LETTER_CONTENT}}",
            "I have developed Python services and I am interested in this position. " * 8,
        )
        (self.root / "candidate/base-documents/backend.tex").write_text(self.resume_original)
        (self.root / "candidate/base-documents/letter.tex").write_text(self.cover_original)

    def stage(self):
        return prepare_latex_documents(
            self.root, **self.params, base_resume="backend.tex", base_cover="letter.tex"
        )

    def validate(self):
        return validate_latex_documents(self.root, **self.params)

    def test_both_selected_sources_are_copied_unchanged(self):
        response = self.stage()
        self.assertEqual(response["sources"]["resume"]["source_type"], "historical_base")
        self.assertEqual(response["sources"]["cover_letter"]["filename"], "letter.tex")
        self.assertEqual((self.position / "resume.tex").read_text(), self.resume_original)
        self.assertEqual((self.position / "cover_letter.tex").read_text(), self.cover_original)
        self.assertEqual((self.root / "candidate/base-documents/backend.tex").read_text(), self.resume_original)

    def test_resume_can_be_validated_without_edits(self):
        self.stage()
        self.assertTrue(self.validate()["documents"]["resume"]["unchanged_from_source"])

    def test_mixed_base_and_default(self):
        result = prepare_latex_documents(self.root, **self.params, base_resume="backend.tex")
        self.assertEqual(result["sources"]["resume"]["source_type"], "historical_base")
        self.assertEqual(result["sources"]["cover_letter"]["source_type"], "default_template")
        self.assertEqual((self.position / "cover_letter.tex").read_text(), LETTER_TEMPLATE)
        self.assertEqual((self.position / "resume.tex").read_text(), self.resume_original)

    def test_selected_filename_cannot_change_after_staging(self):
        self.stage()
        before = (self.position / "resume.tex").read_bytes()
        with self.assertRaisesRegex(LatexWorkflowError, "does not match approved staging"):
            prepare_latex_documents(self.root, **self.params, base_resume="backend.tex", base_cover=None)
        self.assertEqual((self.position / "resume.tex").read_bytes(), before)

    def test_missing_explicit_base_never_falls_back(self):
        with self.assertRaisesRegex(LatexWorkflowError, "Missing or unreadable file"):
            prepare_latex_documents(self.root, **self.params, base_resume="does-not-exist.tex")
        self.assertFalse((self.position / "resume.tex").exists())

    def test_reject_base_symlink(self):
        (self.root / "candidate/base-documents/linked.tex").symlink_to(
            self.root / "candidate/base-documents/backend.tex"
        )
        with self.assertRaisesRegex(LatexWorkflowError, "symlink"):
            prepare_latex_documents(self.root, **self.params, base_resume="linked.tex")
        self.assertFalse((self.position / ".latex_source_manifest.json").exists())

    def test_reject_traversal_source(self):
        with self.assertRaisesRegex(LatexWorkflowError, "single .tex filename"):
            prepare_latex_documents(self.root, **self.params, base_resume="../escape.tex")
        self.assertFalse((self.position / "resume.tex").exists())

    def test_manifest_tamper_blocked(self):
        self.stage()
        manifest = self.position / ".latex_source_manifest.json"
        raw = json.loads(manifest.read_text())
        raw["sources"]["resume"]["virtual_path"] = "/workspace/elsewhere/backend.tex"
        manifest.write_text(json.dumps(raw))
        with self.assertRaisesRegex(LatexWorkflowError, "does not match approved staging"):
            self.validate()

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "needs latexmk/pdflatex")
    def test_unmodified_historical_resume_compiles(self):
        self.stage()
        report = self.validate()
        self.assertEqual(report["status"], "ok", report)
        self.assertTrue(report["documents"]["resume"]["unchanged_from_source"])
        self.assertTrue(report["documents"]["cover_letter"]["unchanged_from_source"])
        self.assertEqual(report["documents"]["cover_letter"]["page_count"], 1)

