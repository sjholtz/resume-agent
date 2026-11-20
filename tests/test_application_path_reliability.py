"""URL-first and canonical directory regression tests; all offline."""
from __future__ import annotations

import contextlib
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application_workspace import (
    ApplicationWorkspaceError, RetrievedJobDescription, initialize_application,
)
from application_write_guard import ApplicationWriteError, require_initialized_document_path
from cli import _prompt_approvals


class TestApplicationPathReliability(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.called = []
        self.params = dict(company_name="Company-A", position_title="Senior Software Engineer - Applied AI",
                           application_date="2026-11-20", job_description_url="https://jobs.example.org/ai")
        self.position = "applications/company_a/2026-11-20-senior_software_engineer_applied_ai"
        self.target = "/workspace/" + self.position + "/qualification_matrix.md"

    def fetch(self, url):
        self.called.append(url)
        return RetrievedJobDescription(
            text="Exact full posting and duties. " * 100,
            final_url="https://jobs.example.org/ai?view=full",
            retrieved_at="2026-11-20T20:00:00+00:00", extraction="main_content",
        )

    def init(self, **kwargs):
        return initialize_application(self.root, **(self.params | {"_fetch_job_description": self.fetch} | kwargs))

    def approval(self, path):
        request = {
            "action_requests": [{"name": "write_file", "args": {"file_path": path, "content": "draft"}}],
            "review_configs": [{"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}],
        }
        questions = []
        with contextlib.redirect_stdout(StringIO()):
            response = _prompt_approvals(request, profile="/workspace/candidate",
                                         workspace_root=self.root,
                                         input_fn=lambda prompt: questions.append(prompt) or "y")
        return response["decisions"][0]["type"], questions

    def test_url_only_saves_complete_snapshot_and_provenance(self):
        result = self.init()
        self.assertEqual(result["job_description_input"], "url")
        self.assertEqual(result["paths"]["position_directory"], "/workspace/" + self.position)
        self.assertEqual(self.called, [self.params["job_description_url"]])
        saved = (self.root / self.position / "job_description.md").read_text()
        self.assertIn("Exact full posting and duties.", saved)
        self.assertGreater(len(saved), 2000)
        self.assertIn("- Original URL: https://jobs.example.org/ai", saved)
        self.assertIn("- Resolved URL: https://jobs.example.org/ai?view=full", saved)
        self.assertIn("- Extraction method: main_content", saved)
        self.assertFalse((self.root / "applications/company-a").exists())
        self.assertTrue((self.root / "applications/company_a/company_inf.md").exists())

    def test_retry_reuses_snapshot_not_live_page(self):
        self.init()
        snapshot = (self.root / self.position / "job_description.md").read_bytes()
        ret = self.init(_fetch_job_description=lambda url: self.fail("unexpected URL fetch"))
        self.assertEqual(ret["created"], [])
        self.assertEqual(ret["job_description_input"], "existing_snapshot")
        self.assertEqual((self.root / self.position / "job_description.md").read_bytes(), snapshot)

    def test_failed_fetch_leaves_no_partial_application(self):
        with self.assertRaisesRegex(ApplicationWorkspaceError, "unavailable"):
            self.init(_fetch_job_description=lambda url: (_ for _ in ()).throw(
                ApplicationWorkspaceError("unavailable")))
        self.assertFalse((self.root / "applications").exists())

    def test_different_url_conflicts_without_refetch(self):
        self.init()
        with self.assertRaisesRegex(ApplicationWorkspaceError, "manual review"):
            self.init(job_description_url="https://jobs.example.org/other")
        self.assertEqual(len(self.called), 1)

    def test_empty_text_and_missing_url_rejected(self):
        with self.assertRaisesRegex(ApplicationWorkspaceError, "job_description_text or job_description_url"):
            self.init(job_description_text=" ", job_description_url="", job_description_source="URL label")
        self.assertFalse((self.root / "applications").exists())

    def test_pasted_text_remains_supported(self):
        result = self.init(job_description_text="Original candidate supplied text",
                           job_description_source="Candidate-supplied posting")
        self.assertEqual(result["job_description_input"], "text")
        self.assertEqual(self.called, [])

    def test_guard_rejects_uninitialized_work(self):
        with self.assertRaises(ApplicationWriteError):
            require_initialized_document_path(self.root, self.target)
        self.assertEqual(self.approval(self.target)[0], "reject")

    def test_guard_approves_only_exact_canonical_initialized_folder(self):
        self.init()
        self.assertEqual(require_initialized_document_path(self.root, self.target),
                         "/workspace/" + self.position)
        self.assertEqual(self.approval(self.target)[0], "approve")
        invalid = [
            self.target.replace("company_a", "company-a"),
            self.target.replace("company_a", "Company_A"),
            self.target.replace("senior_software", "Senior_Software"),
            self.target.replace("2026-11-20", "2024-12-19"),
            self.target.replace("qualification_matrix.md", "job_description.md"),
            self.target.replace("qualification_matrix.md", "additional_inf.md"),
            self.target.replace("qualification_matrix.md", "build/validation.json"),
            self.target.replace("qualification_matrix.md", ".latex_source_manifest.json"),
            self.target.replace("qualification_matrix.md", "../qualification_matrix.md"),
            "/workspace/applications/company_a/company_inf.md",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ApplicationWriteError):
                    require_initialized_document_path(self.root, value)
                status, prompts = self.approval(value)
                self.assertEqual(status, "reject")
                self.assertEqual(prompts, [])

    def test_symlinked_draft_fails_closed(self):
        self.init()
        target = self.root / self.position / "qualification_matrix.md"
        protected = self.root / "candidate" / "original.md"
        protected.parent.mkdir(exist_ok=True)
        protected.write_text("original")
        target.symlink_to(protected)
        with self.assertRaisesRegex(ApplicationWriteError, "symlink"):
            require_initialized_document_path(self.root, self.target)


if __name__ == "__main__":
    unittest.main()
