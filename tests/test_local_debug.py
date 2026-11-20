"""Offline regression tests for the opt-in, privacy-minimal callback tracer."""
import contextlib
import json
import os
import stat
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from local_debug import LocalDebugSink, _task_agent, make_callback_handler


class LocalDebugTests(unittest.TestCase):
    def test_nested_delegation_and_tool_results_are_live_and_redacted(self):
        text = StringIO()
        sink = LocalDebugSink(stream=text)
        sink.start_turn()
        sink.chain_start({"name": "orchestrator"}, "root", None, "orchestrator")
        sink.tool_start({"name": "task"}, '{"subagent_type":"application-agent","description":"PRIVATE CANDIDATE"}',
                        None, "delegation", "root", None)
        sink.chain_start({"name": "agent"}, "child", "delegation", "agent")
        sink.tool_start({"name": "initialize_application"}, "PRIVATE posting URL and resume",
                        None, "tool", "child", None)
        sink.tool_end(SimpleNamespace(status="success", content="PRIVATE TOOL RESPONSE"), "tool")
        sink.chain_end("child")
        sink.tool_end("PRIVATE subagent response", "delegation")
        sink.end_turn("returned_unverified")
        output = text.getvalue()
        self.assertIn("delegate.start agent=application-agent", output)
        self.assertIn("tool.start agent=application-agent tool=initialize_application", output)
        self.assertIn("tool.end agent=application-agent tool=initialize_application outcome=returned_unverified", output)
        self.assertIn("delegations=1", output)
        for secret in ("PRIVATE", "posting URL", "resume", "TOOL RESPONSE"):
            self.assertNotIn(secret, output)

    def test_error_does_not_print_sensitive_exception(self):
        stream = StringIO()
        sink = LocalDebugSink(stream=stream)
        sink.tool_start({"name":"read_web_page"}, "PRIVATE", None, "r1", None, None)
        sink.tool_error(RuntimeError("access_token=SECRET!"), "r1")
        self.assertIn("tool.error", stream.getvalue())
        self.assertIn("error_type=RuntimeError", stream.getvalue())
        self.assertNotIn("SECRET", stream.getvalue())
        self.assertEqual(sink.tool_errors, 1)

    def test_known_role_only_and_non_json_task_inputs(self):
        self.assertEqual(_task_agent({}, "{'subagent_type': 'technical-review-agent'}"),
                         "technical-review-agent")
        self.assertIsNone(_task_agent({"subagent_type": "PRIVATE"}, ""))
        self.assertIsNone(_task_agent({}, "x" * 30000))
        self.assertIsNone(_task_agent({"subagent_type": []}, ""))

    def test_secure_file_permissions_jsonl_and_append(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            for _ in range(2):
                sink = LocalDebugSink(path=path, stream=StringIO())
                try:
                    sink.start_turn()
                    sink.end_turn("returned_unverified")
                finally:
                    sink.close()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            lines = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(lines), 4)
            self.assertEqual([x["event"] for x in lines],
                             ["turn.start", "turn.end", "turn.start", "turn.end"])
            self.assertTrue(all("timestamp" in line for line in lines))

    def test_secure_log_rejects_symlink_and_nonregular(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("unchanged")
            link = Path(tmp) / "alias"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                LocalDebugSink(path=link, stream=StringIO())
            self.assertEqual(target.read_text(), "unchanged")
            with self.assertRaises(OSError):
                LocalDebugSink(path=Path(tmp), stream=StringIO())

    def test_callbacks_capture_nested_tool_events_not_model_content(self):
        class FakeBase:
            pass
        fake_callbacks = ModuleType("langchain_core.callbacks")
        fake_callbacks.BaseCallbackHandler = FakeBase
        with patch.dict(sys.modules, {"langchain_core.callbacks": fake_callbacks}):
            sink = LocalDebugSink(stream=StringIO())
            handler = make_callback_handler(sink)
            handler.on_chain_start({"name":"orchestrator"}, {"private":"SECRET"},run_id="root")
            handler.on_tool_start({"name":"task"}, '{"subagent_type":"recruiting-ats-review-agent","description":"SECRET"}',
                                  run_id="task", parent_run_id="root")
            handler.on_tool_start({"name":"read_file"}, "SECRET", run_id="read",parent_run_id="task")
            handler.on_tool_end("SECRET", run_id="read")
            handler.on_tool_end("SECRET", run_id="task")
            handler.on_llm_start({}, ["SECRET"], run_id="model", parent_run_id="root")
            handler.on_llm_end("SECRET", run_id="model")
            actual = sink.stream.getvalue()
            self.assertIn("delegate.start agent=recruiting-ats-review-agent", actual)
            self.assertIn("tool.start agent=recruiting-ats-review-agent tool=read_file", actual)
            self.assertNotIn("SECRET", actual)

    def test_no_debug_by_default_but_debug_file_enables_events(self):
        @contextlib.contextmanager
        def tracing_context(**kwargs):
            self.assertFalse(kwargs["enabled"])
            yield
        fake_langsmith = ModuleType("langsmith")
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: SimpleNamespace()
        def fake_run(_graph, _payload, **kwargs):
            self.assertIsNotNone(kwargs["local_debug"])
            return {"messages": [SimpleNamespace(type="ai", content="Done", tool_calls=[])]}
        with TemporaryDirectory() as tmp, patch.dict(sys.modules, {"langsmith":fake_langsmith,"agent":fake_agent}):
            log = Path(tmp)/"debug.jsonl"
            with patch.object(cli,"_run_to_completion", fake_run):
                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                    self.assertEqual(cli.main(["--debug-file",str(log),"--message","hi"]), 0)
            records = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([x["event"] for x in records], ["turn.start", "turn.end"])
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)

    def test_callbacks_preserved_across_interrupt_resume_and_capture_decisions(self):
        class FakeBase:
            pass
        fake_callbacks = ModuleType("langchain_core.callbacks")
        fake_callbacks.BaseCallbackHandler = FakeBase
        fake_types = ModuleType("langgraph.types")
        class FakeCommand:
            def __init__(self, *, resume):
                self.resume = resume
        fake_types.Command = FakeCommand
        class FakeGraph:
            def __init__(self):
                self.calls = []
            def invoke(self, payload, *, config):
                self.calls.append(config)
                self_outer.assertIn("callbacks", config)
                callback = config["callbacks"][-1]
                callback.on_tool_start({"name":"web_search"}, "PRIVATE search",run_id=f"tool{len(self.calls)}")
                callback.on_tool_end("PRIVATE result",run_id=f"tool{len(self.calls)}")
                if len(self.calls) == 1:
                    return {"__interrupt__":[SimpleNamespace(value={
                        "action_requests":[{"name":"web_search","args":{"query":"SECRET"}}],
                        "review_configs":[{"action_name":"web_search", "allowed_decisions":["approve","reject"]}],
                    })]}
                return {"messages": []}
        self_outer = self
        trace = StringIO()
        sink = LocalDebugSink(stream=trace)
        graph = FakeGraph()
        with patch.dict(sys.modules, {"langchain_core.callbacks":fake_callbacks,"langgraph.types":fake_types}):
            with contextlib.redirect_stdout(StringIO()):
                outcome = cli._run_to_completion(
                    graph, {"messages":[]}, config={"configurable":{"thread_id":"T"}},
                    profile="/workspace/candidate", input_fn=lambda _: "n",
                    local_debug=sink)
        self.assertEqual(outcome, {"messages":[]})
        self.assertIs(graph.calls[0]["callbacks"][-1], graph.calls[1]["callbacks"][-1])
        self.assertIn("graph.resumed", trace.getvalue())
        self.assertIn("approval.decision tool=web_search decision=rejected", trace.getvalue())
        self.assertNotIn("SECRET", trace.getvalue())

    def test_cli_debug_captures_live_callbacks_with_trace_disabled(self):
        class FakeBase:
            pass
        fake_callbacks = ModuleType("langchain_core.callbacks")
        fake_callbacks.BaseCallbackHandler = FakeBase
        class FakeGraph:
            def invoke(self, payload, *, config):
                handlers = config.get("callbacks", [])
                self_outer.assertEqual(len(handlers), 1)
                handler = handlers[0]
                handler.on_chain_start({"name":"orchestrator"}, {}, run_id="root")
                handler.on_tool_start({"name":"task"},
                                      '{"subagent_type":"application-agent","description":"PRIVATE"}',
                                      run_id="deleg", parent_run_id="root")
                handler.on_tool_start({"name":"prepare_latex_documents"}, "PRIVATE",
                                      run_id="write", parent_run_id="deleg")
                handler.on_tool_end("PRIVATE", run_id="write")
                handler.on_tool_end("PRIVATE", run_id="deleg")
                return {"messages":[SimpleNamespace(type="ai",content="Done",tool_calls=[])]}
        self_outer = self
        @contextlib.contextmanager
        def tracing_context(**kwargs):
            self.assertFalse(kwargs["enabled"])
            yield
        fake_langsmith = ModuleType("langsmith")
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: FakeGraph()
        err = StringIO()
        with patch.dict(sys.modules, {"langchain_core.callbacks":fake_callbacks,
                                     "langsmith":fake_langsmith,"agent":fake_agent}):
            with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["--debug","--message","PRIVATE user message"]), 0)
        report = err.getvalue()
        self.assertIn("delegate.start agent=application-agent", report)
        self.assertIn("tool.start agent=application-agent tool=prepare_latex_documents", report)
        self.assertIn("graph.returned", report)
        self.assertNotIn("PRIVATE", report)

    def test_cli_rejects_debug_file_in_candidate_tree(self):
        @contextlib.contextmanager
        def tracing_context(**kwargs):
            yield
        fake_langsmith = ModuleType("langsmith")
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: SimpleNamespace()
        with TemporaryDirectory() as tmp, patch.dict(sys.modules, {"langsmith": fake_langsmith,
                                                               "agent": fake_agent}):
            target = Path(tmp) / "candidate" / "log.jsonl"
            target.parent.mkdir()
            original = Path.cwd()
            try:
                os.chdir(tmp)
                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                    self.assertEqual(cli.main(["--debug-file", str(target), "--message", "hello"]), 2)
            finally:
                os.chdir(original)
            self.assertFalse(target.exists())

    def test_no_file_created_when_flag_is_absent(self):
        # A sink with no destination writes stderr only and cannot persist.
        with TemporaryDirectory() as tmp:
            stream = StringIO()
            sink = LocalDebugSink(stream=stream)
            sink.emit("turn.start")
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
