"""Regression: never commit new placeholders or stale Last Updated metadata.

Run: uv run python3 -m unittest discover -s tests -p 'test_profile_content_quality.py' -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils
from profile_content_quality import local_date_iso, profile_content_issues
from profile_receipts import ProfileReceiptRegistry
from profile_review import review_profile_action


class ContentQualityTests(unittest.TestCase):
    def setUp(self):
        self.today = local_date_iso()

    def test_new_bracketed_current_date_rejected(self):
        self.assertTrue(
            profile_content_issues(
                None, "**Last Updated:** [Current Date]\n", today=self.today
            )
        )

    def test_date_must_be_actual_local_day(self):
        self.assertFalse(
            profile_content_issues(
                None, "**Last Updated:** " + self.today + "  \n", today=self.today
            )
        )
        self.assertTrue(
            profile_content_issues(None, "Last Updated: 2005-01-01\n", today=self.today)
        )

    def test_other_placeholder_forms(self):
        for s in (
            "[insert project]",
            "[TBD]",
            "[TODO]",
            "[specific framework if known]",
            "[YYYY-MM-DD]",
            "[Your Name]",
            "[Name]",
            "[Project]",
            "[Details]",
        ):
            with self.subTest(s=s):
                self.assertTrue(
                    profile_content_issues(None, f"Evidence: {s}\n", today=self.today)
                )

    def test_nonplaceholder_markdown_and_stable_id_not_rejected(self):
        s = "- [x] verified\n- [ ] follow-up\n[Source](https://example.com/)\nRef [FUP-0001] [1]\n"
        self.assertEqual(profile_content_issues(None, s, today=self.today), [])

    def test_existing_placeholder_does_not_block_unrelated_update(self):
        before = "Last Updated: [Current Date]\nWork: Python APIs\n"
        after = before + "More: unit tests\n"
        self.assertEqual(profile_content_issues(before, after, today=self.today), [])

    def test_repair_preexisting_placeholder(self):
        before = "Last Updated: [Current Date]\n"
        after = "Last Updated: " + self.today + "\n"
        self.assertEqual(profile_content_issues(before, after, today=self.today), [])

    def test_invalid_replacement_refused(self):
        before = "Last Updated: [Current Date]\n"
        after = "Last Updated: [YYYY-MM-DD]\n"
        self.assertTrue(profile_content_issues(before, after, today=self.today))

    def test_configured_timezone_is_used(self):
        with patch.dict("os.environ", {"RESUME_AGENT_TIMEZONE": "America/Chicago"}):
            from datetime import datetime
            from zoneinfo import ZoneInfo

            expected = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
            self.assertEqual(local_date_iso(), expected)

    def test_invalid_timezone_fails_closed(self):
        with patch.dict("os.environ", {"RESUME_AGENT_TIMEZONE": "Not_A_Timezone"}):
            with self.assertRaises(ValueError):
                local_date_iso()

    def test_runtime_system_prompt_supplies_date(self):
        prompt = utils.load_system_prompt("candidate")
        self.assertIn("Today's local date: " + self.today, prompt)
        self.assertIn("Never write bracketed placeholders", prompt)


class ReviewAndReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "candidate" / "profile"
        self.root.mkdir(parents=True)
        self.path = self.root / "PROFILE.md"
        self.path.write_text("Header\nLast Updated: [Current Date]\n", encoding="utf-8")
        self.today = local_date_iso()

    def action(self, new):
        return {
            "name": "edit_file",
            "args": {
                "file_path": str(self.path),
                "old_string": "Last Updated: [Current Date]",
                "new_string": new,
            },
        }

    def test_review_blocks_unresolved_date_before_any_approval(self):
        actions = []
        result = review_profile_action(
            self.action("Last Updated: [YYYY-MM-DD]"),
            profile_prefix=str(self.root),
            allowed_decisions=["approve", "edit", "reject"],
            input_fn=lambda prompt: actions.append(prompt) or "n",
            output=lambda value: None,
        )
        self.assertEqual(result["type"], "reject")
        self.assertNotIn("Authorize this EXACT file operation? [y/N] ", actions)
        self.assertNotIn(
            "Candidate content: [A]pprove / [V]ise / [R]eject / [N]ew proposal? ",
            actions,
        )
        self.assertIn("[Current Date]", self.path.read_text())

    def test_review_approves_actual_date_with_both_gates(self):
        responses = iter(["a", "y"])
        result = review_profile_action(
            self.action("Last Updated: " + self.today),
            profile_prefix=str(self.root),
            allowed_decisions=["approve", "edit", "reject"],
            input_fn=lambda _prompt: next(responses),
            output=lambda value: None,
        )
        self.assertEqual(result, {"type": "approve"})
        self.assertIn(
            "[Current Date]", self.path.read_text()
        )  # approval alone is not execution

    def test_receipt_bypass_rejects_unresolved_template(self):
        registry = ProfileReceiptRegistry()
        registry.begin_turn("test")
        with self.assertRaisesRegex(
            ValueError, "Authoritative profile content check failed"
        ):
            registry.record_authorized(
                "test",
                self.action("Last Updated: [YYYY-MM-DD]"),
                {"type": "approve"},
                profile_prefix=str(self.root),
            )
        self.assertEqual(registry.unreported("test"), 0)
        self.assertIn("[Current Date]", self.path.read_text())

    def test_receipt_accepts_actual_date(self):
        registry = ProfileReceiptRegistry()
        registry.begin_turn("test")
        registry.record_authorized(
            "test",
            self.action("Last Updated: " + self.today),
            {"type": "approve"},
            profile_prefix=str(self.root),
        )
        self.assertEqual(registry.unreported("test"), 1)


if __name__ == "__main__":
    unittest.main()
