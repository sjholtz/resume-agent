"""Patch C claim provenance, quantitative-claim and freshness regressions."""

from __future__ import annotations

import hashlib
import json
import textwrap
import shutil
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application_workspace import ApplicationWorkspaceError, initialize_application
from latex_workflow import prepare_latex_documents
from claim_audit import ClaimAuditError, audit_application_claims, check_claim_audit

RESUME_SOURCE = r"""\documentclass{article}
\begin{document}
This is the candidate's resume document with practical technical accomplishments.
\end{document}
"""
COVER_SOURCE = r"""\documentclass{article}
\begin{document}
I am applying to the role and have hands-on experience in backend engineering.
\end{document}
"""
RESUME_TEXT = (
    "Implemented local Ollama model prototypes for personal learning projects. "
    "Collaborated with a team to maintain backend Python services and APIs. "
    "The work concerned internal experimentation, not a production customer deployment."
)
COVER_TEXT = (
    "I developed local LLM experiments and have worked with backend services. "
    "I am interested in the role because it involves reliable APIs and software design. "
    "My experience includes Python programming and technical collaboration."
)
METRIC_TEXT = (
    "Integrated Whisper for lecture captioning and improved accessibility compliance by 40%+. "
    "I maintained Python API services and contributed to application development."
)


def pdf_bytes(text):
    pdf = pymupdf.open()
    page = pdf.new_page()
    for i, chunk in enumerate(textwrap.wrap(text, width=74, break_long_words=False)):
        page.insert_text((50, 50 + 22 * i), chunk, fontsize=10)
    raw = pdf.tobytes()
    pdf.close()
    return raw


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ClaimAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.params = dict(company_name="Acme Inc", position_title="AI Developer", application_date="2026-11-19")
        (self.root / "templates").mkdir()
        (self.root / "templates/resume_template.tex").write_text(RESUME_SOURCE)
        (self.root / "templates/cover_letter_template.tex").write_text(COVER_SOURCE)
        initialize_application(self.root, **self.params,
                               job_description_text="Develop Python and AI software and maintain reliable services.",
                               job_description_source="Candidate provided description")
        self.position = self.root / "applications/acme_inc/2026-11-19-ai_developer"
        prepare_latex_documents(self.root, **self.params)
        (self.root / "candidate/base-documents").mkdir(parents=True)
        (self.root / "candidate/profile.md").write_text(
            "Candidate built local Ollama model prototypes for personal learning projects.\n"
            "Candidate collaborated on backend Python services and APIs.\n"
            "Candidate experimented with LLM apps and worked with backend services.\n"
            "Candidate integrated Whisper for lecture captioning; no verified percentage.\n"
        )
        (self.root / "candidate/base-documents/old_resume.tex").write_text(
            "Historical draft: implemented local Ollama model prototypes for learning projects.\n"
            "Historical draft: Integrated Whisper for lecture captioning and improved accessibility compliance by 40%+.\n"
        )
        self.record = "/workspace/candidate/profile.md"
        self.old = "/workspace/candidate/base-documents/old_resume.tex"
        self.build_pdf("resume", RESUME_TEXT)
        self.build_pdf("cover_letter", COVER_TEXT)
        self.save_validation()

    def build_pdf(self, kind, text):
        directory = self.position / "build"
        directory.mkdir(exist_ok=True)
        (directory / f"{kind}.pdf").write_bytes(pdf_bytes(text))

    def save_validation(self):
        self.validation = {"status": "ok", "position_directory": "/workspace/applications/acme_inc/2026-11-19-ai_developer", "documents": {}}
        for k in ("resume", "cover_letter"):
            self.validation["documents"][k] = {
                "ok": True,
                "source_sha256": sha((self.position / f"{k}.tex").read_bytes()),
                "pdf_sha256": sha((self.position / f"build/{k}.pdf").read_bytes()),
                "page_count": 1,
            }
        (self.position / "build/validation.json").write_text(json.dumps(self.validation))

    def claim(self, kind, quote, evidence=None, *, ident="CLM-0001"):
        return {"id": ident, "document": kind, "quote": quote, "evidence": evidence or []}

    def source(self, snippet, path=None):
        return {"path": path or self.record, "quote": snippet}

    def request(self, extra=None, fups=None):
        a = self.claim("resume", "Implemented local Ollama model prototypes for personal learning projects.",
                       [self.source("Candidate built local Ollama model prototypes for personal learning projects.")])
        b = self.claim("cover_letter", "I developed local LLM experiments and have worked with backend services.",
                       [self.source("Candidate experimented with LLM apps and worked with backend services.")], ident="CLM-0002")
        return json.dumps({"claims": [a,b] + (extra or []), "fups": fups or []})

    def audit(self, payload=None, **kw):
        return audit_application_claims(self.root, **self.params, claims_json=payload or self.request(), **kw)

    def test_record_anchored_claims_require_semantic_review_not_auto_clearance(self):
        report = self.audit()
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["claims"][0]["status"], "record_backed")
        self.assertIn("candidate_factual_signoff", report["remaining_gates"])
        self.assertTrue((self.position / "claim_audit.md").exists())
        self.assertTrue((self.position / "build/claim_audit.json").exists())
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "review_required")

    def test_unresolved_fup_excluded_is_not_a_blocker(self):
        report = self.audit(self.request(fups=[{"id":"FUP-004", "note":"Impact percentage excluded pending evidence"}]))
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["fups"][0]["id"], "FUP-004")
        self.assertFalse((self.root / "candidate/FUP-004.md").exists())

    def test_unsupported_active_claim_blocks(self):
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"] = []
        report = self.audit(json.dumps(raw))
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["claims"][0]["status"], "unsupported")

    def test_40_percent_claim_needs_profile_metric_not_historical_only(self):
        self.build_pdf("resume", METRIC_TEXT)
        self.save_validation()
        raw = json.loads(self.request())
        raw["claims"][0] = self.claim(
            "resume", "Integrated Whisper for lecture captioning and improved accessibility compliance by 40%+.",
            [self.source("Historical draft: Integrated Whisper for lecture captioning and improved accessibility compliance by 40%+.", self.old)])
        report = self.audit(json.dumps(raw))
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["claims"][0]["status"], "candidate_reported")
        self.assertIn("Quantified result", " ".join(report["claims"][0]["issues"]))

    def test_profile_metric_exactly_supported_is_record_backed_but_not_truth_verified(self):
        self.build_pdf("resume", METRIC_TEXT)
        self.save_validation()
        with (self.root / "candidate/profile.md").open("a") as handle:
            handle.write("Candidate reports improving accessibility compliance by 40%+.\n")
        raw = json.loads(self.request())
        raw["claims"][0] = self.claim(
            "resume", "Integrated Whisper for lecture captioning and improved accessibility compliance by 40%+.",
            [self.source("Candidate reports improving accessibility compliance by 40%+.")])
        report = self.audit(json.dumps(raw))
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["claims"][0]["status"], "record_backed")

    def test_unlisted_metric_fails_numeric_coverage(self):
        self.build_pdf("resume", RESUME_TEXT + " Improved reliability by 40%+.")
        self.save_validation()
        report = self.audit()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["uncovered_quantitative"]["resume"], ["40%"])

    def test_historical_only_nonquantified_claim_requires_confirmation(self):
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"] = [self.source(
            "Historical draft: implemented local Ollama model prototypes for learning projects.", self.old)]
        report = self.audit(json.dumps(raw))
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["claims"][0]["status"], "candidate_reported")
        self.assertIn("confirmation required", " ".join(report["claims"][0]["issues"]))

    def test_job_description_not_allowed_as_candidate_evidence(self):
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"][0]["path"] = self.validation["position_directory"] + "/job_description.md"
        with self.assertRaisesRegex(ClaimAuditError, "candidate profile"):
            self.audit(json.dumps(raw))

    def test_evidence_must_exactly_match_authoritative_file(self):
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"][0]["quote"] = "Candidate improved accessibility compliance by 40%+"
        with self.assertRaisesRegex(ClaimAuditError, "quoted evidence was not found"):
            self.audit(json.dumps(raw))

    def test_pdf_claim_quote_not_found_rejected(self):
        raw = json.loads(self.request())
        raw["claims"][0]["quote"] = "I invented a product with 90% conversion gain."
        with self.assertRaisesRegex(ClaimAuditError, "exact claim quote not found"):
            self.audit(json.dumps(raw))

    def test_duplicate_or_invalid_claim_ids_fail(self):
        raw = json.loads(self.request())
        raw["claims"][1]["id"] = "CLM-0001"
        with self.assertRaisesRegex(ClaimAuditError, "unique"):
            self.audit(json.dumps(raw))
        raw["claims"][1]["id"] = "PROJECT-001"
        with self.assertRaisesRegex(ClaimAuditError, "unique"):
            self.audit(json.dumps(raw))

    def test_no_cover_letter_claim_fails_closed(self):
        raw = json.loads(self.request())
        raw["claims"][1]["document"] = "resume"
        raw["claims"][1]["quote"] = "Collaborated with a team to maintain backend Python services and APIs."
        with self.assertRaisesRegex(ClaimAuditError, "EACH"):
            self.audit(json.dumps(raw))

    def test_audit_requires_validated_pdf_and_source_hash(self):
        (self.position / "resume.tex").write_text("edited after validation")
        with self.assertRaisesRegex(ClaimAuditError, "draft changed"):
            self.audit()
        self.assertFalse((self.position / "build/claim_audit.json").exists())

    def test_audit_requires_current_pdf_hash(self):
        (self.position / "build/resume.pdf").write_bytes(b"tampered")
        with self.assertRaisesRegex(ClaimAuditError, "PDF changed"):
            self.audit()

    def test_missing_validation_fail_closed(self):
        (self.position / "build/validation.json").unlink()
        with self.assertRaises(ClaimAuditError):
            self.audit()

    def test_symlinked_candidate_evidence_rejected(self):
        alias = self.root / "candidate/profile_alias.md"
        alias.symlink_to(self.root / "candidate/profile.md")
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"][0]["path"] = "/workspace/candidate/profile_alias.md"
        with self.assertRaisesRegex(ClaimAuditError, "symlink"):
            self.audit(json.dumps(raw))

    def test_invalid_evidence_paths_rejected(self):
        for path in ("/workspace/other.md", "/workspace/candidate/../applications/acme.md",
                     "candidate/profile.md", "/workspace/candidate/../../etc/passwd"):
            with self.subTest(path=path):
                raw = json.loads(self.request())
                raw["claims"][0]["evidence"][0]["path"] = path
                with self.assertRaises(ClaimAuditError):
                    self.audit(json.dumps(raw))

    def test_changes_to_profile_make_audit_stale(self):
        self.audit()
        with (self.root / "candidate/profile.md").open("a") as handle:
            handle.write("Addendum confirming a new detail.\n")
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "stale")

    def test_changes_to_resume_make_audit_missing_or_invalid(self):
        self.audit()
        with (self.position / "resume.tex").open("a") as handle:
            handle.write("% changed\n")
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "missing_or_invalid")

    def test_missing_or_tampered_report_is_not_accepted(self):
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "missing_or_invalid")
        self.audit()
        data = json.loads((self.position / "build/claim_audit.json").read_text())
        data["document_hashes"]["resume"]["pdf_sha256"] = "x"
        (self.position / "build/claim_audit.json").write_text(json.dumps(data))
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "stale")

    def test_custom_candidate_profile_root_supported(self):
        (self.root / "profiles/primary").mkdir(parents=True)
        (self.root / "profiles/primary/history.md").write_text("Candidate built local Ollama model prototypes for personal learning projects.\n")
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"][0]["path"] = "/workspace/profiles/primary/history.md"
        raw["claims"][1]["evidence"][0]["path"] = "/workspace/profiles/primary/history.md"
        raw["claims"][1]["evidence"][0]["quote"] = "Candidate built local Ollama model prototypes for personal learning projects."
        report = self.audit(json.dumps(raw), candidate_profile="/workspace/profiles/primary")
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(check_claim_audit(self.root, **self.params,
                                          candidate_profile="/workspace/profiles/primary")["status"], "review_required")

    def test_claim_audit_output_symlink_rejected(self):
        self.audit()
        output = self.position / "claim_audit.md"
        output.unlink()
        output.symlink_to(self.root / "candidate/profile.md")
        with self.assertRaisesRegex(ApplicationWorkspaceError, "symlink"):
            self.audit()

    def test_changed_original_template_invalidates_validation(self):
        self.audit()
        (self.root / "templates/resume_template.tex").write_text(RESUME_SOURCE + "\n% edited template\n")
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "missing_or_invalid")
        with self.assertRaises(ApplicationWorkspaceError):
            self.audit()

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"),
                         "requires real LaTeX toolchain")
    def test_real_pdf_compilation_to_claim_audit(self):
        from latex_workflow import validate_latex_documents
        for kind, content in (("resume", RESUME_TEXT), ("cover_letter", COVER_TEXT)):
            original = (self.position / f"{kind}.tex").read_text()
            target = original.replace("\\end{document}", content + "\n\\end{document}")
            (self.position / f"{kind}.tex").write_text(target)
        result = validate_latex_documents(self.root, **self.params)
        self.assertEqual(result["status"], "ok", str(result))
        # Source lines may be wrapped by TeX; choose short, verbatim spans.
        claims = [
            self.claim("resume", "Implemented local Ollama model prototypes for personal learning projects.",
                       [self.source("Candidate built local Ollama model prototypes for personal learning projects.")]),
            self.claim("cover_letter", "I developed local LLM experiments and have worked with backend services.",
                       [self.source("Candidate experimented with LLM apps and worked with backend services.")],
                       ident="CLM-0002"),
        ]
        audit = self.audit(json.dumps({"claims": claims}))
        self.assertEqual(audit["status"], "review_required")
        self.assertEqual(check_claim_audit(self.root, **self.params)["status"], "review_required")

    def test_disallowed_extension_rejected(self):
        (self.root / "candidate/raw.pdf").write_bytes(b"%PDF-1.7")
        raw = json.loads(self.request())
        raw["claims"][0]["evidence"][0]["path"] = "/workspace/candidate/raw.pdf"
        with self.assertRaisesRegex(ClaimAuditError, "text-based"):
            self.audit(json.dumps(raw))


if __name__ == "__main__":
    unittest.main()
