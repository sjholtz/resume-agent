"""The Ollama file-block failure must not terminate interactive chat."""

import contextlib
import sys
import unittest
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli


class OneFailureGraph:
    def __init__(self):
        self.thread_ids = []

    def invoke(self, payload, *, config):
        self.thread_ids.append(config["configurable"]["thread_id"])
        if len(self.thread_ids) == 1:
            raise ValueError("Blocks of type file not supported.")
        return {
            "messages": [
                SimpleNamespace(type="ai", content="Recovery worked", tool_calls=[])
            ]
        }


class PdfCrashCLITests(unittest.TestCase):
    def fake_modules(self, graph):
        @contextlib.contextmanager
        def trace(**kwargs):
            yield

        langsmith = ModuleType("langsmith")
        langsmith.tracing_context = trace
        agent = ModuleType("agent")
        agent.make_graph = lambda: graph
        return {"langsmith": langsmith, "agent": agent}

    def test_interactive_error_keeps_cli_alive_and_changes_thread(self):
        graph = OneFailureGraph()
        out, err = StringIO(), StringIO()
        with (
            patch.dict(sys.modules, self.fake_modules(graph)),
            patch.object(cli, "configure_readline", return_value=None),
            patch("builtins.input", side_effect=["first", "second", "quit"]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(len(graph.thread_ids), 2)
        self.assertNotEqual(graph.thread_ids[0], graph.thread_ids[1])
        self.assertIn("Recovery worked", out.getvalue())
        self.assertIn("/quit or /exit", out.getvalue())
        self.assertIn("outcome is unknown", out.getvalue())
        self.assertIn("Blocks of type file not supported", err.getvalue())

    def test_single_message_error_returns_failure_exit_status(self):
        graph = OneFailureGraph()
        out, err = StringIO(), StringIO()
        with (
            patch.dict(sys.modules, self.fake_modules(graph)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(cli.main(["--message", "read the pdf"]), 1)
        self.assertIn("Agent turn failed", err.getvalue())
        self.assertIn("Verify files", out.getvalue())


if __name__ == "__main__":
    unittest.main()
