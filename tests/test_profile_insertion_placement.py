"""Regression coverage for safe profile prepend, middle insertion, and append.

Only temporary profile files are used. No test performs a tool write from HITL;
manual file updates emulate execution solely to test receipt verification.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from profile_receipts import ProfileReceiptRegistry
from profile_review import (
    ReviewError,
    _project,
    is_explicit_begin_prepend_request,
    is_explicit_end_append_request,
    review_profile_action,
)

BASE = (
    "# FICTIONAL TEST PROFILE\n"
    "Verified skill: Python\n"
    "\n"
    "This entire document is artificial smoke-test data.\n"
)
LINE = "SMOKE-CANDIDATE-PREFERENCE: remote"
REVISED = "SMOKE-CANDIDATE-PREFERENCE: remote only"
DISCLAIMER = "This entire document is artificial smoke-test data.\n"


class InsertionPlacementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace" / "candidate" / "profile"
        self.root.mkdir(parents=True)
        self.path = self.root / "SMOKE_PROFILE.md"
        self.path.write_text(BASE, encoding="utf-8")
        self.registry = ProfileReceiptRegistry()
        self.registry.begin_turn("t")

    def edit(self, old, new):
        return {
            "name": "edit_file",
            "args": {
                "file_path": str(self.path),
                "old_string": old,
                "new_string": new,
            },
        }

    def write(self, content):
        return {
            "name": "write_file",
            "args": {
                "file_path": str(self.path),
                "content": content,
            },
        }

    def middle(self):
        old = "Verified skill: Python\n"
        return self.edit(old, old + LINE + "\n")

    def prepend(self):
        old = "# FICTIONAL TEST PROFILE\n"
        return self.edit(old, LINE + "\n" + old)

    def misplaced_append(self):
        old = "Verified skill: Python\n\n" + DISCLAIMER
        return self.edit(old, "Verified skill: Python\n" + LINE + "\n\n" + DISCLAIMER)

    def review(self, action, answers, request, *, revise_fn=None, output=None):
        answers = iter(answers)
        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": ""}):
            return review_profile_action(
                action,
                profile_prefix=str(self.root),
                original_request=request,
                allowed_decisions=["approve", "reject", "edit"],
                input_fn=lambda _: next(answers),
                output=output if output is not None else lambda *_: None,
                revise_fn=revise_fn,
            )

    def verify_receipt(self, action, decision, request, expected):
        self.registry.record_authorized(
            "t",
            action,
            decision,
            profile_prefix=str(self.root),
            original_request=request,
        )
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.path.write_text(expected, encoding="utf-8")
        (receipt,) = self.registry.finish_profile_task("t", task_success=True)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["status"], "applied_verified")
        return receipt

    def test_8r_revision_preserves_exact_single_line_suffix(self):
        action = self.write(BASE + "SMOKE-APPEND-REVISION-TEST: initial\n")
        request = "Append one new line to the END of the profile."
        decision = self.review(
            action, ["v", "SMOKE-APPEND-REVISION-TEST: revised", "a", "y"], request
        )
        self.assertEqual(decision["type"], "edit")
        expected = BASE + "SMOKE-APPEND-REVISION-TEST: revised\n"
        self.assertEqual(_project(decision["edited_action"], BASE), expected)
        self.verify_receipt(action, decision, request, expected)

    def test_one_line_append_with_extra_blank_line_is_rejected(self):
        action = self.write(BASE + "\n" + LINE + "\n")
        decision = self.review(
            action, ["n"], "Append one new line to the END of the profile"
        )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_8r_misplaced_append_is_moved_to_end_before_review(self):
        action = self.misplaced_append()
        messages = []
        request = "Append exactly one line to the END of the file."
        decision = self.review(
            action, ["v", REVISED, "a", "y"], request, output=messages.append
        )
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(decision["edited_action"]["name"], "edit_file")
        self.assertEqual(decision["edited_action"]["args"]["file_path"], str(self.path))
        self.assertEqual(
            _project(decision["edited_action"], BASE), BASE + REVISED + "\n"
        )
        self.assertIn("Corrected to an end-of-file append", " ".join(messages))
        self.assertIn("+" + REVISED, "\n".join(messages))
        receipt = self.verify_receipt(action, decision, request, BASE + REVISED + "\n")
        self.assertTrue(receipt["candidate_revised"])

    def test_misplaced_append_approved_without_revision_is_still_tail_only(self):
        action = self.misplaced_append()
        request = "Append a line at the end of the profile."
        decision = self.review(action, ["a", "y"], request)
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(_project(decision["edited_action"], BASE), BASE + LINE + "\n")
        self.verify_receipt(action, decision, request, BASE + LINE + "\n")

    def test_end_append_never_allows_replacing_original_content(self):
        action = self.edit("Verified skill: Python", "Work: remote")
        with self.assertRaisesRegex(ReviewError, "Append-only request"):
            self.review(action, [], "Append the preference at the end of the file")
        self.assertEqual(self.path.read_text(), BASE)

    def test_prepending_edit_file_revision_preserves_every_original_line(self):
        action = self.prepend()
        request = "Prepend the preference at the start of the profile."
        decision = self.review(action, ["v", REVISED, "a", "y"], request)
        self.assertEqual(decision["type"], "edit")
        expected = REVISED + "\n" + BASE
        self.assertEqual(_project(decision["edited_action"], BASE), expected)
        self.assertEqual(
            decision["edited_action"]["args"]["old_string"],
            action["args"]["old_string"],
        )
        self.verify_receipt(action, decision, request, expected)

    def test_prepending_write_file_revision_preserves_line_separator(self):
        action = self.write(LINE + "\n" + BASE)
        request = "Prepend a line to the beginning of the file."
        decision = self.review(action, ["v", REVISED, "a", "y"], request)
        self.assertEqual(decision["type"], "edit")
        expected = REVISED + "\n" + BASE
        self.assertEqual(decision["edited_action"]["args"]["content"], expected)
        self.verify_receipt(action, decision, request, expected)

    def test_middle_edit_file_revision_does_not_merge_adjacent_lines(self):
        action = self.middle()
        request = "Insert the preference immediately after the skill line."
        decision = self.review(action, ["v", REVISED, "a", "y"], request)
        self.assertEqual(decision["type"], "edit")
        expected = BASE.replace(
            "Verified skill: Python\n", "Verified skill: Python\n" + REVISED + "\n"
        )
        self.assertEqual(_project(decision["edited_action"], BASE), expected)
        self.assertIn(REVISED + "\n\nThis entire", expected)
        self.verify_receipt(action, decision, request, expected)

    def test_middle_write_file_revision_remains_in_the_middle(self):
        intended = BASE.replace(
            "Verified skill: Python\n", "Verified skill: Python\n" + LINE + "\n"
        )
        action = self.write(intended)
        request = "Insert the preference in the skills section, before the disclaimer."
        decision = self.review(action, ["v", REVISED, "a", "y"], request)
        expected = intended.replace(LINE, REVISED)
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(_project(decision["edited_action"], BASE), expected)
        self.verify_receipt(action, decision, request, expected)

    def test_middle_insert_does_not_move_when_no_end_append_requested(self):
        action = self.middle()
        decision = self.review(action, ["a", "y"], "Insert after the skill line")
        self.assertEqual(decision, {"type": "approve"})
        self.verify_receipt(
            action, decision, "Insert after the skill line", _project(action, BASE)
        )

    def test_misplaced_prepend_is_rejected_instead_of_relocated(self):
        with self.assertRaisesRegex(ReviewError, "Prepend-only"):
            self.review(self.middle(), [], "Prepend the preference to the profile")
        self.assertEqual(self.path.read_text(), BASE)

    def test_prepending_does_not_run_for_negated_request(self):
        self.assertFalse(
            is_explicit_begin_prepend_request("Do not prepend. Insert in the middle.")
        )
        self.assertFalse(
            is_explicit_end_append_request("Do not append. Insert in the middle.")
        )
        self.assertTrue(
            is_explicit_begin_prepend_request(
                "Insert this at the beginning of the file"
            )
        )
        self.assertTrue(
            is_explicit_end_append_request("Append this at the end of the file")
        )

    def test_moving_middle_insertion_during_revision_is_blocked(self):
        # Anchor includes the tail of the file so a malicious editor callback
        # *can* express a move. The review scope must still reject it.
        action = self.misplaced_append()
        old = action["args"]["old_string"]
        revised_at_end = old + "\n" + LINE + "\n"
        decision = self.review(
            action,
            ["v", "n"],
            "Insert after the skill line",
            revise_fn=lambda _: revised_at_end,
        )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(), BASE)

    def test_deleted_disclaimer_during_insertion_revision_is_rejected(self):
        action = self.misplaced_append()
        request = "Insert after the skill line"
        decision = self.review(
            action,
            ["v", "n"],
            request,
            revise_fn=lambda _: "Verified skill: Python\n" + LINE + "\n",
        )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(), BASE)

    def test_editor_sees_only_new_middle_text_not_existing_document(self):
        action = self.middle()
        answers = iter(["v", "a", "y"])
        with (
            patch(
                "profile_review._edit_in_editor", return_value=REVISED + "\n"
            ) as editor,
            patch.dict(os.environ, {"VISUAL": "nano", "EDITOR": "nano"}),
        ):
            decision = review_profile_action(
                action,
                profile_prefix=str(self.root),
                allowed_decisions=["approve", "reject", "edit"],
                original_request="Insert after the skill line",
                input_fn=lambda _: next(answers),
                output=lambda *_: None,
            )
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(editor.call_args.args[0], LINE + "\n")
        self.assertNotIn(DISCLAIMER, editor.call_args.args[0])
        self.assertEqual(self.path.read_text(), BASE)

    def test_end_append_when_original_lacks_final_newline(self):
        before = BASE.rstrip("\n")
        self.path.write_text(before, encoding="utf-8")
        old = "This entire document is artificial smoke-test data."
        action = self.edit(old, old + "\n\n" + LINE + "\n")
        decision = self.review(
            action, ["v", REVISED, "a", "y"], "Append the line to the end of the file"
        )
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(
            _project(decision["edited_action"], before),
            before + "\n\n" + REVISED + "\n",
        )
        self.assertEqual(self.path.read_text(), before)

    def test_explicit_replacement_is_not_forced_into_insertion_mode(self):
        action = self.edit("Verified skill: Python", "Work: remote only")
        decision = self.review(
            action, ["a", "y"], "Replace the Work preference with remote only"
        )
        self.assertEqual(decision, {"type": "approve"})
        expected = BASE.replace("Verified skill: Python", "Work: remote only")
        self.verify_receipt(
            action, decision, "Replace the Work preference with remote only", expected
        )

    def test_rejected_tool_does_not_write_prepended_content(self):
        action = self.prepend()
        decision = self.review(
            action, ["v", REVISED, "a", "n"], "Prepend at the start of the file"
        )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(), BASE)
        self.assertEqual(self.registry.unreported("t"), 0)

    def test_missing_edited_tool_permission_cannot_apply_append_correction(self):
        action = self.misplaced_append()
        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": ""}):
            decision = review_profile_action(
                action,
                profile_prefix=str(self.root),
                allowed_decisions=["approve", "reject"],
                original_request="Append to end of file",
                input_fn=lambda _: "a",
                output=lambda *_: None,
            )
        self.assertEqual(decision["type"], "reject")
        self.assertEqual(self.path.read_text(), BASE)

    def test_receipt_cannot_change_middle_insertion_to_append_unasked(self):
        action = self.misplaced_append()
        old = action["args"]["old_string"]
        bad = {
            "type": "edit",
            "edited_action": self.edit(
                old,
                old + "\n" + LINE + "\n",
            ),
        }
        with self.assertRaisesRegex(ValueError, "location"):
            self.registry.record_authorized(
                "t",
                action,
                bad,
                profile_prefix=str(self.root),
                original_request="Insert after the skill line",
            )
        self.assertEqual(self.registry.unreported("t"), 0)

    def test_receipt_enforces_prepend_location_even_with_cli_bypass(self):
        action = self.middle()
        with self.assertRaisesRegex(ValueError, "Prepend-only"):
            self.registry.record_authorized(
                "t",
                action,
                {"type": "approve"},
                profile_prefix=str(self.root),
                original_request="Prepend at beginning of file",
            )
        self.assertEqual(self.registry.unreported("t"), 0)

    def test_duplicate_old_anchor_still_fails_closed(self):
        self.path.write_text("same\nsame\n", encoding="utf-8")
        action = self.edit("same\n", "new\nsame\n")
        with self.assertRaisesRegex(ReviewError, "exactly once"):
            self.review(action, [], "Insert at the beginning")

    def test_conflicting_append_prepend_request_rejected(self):
        with self.assertRaisesRegex(ReviewError, "Conflicting"):
            self.review(self.prepend(), [], "Prepend and append to the end of the file")


if __name__ == "__main__":
    unittest.main()
