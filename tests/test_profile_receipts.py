"""Offline tests: python3 -m unittest -v tests/test_profile_receipts.py

Uses only stdlib and the copied v3 profile_review.py; does not call Ollama.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from profile_receipts import (
    RECEIPT_PREFIX,
    REGISTRY,
    SCHEMA,
    ProfileReceiptRegistry,
    add_receipts_to_tool_result,
    receipt_text,
    thread_from_runtime,
)
from profile_review import review_profile_action

BASE = (
    "# FICTIONAL TEST CANDIDATE — NOT A REAL PROFILE\n\n"
    "Candidate evidence status: PARTIALLY_COMPLETE\n\n"
    "This entire document is artificial smoke-test data. Never incorporate it\n"
    "into a real candidate's materials.\n"
)
OLD = (
    "This entire document is artificial smoke-test data. Never incorporate it\n"
    "into a real candidate's materials."
)
ORIGINAL = "SMOKE-CANDIDATE-PREFERENCE: remote"
REVISED = "SMOKE-CANDIDATE-PREFERENCE: remote only"


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "workspace" / "candidate" / "profile"
        self.root.mkdir(parents=True)
        self.path = self.root / "SMOKE_PROFILE.md"
        self.path.write_text(BASE, encoding="utf-8")
        self.action = {
            "name": "edit_file",
            "args": {
                "file_path": str(self.path),
                "old_string": OLD,
                "new_string": OLD + "\n\n" + ORIGINAL,
            },
        }
        self.registry = ProfileReceiptRegistry()
        self.registry.begin_turn("t1")

    def decision(self, revised: bool):
        if not revised:
            return {"type": "approve"}
        return {
            "type": "edit",
            "edited_action": {
                "name": "edit_file",
                "args": {
                    **self.action["args"],
                    "new_string": OLD + "\n\n" + REVISED,
                },
            },
        }

    def authorize(self, revised=True):
        self.registry.record_authorized(
            "t1",
            self.action,
            self.decision(revised),
            profile_prefix=str(self.root),
        )

    def test_revised_profile_receipt_verified_exact(self):
        self.authorize()
        self.assertEqual(self.path.read_text(), BASE)
        self.path.write_text(BASE + "\n" + REVISED + "\n")
        receipts = self.registry.finish_profile_task("t1", task_success=True)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]
        self.assertEqual(r["schema"], SCHEMA)
        self.assertEqual(r["status"], "applied_verified")
        self.assertTrue(r["verified"])
        self.assertTrue(r["candidate_revised"])
        self.assertIn("+" + REVISED, r["approved_change_diff"])
        self.assertIn("+" + ORIGINAL, r["original_proposal_diff"])
        self.assertNotIn(
            "-into a real candidate's materials", r["approved_change_diff"]
        )
        self.assertEqual(self.registry.unreported("t1"), 0)
        self.assertEqual(self.registry.completed("t1"), receipts)
        self.assertEqual(self.registry.finish_profile_task("t1", task_success=True), [])

    def test_unchanged_approved_content(self):
        self.authorize(revised=False)
        self.path.write_text(BASE + "\n" + ORIGINAL + "\n")
        r = self.registry.finish_profile_task("t1", task_success=True)[0]
        self.assertFalse(r["candidate_revised"])
        self.assertTrue(r["verified"])

    def test_authorization_without_actual_write_not_verified(self):
        self.authorize()
        r = self.registry.finish_profile_task("t1", task_success=True)[0]
        self.assertEqual(r["status"], "not_verified")
        self.assertFalse(r["verified"])
        self.assertEqual(self.path.read_text(), BASE)

    def test_wrong_actual_content_not_verified(self):
        self.authorize()
        self.path.write_text(BASE + "\n" + ORIGINAL + "\n")
        r = self.registry.finish_profile_task("t1", task_success=True)[0]
        self.assertEqual(r["status"], "not_verified")

    def test_task_failure_even_when_file_matches_is_not_verified(self):
        self.authorize()
        self.path.write_text(BASE + "\n" + REVISED + "\n")
        r = self.registry.finish_profile_task("t1", task_success=False)[0]
        self.assertEqual(r["status"], "file_matches_but_task_failed")
        self.assertFalse(r["verified"])

    def test_reject_produces_no_receipt(self):
        self.registry.record_authorized(
            "t1", self.action, {"type": "reject"}, profile_prefix=str(self.root)
        )
        self.assertFalse(self.registry.finish_profile_task("t1", task_success=True))

    def test_changed_path_is_refused(self):
        decision = self.decision(revised=True)
        decision["edited_action"]["args"]["file_path"] = str(self.root / "OTHER.md")
        with self.assertRaises(ValueError):
            self.registry.record_authorized(
                "t1", self.action, decision, profile_prefix=str(self.root)
            )

    def test_changed_old_string_is_refused(self):
        decision = self.decision(revised=True)
        decision["edited_action"]["args"]["old_string"] = "preference"
        with self.assertRaises(ValueError):
            self.registry.record_authorized(
                "t1", self.action, decision, profile_prefix=str(self.root)
            )

    def test_duplicate_authorization_fails_closed(self):
        self.authorize()
        with self.assertRaises(ValueError):
            self.authorize()

    def test_begin_turn_drops_old_turn_data(self):
        self.authorize()
        self.registry.begin_turn("t1")
        self.assertEqual(self.registry.unreported("t1"), 0)

    def test_serialization_has_single_machine_readable_line(self):
        self.authorize()
        self.path.write_text(BASE + "\n" + REVISED + "\n")
        receipt = self.registry.finish_profile_task("t1", task_success=True)[0]
        line = receipt_text([receipt])
        self.assertTrue(line.startswith(RECEIPT_PREFIX))
        self.assertEqual(json.loads(line[len(RECEIPT_PREFIX) :]), receipt)

    def test_v3_review_edit_is_recordable_and_does_not_write(self):
        inputs = iter(["v", "a", "y"])
        decision = review_profile_action(
            self.action,
            profile_prefix=str(self.root),
            allowed_decisions=["approve", "reject", "edit"],
            input_fn=lambda _prompt: next(inputs),
            output=lambda *_: None,
            revise_fn=lambda _: OLD + "\n\n" + REVISED,
        )
        self.assertEqual(decision["type"], "edit")
        self.assertEqual(self.path.read_text(), BASE)
        self.registry.record_authorized(
            "t1", self.action, decision, profile_prefix=str(self.root)
        )
        self.path.write_text(BASE + "\n" + REVISED + "\n")
        receipt = self.registry.finish_profile_task("t1", task_success=True)[0]
        self.assertTrue(receipt["verified"])

    def test_cli_tracks_approved_operation_before_execution(self):
        import cli

        REGISTRY.begin_turn("cli-receipt-test")
        request = {
            "action_requests": [self.action],
            "review_configs": [
                {
                    "action_name": "edit_file",
                    "allowed_decisions": ["approve", "edit", "reject"],
                }
            ],
        }
        import contextlib
        import io

        answers = iter(["a", "y"])
        with contextlib.redirect_stdout(io.StringIO()):
            response = cli._prompt_approvals(
                request,
                profile=str(self.root),
                thread_id="cli-receipt-test",
                input_fn=lambda _: next(answers),
            )
        self.assertEqual(response["decisions"], [{"type": "approve"}])
        self.assertEqual(REGISTRY.unreported("cli-receipt-test"), 1)
        self.assertEqual(self.path.read_text(), BASE)

    def test_cli_reject_does_not_create_receipt(self):
        import cli

        REGISTRY.begin_turn("cli-reject-test")
        request = {
            "action_requests": [self.action],
            "review_configs": [
                {
                    "action_name": "edit_file",
                    "allowed_decisions": ["approve", "edit", "reject"],
                }
            ],
        }
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            response = cli._prompt_approvals(
                request,
                profile=str(self.root),
                thread_id="cli-reject-test",
                input_fn=lambda _: "r",
            )
        self.assertEqual(response["decisions"][0]["type"], "reject")
        self.assertEqual(REGISTRY.unreported("cli-reject-test"), 0)

    def test_runtime_config_thread(self):
        runtime = types.SimpleNamespace(config={"configurable": {"thread_id": "abc"}})
        self.assertEqual(thread_from_runtime(runtime), "abc")
        self.assertEqual(thread_from_runtime(types.SimpleNamespace(config={})), "")

    def test_tool_result_receipt_injection_with_stubbed_langchain(self):
        class StubToolMessage:
            def __init__(self, content, status="success", tool_call_id="task-1"):
                self.content = content
                self.status = status
                self.tool_call_id = tool_call_id

            def model_copy(self, update):
                return StubToolMessage(
                    update["content"], self.status, self.tool_call_id
                )

        @dataclass
        class StubCommand:
            update: dict

        fake_lc = types.ModuleType("langchain_core")
        fake_messages = types.ModuleType("langchain_core.messages")
        fake_messages.ToolMessage = StubToolMessage
        fake_lc.messages = fake_messages
        fake_lg = types.ModuleType("langgraph")
        fake_lgtypes = types.ModuleType("langgraph.types")
        fake_lgtypes.Command = StubCommand
        fake_lg.types = fake_lgtypes
        with patch.dict(
            sys.modules,
            {
                "langchain_core": fake_lc,
                "langchain_core.messages": fake_messages,
                "langgraph": fake_lg,
                "langgraph.types": fake_lgtypes,
            },
        ):
            r = {"schema": SCHEMA, "status": "applied_verified"}
            result = add_receipts_to_tool_result(StubToolMessage("Task complete"), [r])
            self.assertIn(RECEIPT_PREFIX, result.content)
            self.assertIn('"applied_verified"', result.content)
            nested = StubCommand(
                {
                    "messages": [
                        StubToolMessage("Another", tool_call_id="other"),
                        StubToolMessage("Done", tool_call_id="task-1"),
                    ]
                }
            )
            result2 = add_receipts_to_tool_result(nested, [r], tool_call_id="task-1")
            self.assertNotIn(RECEIPT_PREFIX, result2.update["messages"][0].content)
            self.assertIn(RECEIPT_PREFIX, result2.update["messages"][1].content)


if __name__ == "__main__":
    unittest.main()
