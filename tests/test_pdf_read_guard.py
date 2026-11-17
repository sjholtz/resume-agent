"""Regression: Deep Agents PDF file blocks must never reach text-only Ollama."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_guard_with_stubs():
    class Middleware:
        pass

    class Message:
        def __init__(self, *, name, tool_call_id, content, status):
            self.name, self.tool_call_id = name, tool_call_id
            self.content, self.status = content, status

    fake_langchain = ModuleType("langchain")
    fake_agents = ModuleType("langchain.agents")
    fake_middleware = ModuleType("langchain.agents.middleware")
    fake_middleware.AgentMiddleware = Middleware
    fake_core = ModuleType("langchain_core")
    fake_messages = ModuleType("langchain_core.messages")
    fake_messages.ToolMessage = Message
    with patch.dict(
        sys.modules,
        {
            "langchain": fake_langchain,
            "langchain.agents": fake_agents,
            "langchain.agents.middleware": fake_middleware,
            "langchain_core": fake_core,
            "langchain_core.messages": fake_messages,
        },
    ):
        spec = importlib.util.spec_from_file_location(
            "pdf_read_guard_test_subject", ROOT / "pdf_read_guard.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class PdfReadGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_guard_with_stubs()

    def request(
        self,
        name="read_file",
        path="/workspace/candidate/evidence/sources/documents/thesis.pdf",
    ):
        return SimpleNamespace(
            tool_call={
                "name": name,
                "id": "call-123",
                "args": {"file_path": path},
            }
        )

    def test_pdf_read_is_blocked_before_handler_and_returns_text_error(self):
        middleware = self.mod.PdfReadGuardMiddleware()
        calls = []

        def handler(request):
            calls.append(request)
            return "executed"

        result = middleware.wrap_tool_call(self.request(), handler)
        self.assertEqual(calls, [])
        self.assertEqual(result.name, "read_file")
        self.assertEqual(result.tool_call_id, "call-123")
        self.assertEqual(result.status, "error")
        self.assertIsInstance(result.content, str)
        self.assertIn("read_local_pdf", result.content)
        self.assertIn("NOT read", result.content)

    def test_case_insensitive_extension(self):
        result = self.mod.PdfReadGuardMiddleware().wrap_tool_call(
            self.request(path="/workspace/my.THESIS.PDF"),
            lambda _r: self.fail("must not execute PDF read"),
        )
        self.assertEqual(result.status, "error")

    def test_read_text_unaffected(self):
        value = self.mod.PdfReadGuardMiddleware().wrap_tool_call(
            self.request(path="/workspace/candidate/profile/candidate.md"),
            lambda req: "text result",
        )
        self.assertEqual(value, "text result")

    def test_read_local_pdf_unaffected(self):
        value = self.mod.PdfReadGuardMiddleware().wrap_tool_call(
            self.request(name="read_local_pdf"),
            lambda req: {"ok": True},
        )
        self.assertEqual(value, {"ok": True})

    def test_non_read_action_unaffected(self):
        value = self.mod.PdfReadGuardMiddleware().wrap_tool_call(
            self.request(name="edit_file"),
            lambda req: "defer to permissions",
        )
        self.assertEqual(value, "defer to permissions")

    def test_nonstandard_args_do_not_crash(self):
        request = SimpleNamespace(
            tool_call={"name": "read_file", "id": "x", "args": []}
        )
        value = self.mod.PdfReadGuardMiddleware().wrap_tool_call(
            request, lambda req: "normal"
        )
        self.assertEqual(value, "normal")

    def test_async_pdf_block_does_not_invoke_handler(self):
        async def run():
            async def handler(_req):
                self.fail("must not invoke async handler")

            return await self.mod.PdfReadGuardMiddleware().awrap_tool_call(
                self.request(), handler
            )

        result = asyncio.run(run())
        self.assertEqual(result.status, "error")

    def test_async_nonpdf_delegates(self):
        async def run():
            async def handler(_req):
                return "normal async result"

            return await self.mod.PdfReadGuardMiddleware().awrap_tool_call(
                self.request(path="/workspace/a.txt"), handler
            )

        self.assertEqual(asyncio.run(run()), "normal async result")


if __name__ == "__main__":
    unittest.main()
