"""Separate, non-destructive field-update and contradiction regression tests.

The smoke alias and the canonical work-arrangement label are one logical field.
No test executes an actual HITL tool write; receipt tests emulate the result
only after a recorded candidate and tool approval.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from profile_conflicts import newly_introduced_profile_conflicts
from profile_receipts import ProfileReceiptRegistry
from profile_review import ReviewError, _project, review_profile_action

FIELD = "Candidate work-arrangement preference: NOT_RECORDED"
BASE = (
    "# FICTIONAL PROFILE\n"
    "Verified experience: prototype Python APIs.\n"
    f"{FIELD}\n\n"
    "This entire document is artificial smoke-test data.\n"
)
CONFIRMED = "Candidate work-arrangement preference: remote"
REVISED = "Candidate work-arrangement preference: remote only"
SMOKE = "SMOKE-CANDIDATE-PREFERENCE: remote"


class FieldUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace" / "candidate" / "profile"
        self.root.mkdir(parents=True)
        self.path = self.root / "SMOKE_PROFILE.md"
        self.path.write_text(BASE, encoding="utf-8")
        self.registry = ProfileReceiptRegistry()
        self.registry.begin_turn("field-update")

    def edit(self, old, new):
        return {
            "name": "edit_file",
            "args": {
                "file_path": str(self.path),
                "old_string": old,
                "new_string": new,
            },
        }

    def review(self, action, answers, request, revise_fn=None):
        answers = iter(answers)
        return review_profile_action(
            action,
            profile_prefix=str(self.root),
            allowed_decisions=["approve", "reject", "edit"],
            original_request=request,
            input_fn=lambda _: next(answers),
            output=lambda *_: None,
            revise_fn=revise_fn,
        )

    def verify(self, action, decision, expected, request):
        self.registry.record_authorized(
            "field-update",
            action,
            decision,
            profile_prefix=str(self.root),
            original_request=request,
        )
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.path.write_text(expected, encoding="utf-8")
        (receipt,) = self.registry.finish_profile_task(
            "field-update", task_success=True
        )
        self.assertEqual(receipt["status"], "applied_verified")
        self.assertTrue(receipt["verified"])
        self.assertEqual(expected.count("work-arrangement preference:"), 1)
        self.assertNotIn("NOT_RECORDED", expected)

    def test_field_update_replaces_existing_not_recorded_without_duplicate(self):
        action = self.edit(FIELD, CONFIRMED)
        request = "Update the existing work-arrangement preference field to remote."
        decision = self.review(action, ["a", "y"], request)
        self.assertEqual(decision, {"type": "approve"})
        expected = BASE.replace(FIELD, CONFIRMED)
        self.assertEqual(_project(action, BASE), expected)
        self.assertIn("Verified experience: prototype Python APIs.", expected)
        self.assertIn("This entire document is artificial smoke-test data.", expected)
        self.verify(action, decision, expected, request)

    def test_field_update_can_be_revised_before_two_separate_approvals(self):
        action = self.edit(FIELD, CONFIRMED)
        request = "Update the existing work-arrangement preference field, without duplicating it."
        decision = self.review(
            action,
            ["v", "a", "y"],
            request,
            revise_fn=lambda _: REVISED,
        )
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(decision["edited_action"]["args"]["old_string"], FIELD)
        expected = BASE.replace(FIELD, REVISED)
        self.assertEqual(_project(decision["edited_action"], BASE), expected)
        self.verify(action, decision, expected, request)

    def test_field_update_second_approval_rejected_means_no_file_change(self):
        action = self.edit(FIELD, CONFIRMED)
        decision = self.review(
            action, ["a", "n"], "Update the existing preference field"
        )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.registry.unreported("field-update"), 0)

    def test_conflicting_append_is_refused_before_candidate_approval(self):
        action = self.edit(
            "This entire document is artificial smoke-test data.\n",
            "This entire document is artificial smoke-test data.\n\n" + SMOKE + "\n",
        )
        with self.assertRaisesRegex(ReviewError, "Conflicting"):
            self.review(action, [], "Append the preference to the end of the file")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_8r_midfile_preference_injection_is_not_moved_to_end(self):
        # Reproduce the agent's observed mistake: it inserted the new
        # preference beside NOT_RECORDED, *above* the disclaimer.
        old = FIELD + "\n\nThis entire document is artificial smoke-test data.\n"
        new = (
            FIELD
            + "\n"
            + SMOKE
            + "\n\nThis entire document is artificial smoke-test data.\n"
        )
        action = self.edit(old, new)
        with self.assertRaisesRegex(ReviewError, "Conflicting"):
            self.review(action, [], "Append the preference to the END of the file")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_duplicate_equivalent_preference_is_refused_even_if_same_value(self):
        self.path.write_text(BASE.replace(FIELD, CONFIRMED), encoding="utf-8")
        action = self.edit(
            "This entire document is artificial smoke-test data.\n",
            "This entire document is artificial smoke-test data.\n\n" + SMOKE + "\n",
        )
        with self.assertRaisesRegex(ReviewError, "Duplicate"):
            self.review(action, [], "Append the preference at the end of the file")

    def test_duplicate_equivalent_preference_approval_cannot_bypass_receipt(self):
        action = self.edit(
            "This entire document is artificial smoke-test data.\n",
            "This entire document is artificial smoke-test data.\n\n" + SMOKE + "\n",
        )
        with self.assertRaisesRegex(ValueError, "profile conflict"):
            self.registry.record_authorized(
                "field-update",
                action,
                {"type": "approve"},
                profile_prefix=str(self.root),
                original_request="Append the preference to end of file",
            )
        self.assertEqual(self.registry.unreported("field-update"), 0)

    def test_unrelated_unknown_field_is_not_a_false_conflict(self):
        before = "# Fictional\nProject status: NOT_RECORDED\n"
        after = before + "\n" + SMOKE + "\n"
        self.assertEqual(newly_introduced_profile_conflicts(before, after), [])


if __name__ == "__main__":
    unittest.main()
