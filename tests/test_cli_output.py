"""Regression coverage for once-per-turn reports and opt-in incremental debug.

These tests use stubs for expensive graph/trace dependencies and synthetic data.
"""

import contextlib
import sys
import unittest
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli


class FakeAIMessage:
    type = "ai"
    response_metadata = {}
    additional_kwargs = {}
    tool_calls = []

    def __init__(self, content):
        self.content = content


class FakeHumanMessage:
    type = "human"
    response_metadata = {}
    additional_kwargs = {}
    tool_calls = []

    def __init__(self, content):
        self.content = content


class CliOutputTests(unittest.TestCase):
    @staticmethod
    @contextlib.contextmanager
    def _fake_environment():
        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield

        fake_langsmith = ModuleType("langsmith")
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: SimpleNamespace()
        fake_messages = ModuleType("langchain_core.messages")
        fake_messages.AIMessage = FakeAIMessage
        fake_messages.ToolMessage = type("ToolMessage", (), {})

        with patch.dict(
            sys.modules,
            {
                "langsmith": fake_langsmith,
                "agent": fake_agent,
                "langchain_core.messages": fake_messages,
            },
        ):
            yield

    def test_rejected_write_report_exactly_once_not_per_message(self):
        def run(_graph, _payload, *, write_audit, **_kwargs):
            write_audit.record(
                {
                    "name": "write_file",
                    "args": {"file_path": "/workspace/applications/test/result.md"},
                },
                authorized=False,
            )
            return {
                "messages": [
                    FakeHumanMessage("create file"),
                    FakeAIMessage("tool call"),
                    FakeAIMessage("False claim: file created"),
                ]
            }

        out = StringIO()
        err = StringIO()
        with self._fake_environment(), patch.object(cli, "_run_to_completion", run):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["--message", "create file"]), 0)
        self.assertEqual(out.getvalue().count("Filesystem authorization report"), 1)
        self.assertIn("REJECTED: write_file", out.getvalue())
        self.assertNotIn("False claim: file created", out.getvalue())
        self.assertNotIn("[DEBUG]", err.getvalue())

    def test_debug_is_off_by_default_and_enabled_explicitly(self):
        def run(_graph, _payload, **_kwargs):
            return {"messages": [FakeHumanMessage("hello"), FakeAIMessage("Done")]}

        with self._fake_environment(), patch.object(cli, "_run_to_completion", run):
            err = StringIO()
            with (
                contextlib.redirect_stdout(StringIO()),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(cli.main(["--message", "hello"]), 0)
            self.assertEqual(err.getvalue(), "")

            err = StringIO()
            with (
                contextlib.redirect_stdout(StringIO()),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(cli.main(["--debug", "--message", "hello"]), 0)
            self.assertEqual(err.getvalue().count("[DEBUG]"), 2)

    def test_interactive_debug_only_emits_new_messages(self):
        history = [FakeHumanMessage("first"), FakeAIMessage("one")]
        responses = iter(
            [
                {"messages": list(history)},
                {
                    "messages": [
                        *history,
                        FakeHumanMessage("second"),
                        FakeAIMessage("two"),
                    ]
                },
            ]
        )

        def run(_graph, _payload, **_kwargs):
            return next(responses)

        commands = iter(["first", "second", "exit"])
        err = StringIO()
        with self._fake_environment(), patch.object(cli, "_run_to_completion", run):
            with patch("builtins.input", side_effect=lambda _: next(commands)):
                with (
                    contextlib.redirect_stdout(StringIO()),
                    contextlib.redirect_stderr(err),
                ):
                    self.assertEqual(cli.main(["--debug"]), 0)
        self.assertEqual(err.getvalue().count("[DEBUG]"), 4)


if __name__ == "__main__":
    unittest.main()
