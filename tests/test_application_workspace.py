"""Offline tests for deterministic, lossless application initialization."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application_workspace import (
    ApplicationWorkspaceError,
    canonical_paths,
    initialize_application,
)


class ApplicationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "workspace"
        self.root.mkdir()
        (self.root / "candidate").mkdir()
        (self.root / "candidate" / "profile.md").write_text(
            "protected profile", encoding="utf-8"
        )
        (self.root / "templates").mkdir()
        (self.root / "templates" / "resume_template.tex").write_text(
            "template", encoding="utf-8"
        )
        self.inputs = dict(
            company_name="ACME, Inc.",
            position_title="Senior AI Engineer",
            application_date="2026-11-17",
            job_description_text="Exact posting: experience with Python & LLMs.\nLine two.\n",
            job_description_source="Candidate-supplied job posting",
            job_description_url="https://example.org/careers/ai-engineer",
        )

    def init(self, **updates):
        return initialize_application(self.root, **(self.inputs | updates))

    def test_paths_and_required_source_records(self):
        result = self.init()
        paths = result["paths"]
        self.assertEqual(
            paths["position_directory"],
            "/workspace/applications/acme_inc/2026-11-17-senior_ai_engineer",
        )
        self.assertEqual(paths["resume"], paths["position_directory"] + "/resume.tex")
        self.assertEqual(
            paths["cover_letter"], paths["position_directory"] + "/cover_letter.tex"
        )
        self.assertEqual(len(result["created"]), 4)
        job = (
            self.root
            / "applications/acme_inc/2026-11-17-senior_ai_engineer/job_description.md"
        )
        content = job.read_text(encoding="utf-8")
        self.assertIn("Candidate-supplied job posting", content)
        self.assertIn("https://example.org/careers/ai-engineer", content)
        self.assertIn(
            "Exact posting: experience with Python & LLMs.\nLine two.\n", content
        )
        for file in [
            "applications/acme_inc/company_inf.md",
            "applications/acme_inc/additional_inf.md",
            "applications/acme_inc/2026-11-17-senior_ai_engineer/additional_inf.md",
        ]:
            self.assertIn(
                "Status: not_provided", (self.root / file).read_text(encoding="utf-8")
            )
        self.assertFalse((self.root / "resume.tex").exists())
        self.assertFalse((self.root / "applications/acme_inc/resume.tex").exists())
        self.assertEqual(
            (self.root / "templates/resume_template.tex").read_text(encoding="utf-8"),
            "template",
        )
        self.assertEqual(
            (self.root / "candidate/profile.md").read_text(encoding="utf-8"),
            "protected profile",
        )

    def test_same_inputs_are_idempotent(self):
        self.init(
            company_information_text="Facts supplied",
            company_information_source="Employer website",
        )
        second = self.init(
            company_information_text="Facts supplied",
            company_information_source="Employer website",
        )
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["preserved_existing"]), 4)

    def test_preexisting_company_records_preserved_across_positions(self):
        self.init(
            company_information_text="Genuine shared information",
            company_information_source="Supplied notes",
        )
        second = self.init(
            position_title="Backend Engineer",
            company_information_text="",
            company_information_source="",
        )
        self.assertEqual(len(second["created"]), 2)
        self.assertEqual(len(second["preserved_existing"]), 2)
        self.assertIn(
            "Genuine shared information",
            (self.root / "applications/acme_inc/company_inf.md").read_text(
                encoding="utf-8"
            ),
        )

    def test_conflicts_abort_before_any_files_were_written(self):
        self.init()
        other = dict(self.inputs, job_description_text="New contradictory description")
        with self.assertRaisesRegex(ApplicationWorkspaceError, "Conflicting existing"):
            initialize_application(self.root, **other)
        self.assertIn(
            self.inputs["job_description_text"].strip(),
            (
                self.root
                / "applications/acme_inc/2026-11-17-senior_ai_engineer/job_description.md"
            ).read_text(encoding="utf-8"),
        )
        self.assertFalse((self.root / "resume.tex").exists())

    def test_conflicting_company_record_does_not_create_new_role(self):
        self.init(
            company_information_text="Fact A", company_information_source="Source A"
        )
        with self.assertRaisesRegex(ApplicationWorkspaceError, "Conflicting existing"):
            self.init(
                position_title="Different Role",
                company_information_text="Fact B",
                company_information_source="Source B",
            )
        self.assertFalse(
            (self.root / "applications/acme_inc/2026-11-17-different_role").exists()
        )

    def test_missing_job_description_fails_closed(self):
        with self.assertRaisesRegex(
            ApplicationWorkspaceError, "Nonempty original job_description_text"
        ):
            self.init(job_description_text=" \n")
        self.assertFalse((self.root / "applications").exists())

    def test_invalid_names_dates_and_source_metadata(self):
        for updates in [
            {"company_name": "../oops"},
            {"company_name": "ACME\\misc"},
            {"position_title": ""},
            {"application_date": "2026-02-30"},
            {"application_date": "11/17/2026"},
            {"job_description_source": "\npretend"},
            {"job_description_url": "file:///etc/passwd"},
            {"company_information_source": "source without content"},
        ]:
            with self.subTest(**updates), self.assertRaises(ApplicationWorkspaceError):
                self.init(**updates)
        self.assertFalse((self.root / "applications").exists())

    def test_symlinked_company_directory_rejected(self):
        app = self.root / "applications"
        app.mkdir()
        dest = self.root / "candidate"
        (app / "acme_inc").symlink_to(dest, target_is_directory=True)
        with self.assertRaisesRegex(ApplicationWorkspaceError, "symlink"):
            self.init()
        self.assertEqual(
            (dest / "profile.md").read_text(encoding="utf-8"), "protected profile"
        )

    def test_paths_never_use_outside_workspace(self):
        paths = canonical_paths("A Company", "AI Platform", "2026-11-17")
        self.assertTrue(paths.job_description.startswith("/workspace/applications/"))
        with self.assertRaises(ApplicationWorkspaceError):
            canonical_paths("A Company", "AI/Platform", "2026-11-17")


if __name__ == "__main__":
    unittest.main()
