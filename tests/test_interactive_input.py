"""Regression tests for session-only Readline and /edit composition."""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interactive_input as user_input


class InteractiveInputTests(unittest.TestCase):
    def test_non_tty_does_not_import_or_configure_readline(self):
        with patch.object(
            user_input.sys, "stdin", SimpleNamespace(isatty=lambda: False)
        ):
            self.assertIsNone(user_input.configure_readline())

    def test_readline_disables_auto_history_and_clears_existing_history(self):
        rl = Mock()
        fake_rl = ModuleType("readline")
        fake_rl.set_auto_history = rl.set_auto_history
        fake_rl.clear_history = rl.clear_history
        with (
            patch.object(user_input.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
            patch.dict(sys.modules, {"readline": fake_rl}),
        ):
            self.assertIs(user_input.configure_readline(), fake_rl)
        rl.set_auto_history.assert_called_once_with(False)
        rl.clear_history.assert_called_once_with()

    def test_readline_without_safe_auto_history_does_not_enable_manual_history(self):
        fake_rl = ModuleType("readline")
        with (
            patch.object(user_input.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
            patch.dict(sys.modules, {"readline": fake_rl}),
        ):
            self.assertIsNone(user_input.configure_readline())

    def test_only_explicit_chat_message_added_to_history(self):
        rl = Mock()
        rl.get_current_history_length.return_value = 1
        user_input.remember_chat_message(rl, "Plan my application")
        rl.add_history.assert_called_once_with("Plan my application")
        user_input.remember_chat_message(rl, "   ")
        self.assertEqual(rl.add_history.call_count, 1)

    def test_history_bounded_without_disk_write(self):
        rl = Mock()
        rl.get_current_history_length.return_value = user_input.MAX_CHAT_HISTORY + 1
        user_input.remember_chat_message(rl, "Some message")
        rl.remove_history_item.assert_called_once_with(0)
        self.assertFalse(rl.write_history_file.called)
        self.assertFalse(rl.read_history_file.called)

    def test_editor_uses_visual_with_quoted_arguments_and_mode_600(self):
        observed = {}

        def simulate_editor(args, *, check):
            observed["cmd"] = args
            observed["mode"] = stat.S_IMODE(os.stat(args[-1]).st_mode)
            Path(args[-1]).write_text(
                "First paragraph\nSecond paragraph\n", encoding="utf-8"
            )
            observed["file"] = args[-1]
            return SimpleNamespace(returncode=0)

        with patch.dict(os.environ, {"VISUAL": "nano -w", "EDITOR": "vi"}):
            with patch.object(
                user_input.subprocess, "run", side_effect=simulate_editor
            ):
                value = user_input.edit_chat_message()
        self.assertEqual(value, "First paragraph\nSecond paragraph")
        self.assertEqual(observed["cmd"][0:2], ["nano", "-w"])
        self.assertEqual(observed["mode"], 0o600)
        self.assertFalse(Path(observed["file"]).exists())

    def test_editor_falls_back_to_editor(self):
        def editor(args, *, check):
            Path(args[-1]).write_text("A message\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": '"my editor" --wait'}):
            with patch.object(
                user_input.subprocess, "run", side_effect=editor
            ) as mock_run:
                self.assertEqual(user_input.edit_chat_message(), "A message")
        self.assertEqual(mock_run.call_args.args[0][:2], ["my editor", "--wait"])

    def test_empty_editor_cancels_and_removes_tempfile(self):
        observed = {}

        def editor(args, *, check):
            observed["file"] = args[-1]
            return SimpleNamespace(returncode=0)

        with patch.dict(os.environ, {"VISUAL": "nano"}):
            with patch.object(user_input.subprocess, "run", side_effect=editor):
                self.assertIsNone(user_input.edit_chat_message())
        self.assertFalse(Path(observed["file"]).exists())

    def test_nonzero_editor_does_not_submit(self):
        with (
            patch.dict(os.environ, {"VISUAL": "nano"}),
            patch.object(
                user_input.subprocess, "run", return_value=SimpleNamespace(returncode=1)
            ),
        ):
            self.assertIsNone(user_input.edit_chat_message())

    def test_no_editor_configuration_does_not_start_subprocess(self):
        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": ""}):
            with patch.object(user_input.subprocess, "run") as run:
                self.assertIsNone(user_input.edit_chat_message())
                run.assert_not_called()

    def test_malformed_editor_command_does_not_execute(self):
        with patch.dict(os.environ, {"VISUAL": '"unterminated', "EDITOR": "nano"}):
            with patch.object(user_input.subprocess, "run") as run:
                self.assertIsNone(user_input.edit_chat_message())
                run.assert_not_called()

    def test_editor_exception_cleans_up_tempfile(self):
        observed = {}

        def editor(args, *, check):
            observed["file"] = args[-1]
            raise FileNotFoundError("editor missing")

        with patch.dict(os.environ, {"VISUAL": "missing-editor"}):
            with patch.object(user_input.subprocess, "run", side_effect=editor):
                self.assertIsNone(user_input.edit_chat_message())
        self.assertFalse(Path(observed["file"]).exists())


class CliIntegrationTests(unittest.TestCase):
    def test_interactive_regular_message_editor_message_and_exit(self):
        # Do not import LangChain, compile a graph, access user files, or persist history.
        import cli

        calls = []
        fake_graph = SimpleNamespace(
            invoke=lambda payload, *, config: (calls.append(payload), {"messages": []})[
                1
            ]
        )
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: fake_graph
        fake_langsmith = ModuleType("langsmith")

        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield

        fake_langsmith.tracing_context = tracing_context
        fake_rl = Mock()
        fake_rl.get_current_history_length.return_value = 1
        with (
            patch.dict(sys.modules, {"agent": fake_agent, "langsmith": fake_langsmith}),
            patch.object(
                cli, "configured_profile_prefix", return_value="/workspace/candidate"
            ),
            patch.object(cli, "configure_readline", return_value=fake_rl),
            patch.object(
                cli,
                "edit_chat_message",
                return_value="Edited first\nEdited second",
            ),
            patch("builtins.input", side_effect=["Hello", "/edit", "quit"]),
            patch("builtins.print"),
        ):
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["messages"][0]["content"], "Hello")
        self.assertEqual(
            calls[1]["messages"][0]["content"], "Edited first\nEdited second"
        )
        fake_rl.add_history.assert_called_once_with("Hello")

    def test_edit_cancel_does_not_submit(self):
        import cli

        calls = []
        fake_graph = SimpleNamespace(
            invoke=lambda payload, *, config: calls.append(payload)
        )
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: fake_graph
        fake_langsmith = ModuleType("langsmith")

        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield

        fake_langsmith.tracing_context = tracing_context
        with (
            patch.dict(sys.modules, {"agent": fake_agent, "langsmith": fake_langsmith}),
            patch.object(
                cli, "configured_profile_prefix", return_value="/workspace/candidate"
            ),
            patch.object(cli, "configure_readline", return_value=None),
        ):
            with patch.object(cli, "edit_chat_message", return_value=None):
                with patch("builtins.input", side_effect=["/edit", "exit"]):
                    with patch("builtins.print"):
                        self.assertEqual(cli.main([]), 0)
        self.assertEqual(calls, [])

    def test_hitl_answer_is_never_added_to_chat_history(self):
        import cli

        class FakeCommand:
            def __init__(self, *, resume):
                self.resume = resume

        class FakeGraph:
            def __init__(self):
                self.calls = []

            def invoke(self, payload, *, config):
                self.calls.append((payload, config))
                if len(self.calls) == 1:
                    return {
                        "__interrupt__": [
                            SimpleNamespace(
                                value={
                                    "action_requests": [
                                        {
                                            "name": "web_search",
                                            "args": {"query": "docs"},
                                        }
                                    ],
                                    "review_configs": [
                                        {
                                            "action_name": "web_search",
                                            "allowed_decisions": ["approve", "reject"],
                                        }
                                    ],
                                }
                            )
                        ]
                    }
                return {"messages": []}

        graph = FakeGraph()
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: graph
        fake_langsmith = ModuleType("langsmith")
        fake_langgraph = ModuleType("langgraph")
        fake_types = ModuleType("langgraph.types")
        fake_types.Command = FakeCommand

        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield

        fake_langsmith.tracing_context = tracing_context
        rl = Mock()
        rl.get_current_history_length.return_value = 1
        original_run = cli._run_to_completion

        def mocked_run(*args, **kwargs):
            return original_run(*args, input_fn=lambda _: "y", **kwargs)

        with (
            patch.dict(
                sys.modules,
                {
                    "agent": fake_agent,
                    "langsmith": fake_langsmith,
                    "langgraph": fake_langgraph,
                    "langgraph.types": fake_types,
                },
            ),
            patch.object(
                cli, "configured_profile_prefix", return_value="/workspace/candidate"
            ),
            patch.object(cli, "configure_readline", return_value=rl),
            patch.object(cli, "_run_to_completion", side_effect=mocked_run),
            patch(
                "builtins.input",
                side_effect=["Search documentation", "exit"],
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(len(graph.calls), 2)
        self.assertEqual(graph.calls[1][0].resume["decisions"], [{"type": "approve"}])
        self.assertEqual(
            graph.calls[0][1]["configurable"]["thread_id"],
            graph.calls[1][1]["configurable"]["thread_id"],
        )
        rl.add_history.assert_called_once_with("Search documentation")

    def test_single_message_skips_readline_and_editor(self):
        import cli

        calls = []
        fake_graph = SimpleNamespace(
            invoke=lambda payload, *, config: (calls.append(payload), {"messages": []})[
                1
            ]
        )
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: fake_graph
        fake_langsmith = ModuleType("langsmith")

        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield

        fake_langsmith.tracing_context = tracing_context
        with (
            patch.dict(sys.modules, {"agent": fake_agent, "langsmith": fake_langsmith}),
            patch.object(
                cli, "configured_profile_prefix", return_value="/workspace/candidate"
            ),
            patch.object(cli, "configure_readline") as setup,
        ):
            with patch.object(cli, "edit_chat_message") as editor:
                with patch("builtins.print"):
                    self.assertEqual(cli.main(["--message", "one shot"]), 0)
        self.assertEqual(len(calls), 1)
        setup.assert_not_called()
        editor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
