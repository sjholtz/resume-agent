"""CLI tracing and HITL tests for the v3/v4b profile-review workflow.

These are offline tests. They verify the CLI's approval decisions and fake-graph
resumption. They do not replace the real Guix smoke tests or filesystem fixtures.
"""

import contextlib
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from profile_cancel_guard import ProfileCancelGuard
from profile_receipts import REGISTRY
from write_decision_audit import WriteDecisionAudit


class FakeCommand:
    def __init__(self, *, resume):
        self.resume = resume


class InterruptGraph:
    def __init__(self, path):
        self.payloads = []
        self.path = path

    def invoke(self, payload, *, config):
        self.payloads.append((payload, config))
        if len(self.payloads) == 1:
            return {
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {
                                    "name": "write_file",
                                    "args": {"file_path": self.path, "content": "test"},
                                }
                            ],
                            "review_configs": [
                                {
                                    "action_name": "write_file",
                                    "allowed_decisions": ["approve", "reject"],
                                }
                            ],
                        }
                    )
                ]
            }
        return {"messages": [{"role": "assistant", "content": "Completed"}]}


class CliTests(unittest.TestCase):
    def test_default_trace_off_and_opt_in(self):
        contexts = []

        @contextlib.contextmanager
        def tracing_context(**kwargs):
            contexts.append(kwargs)
            yield

        fake_langsmith = ModuleType("langsmith")
        fake_langsmith.tracing_context = tracing_context
        fake_agent = ModuleType("agent")
        fake_agent.make_graph = lambda: SimpleNamespace(
            invoke=lambda payload, *, config: {
                "messages": [{"role": "assistant", "content": "Done"}]
            }
        )
        # The CLI debug renderer imports these real types inside run_once().
        # Provide both names in the stub, even though this fake graph returns dicts.
        fake_messages = ModuleType("langchain_core.messages")
        fake_messages.AIMessage = type("AIMessage", (), {})
        fake_messages.ToolMessage = type("ToolMessage", (), {})

        with (
            patch.dict(
                sys.modules,
                {
                    "langsmith": fake_langsmith,
                    "agent": fake_agent,
                    "langchain_core.messages": fake_messages,
                },
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(cli.main(["--message", "hello"]), 0)
            self.assertEqual(cli.main(["--trace", "--message", "hello"]), 0)
        self.assertEqual([x["enabled"] for x in contexts], [False, True])

    def test_trace_project_requires_trace(self):
        with patch("builtins.print"):
            self.assertEqual(cli.main(["--trace-project", "myproject"]), 2)

    def test_profile_write_requires_two_separate_consents(self):
        # Real v3 profile review checks a real on-disk snapshot and refuses
        # arbitrary missing paths. Use a disposable fixture, not /workspace.
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "candidate"
            prefix.mkdir()
            path = prefix / "SMOKE_PROFILE.md"
            original = "Fictional candidate profile.\n"
            path.write_text(original, encoding="utf-8")
            request = {
                "action_requests": [
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": str(path),
                            "old_string": "Fictional candidate profile.",
                            "new_string": "Fictional candidate profile.\n\nREMOTE",
                        },
                    },
                    {"name": "web_search", "args": {"query": "company"}},
                ],
                "review_configs": [
                    {
                        "action_name": "edit_file",
                        "allowed_decisions": ["approve", "edit", "reject"],
                    },
                    {
                        "action_name": "web_search",
                        "allowed_decisions": ["approve", "reject"],
                    },
                ],
            }
            thread_id = "test-profile-approval"
            REGISTRY.begin_turn(thread_id)
            answers = iter(["a", "y", "n"])  # content, tool, then web search
            with contextlib.redirect_stdout(StringIO()):
                result = cli._prompt_approvals(
                    request,
                    profile=str(prefix),
                    thread_id=thread_id,
                    input_fn=lambda _: next(answers),
                )
            self.assertEqual(result["decisions"][0], {"type": "approve"})
            self.assertEqual(result["decisions"][1]["type"], "reject")
            self.assertEqual(REGISTRY.unreported(thread_id), 1)
            # Approval alone does NOT execute the edit.
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_candidate_decline_never_approves_tool(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "candidate"
            prefix.mkdir()
            path = prefix / "SMOKE_PROFILE.md"
            original = "Fictional candidate profile.\n"
            path.write_text(original, encoding="utf-8")
            request = {
                "action_requests": [
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": str(path),
                            "content": original + "REMOTE\n",
                        },
                    }
                ],
                "review_configs": [
                    {
                        "action_name": "write_file",
                        "allowed_decisions": ["approve", "edit", "reject"],
                    }
                ],
            }
            guard = ProfileCancelGuard()
            with contextlib.redirect_stdout(StringIO()):
                # 'r' means Reject; 'n' now means New proposal, NOT No.
                result = cli._prompt_approvals(
                    request,
                    profile=str(prefix),
                    guard=guard,
                    input_fn=lambda _: "r",
                )
            self.assertEqual(result["decisions"][0]["type"], "reject")
            self.assertTrue(guard.cancelled)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_candidate_approves_content_but_denies_execution(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "candidate"
            prefix.mkdir()
            path = prefix / "SMOKE_PROFILE.md"
            original = "Fictional candidate profile.\n"
            path.write_text(original, encoding="utf-8")
            request = {
                "action_requests": [
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": str(path),
                            "content": original + "REMOTE\n",
                        },
                    }
                ],
                "review_configs": [
                    {
                        "action_name": "write_file",
                        "allowed_decisions": ["approve", "edit", "reject"],
                    }
                ],
            }
            answers = iter(["a", "n"])
            guard = ProfileCancelGuard()
            with contextlib.redirect_stdout(StringIO()):
                result = cli._prompt_approvals(
                    request,
                    profile=str(prefix),
                    guard=guard,
                    input_fn=lambda _: next(answers),
                )
            self.assertEqual(result["decisions"][0]["type"], "reject")
            self.assertTrue(guard.cancelled)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_new_proposal_is_not_a_cancellation(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "candidate"
            prefix.mkdir()
            path = prefix / "SMOKE_PROFILE.md"
            original = "Fictional candidate profile.\n"
            path.write_text(original, encoding="utf-8")
            request = {
                "action_requests": [
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": str(path),
                            "content": original + "REMOTE\n",
                        },
                    }
                ],
                "review_configs": [
                    {
                        "action_name": "write_file",
                        "allowed_decisions": ["approve", "edit", "reject"],
                    }
                ],
            }
            answers = iter(["n", "Preference should be local"])
            guard = ProfileCancelGuard()
            with contextlib.redirect_stdout(StringIO()):
                result = cli._prompt_approvals(
                    request,
                    profile=str(prefix),
                    guard=guard,
                    input_fn=lambda _: next(answers),
                )
            self.assertEqual(result["decisions"][0]["type"], "reject")
            self.assertIn(
                "Preference should be local", result["decisions"][0]["message"]
            )
            self.assertFalse(guard.cancelled)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_interrupt_resume_uses_same_thread_and_rejects_by_default(self):
        fake_langgraph = ModuleType("langgraph")
        fake_types = ModuleType("langgraph.types")
        fake_types.Command = FakeCommand
        graph = InterruptGraph("/workspace/applications/resume.md")
        cfg = {"configurable": {"thread_id": "abc"}}
        audit = WriteDecisionAudit()
        with (
            patch.dict(
                sys.modules,
                {"langgraph": fake_langgraph, "langgraph.types": fake_types},
            ),
            patch("builtins.print"),
        ):
            result = cli._run_to_completion(
                graph,
                {"messages": []},
                config=cfg,
                profile="/workspace/candidate",
                write_audit=audit,
                input_fn=lambda _: "",
            )
        self.assertEqual(result["messages"][-1]["content"], "Completed")
        self.assertIs(graph.payloads[1][1], cfg)
        self.assertIsInstance(graph.payloads[1][0], FakeCommand)
        self.assertEqual(graph.payloads[1][0].resume["decisions"][0]["type"], "reject")
        self.assertEqual(len(audit.denied), 1)

    def test_missing_review_configs_fail_closed(self):
        request = {
            "action_requests": [
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/workspace/applications/result.md",
                        "content": "test",
                    },
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "review_configs"):
            cli._prompt_approvals(request, profile="/workspace/candidate")

    def test_mismatched_review_configs_fail_closed(self):
        request = {
            "action_requests": [
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/workspace/applications/result.md",
                        "content": "test",
                    },
                }
            ],
            "review_configs": [],
        }
        with self.assertRaisesRegex(ValueError, "review_configs"):
            cli._prompt_approvals(request, profile="/workspace/candidate")


if __name__ == "__main__":
    unittest.main()
