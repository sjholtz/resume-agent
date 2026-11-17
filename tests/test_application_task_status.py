import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(ROOT))

from application_task_status import (
    REGISTRY,
    rejected_write_status,
    replace_task_narrative,
)
from write_decision_audit import WriteDecisionAudit


class FakeToolMessage:
    def __init__(self, content, tool_call_id="task-1", status="success", name="task"):
        self.content = content
        self.tool_call_id = tool_call_id
        self.status = status
        self.name = name

    def model_copy(self, update):
        return FakeToolMessage(
            update.get("content", self.content),
            self.tool_call_id,
            self.status,
            self.name,
        )


@dataclass(frozen=True)
class FakeCommand:
    update: dict


def install_deps():
    pr = ModuleType("profile_receipts")
    pr.REGISTRY = object()
    pr.add_receipts_to_tool_result = lambda result, receipts, **kwargs: result
    pr.thread_from_runtime = lambda runtime: runtime.config.get("configurable", {}).get(
        "thread_id", ""
    )
    sys.modules["profile_receipts"] = pr
    core = ModuleType("langchain_core")
    msgs = ModuleType("langchain_core.messages")
    msgs.ToolMessage = FakeToolMessage
    core.messages = msgs
    sys.modules["langchain_core"] = core
    sys.modules["langchain_core.messages"] = msgs
    lg = ModuleType("langgraph")
    types = ModuleType("langgraph.types")
    types.Command = FakeCommand
    lg.types = types
    sys.modules["langgraph"] = lg
    sys.modules["langgraph.types"] = types
    lc = ModuleType("langchain")
    agents = ModuleType("langchain.agents")
    middle = ModuleType("langchain.agents.middleware")
    middle.wrap_tool_call = lambda f: f
    agents.middleware = middle
    lc.agents = agents
    sys.modules["langchain"] = lc
    sys.modules["langchain.agents"] = agents
    sys.modules["langchain.agents.middleware"] = middle


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Snapshot sys.modules before installing fake dependencies.
        # Restore the entire module cache after this test class.
        cls._modules_patch = patch.dict(sys.modules, {})
        cls._modules_patch.start()
        cls.addClassCleanup(cls._modules_patch.stop)

        # Ensure the middleware is imported against our test stubs,
        # rather than reusing a previously cached real import.
        sys.modules.pop("profile_receipt_middleware", None)

        install_deps()

    def setUp(self):
        self.audit = WriteDecisionAudit()
        REGISTRY.begin_turn("t", self.audit)

    def test_denied_status_is_not_fake_execution_verification(self):
        self.audit.record(
            {
                "name": "write_file",
                "args": {
                    "file_path": "/workspace/applications/smoke-test/SMOKE_RESULT.md"
                },
            },
            authorized=False,
        )
        text = rejected_write_status(self.audit.decisions)
        self.assertIn("rejected_not_authorized", text)
        self.assertIn("authorization_only", text)
        self.assertIn(
            "Do not retry without a new request",
            text.replace(
                "do not retry without a new request",
                "Do not retry without a new request",
            ),
        )

    def test_approved_only_does_not_claim_success_or_replace(self):
        self.audit.record(
            {
                "name": "write_file",
                "args": {"file_path": "/workspace/applications/test.md"},
            },
            authorized=True,
        )
        self.assertIsNone(rejected_write_status(self.audit.decisions))

    def test_rejected_result_replaces_fake_success(self):
        text = rejected_write_status([_denied_decision()])
        result = replace_task_narrative(
            FakeToolMessage("The file was successfully completed."),
            text,
            tool_call_id="task-1",
        )
        self.assertNotIn("successfully completed", result.content)
        self.assertEqual(result.status, "success")
        self.assertIn("REJECTED", result.content)

    def test_command_result_replacement_preserves_other_messages(self):
        text = rejected_write_status([_denied_decision()])
        original = FakeToolMessage("hallucinated success", tool_call_id="task-1")
        other = FakeToolMessage("other message", tool_call_id="other")
        result = replace_task_narrative(
            FakeCommand({"messages": [original, other], "flag": True}),
            text,
            tool_call_id="task-1",
        )
        self.assertIn("REJECTED", result.update["messages"][0].content)
        self.assertEqual(result.update["messages"][1].content, "other message")
        self.assertTrue(result.update["flag"])

    def test_mismatched_tool_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            replace_task_narrative(
                FakeToolMessage("ok", "wrong"), "reject", tool_call_id="task-1"
            )

    def test_registry_survives_interrupt_reentry(self):
        REGISTRY.start_task("t", "task-1")
        self.audit.record(
            {
                "name": "write_file",
                "args": {
                    "file_path": "/workspace/applications/smoke-test/SMOKE_RESULT.md"
                },
            },
            authorized=False,
        )
        REGISTRY.start_task("t", "task-1")
        decisions = REGISTRY.finish_task("t", "task-1")
        self.assertEqual(len(decisions), 1)
        self.assertFalse(decisions[0].authorized)

    def test_restricted_parallel_tasks(self):
        REGISTRY.start_task("t", "task-1")
        with self.assertRaisesRegex(RuntimeError, "Concurrent"):
            REGISTRY.start_task("t", "task-2")

    def test_new_turn_does_not_retain_decisions(self):
        REGISTRY.start_task("t", "task-1")
        self.audit.record(
            {"name": "write_file", "args": {"file_path": "/workspace/a"}},
            authorized=False,
        )
        REGISTRY.finish_task("t", "task-1")
        next_audit = WriteDecisionAudit()
        REGISTRY.begin_turn("t", next_audit)
        REGISTRY.start_task("t", "task-2")
        self.assertEqual(REGISTRY.finish_task("t", "task-2"), [])

    def test_middleware_replaces_delegated_false_success(self):
        import profile_receipt_middleware as middleware

        request = SimpleNamespace(
            tool_call={
                "name": "task",
                "id": "task-1",
                "args": {"subagent_type": "application-agent"},
            },
            runtime=SimpleNamespace(config={"configurable": {"thread_id": "t"}}),
        )

        def handler(_request):
            self.audit.record(
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/workspace/applications/smoke-test/SMOKE_RESULT.md"
                    },
                },
                authorized=False,
            )
            return FakeToolMessage("The write operation was successfully completed.")

        result = middleware.profile_task_receipt_middleware(request, handler)
        self.assertIn("rejected_not_authorized", result.content)
        self.assertNotIn("successfully completed", result.content)

    def test_middleware_leaves_approved_task_narrative_unverified(self):
        import profile_receipt_middleware as middleware

        request = SimpleNamespace(
            tool_call={
                "name": "task",
                "id": "task-1",
                "args": {"subagent_type": "application-agent"},
            },
            runtime=SimpleNamespace(config={"configurable": {"thread_id": "t"}}),
        )

        def handler(_request):
            self.audit.record(
                {
                    "name": "write_file",
                    "args": {"file_path": "/workspace/applications/test.md"},
                },
                authorized=True,
            )
            return FakeToolMessage("Agent says success")

        result = middleware.profile_task_receipt_middleware(request, handler)
        self.assertEqual(result.content, "Agent says success")

    def test_non_task_passthrough(self):
        import profile_receipt_middleware as middleware

        request = SimpleNamespace(
            tool_call={
                "name": "read_file",
                "id": "read-1",
                "args": {"file_path": "/workspace/a"},
            },
            runtime=SimpleNamespace(config={}),
        )
        result = middleware.profile_task_receipt_middleware(
            request, lambda _: FakeToolMessage("ok", tool_call_id="read-1")
        )
        self.assertEqual(result.content, "ok")


def _denied_decision():
    a = WriteDecisionAudit()
    a.record(
        {
            "name": "write_file",
            "args": {"file_path": "/workspace/applications/smoke-test/SMOKE_RESULT.md"},
        },
        authorized=False,
    )
    return a.decisions[0]


if __name__ == "__main__":
    unittest.main()
