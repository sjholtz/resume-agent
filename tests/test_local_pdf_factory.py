"""Agent-facing local PDF tool is bound to its workspace, not model paths."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pymupdf


class LocalPDFFactoryTests(unittest.TestCase):
    def test_created_tool_has_expected_name_and_is_workspace_bound(self):
        # A fake decorator exercises the factory even in an offline test
        # environment without the full LangChain runtime.
        tools_module = ModuleType("langchain_core.tools")

        class FakeTool:
            def __init__(self, callback):
                self.callback = callback
                self.name = callback.__name__

            def invoke(self, values):
                return self.callback(**values)

        tools_module.tool = lambda fn: FakeTool(fn)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "candidate/evidence/sources/documents/example.pdf"
            path.parent.mkdir(parents=True)
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "LOCAL_SOURCE_READ_TOKEN")
            path.write_bytes(pdf.tobytes())
            pdf.close()
            fake_core = ModuleType("langchain_core")
            with patch.dict(
                sys.modules,
                {
                    "langchain_core": fake_core,
                    "langchain_core.tools": tools_module,
                },
            ):
                previous = sys.modules.pop("tools.read_local_pdf", None)
                try:
                    reader = importlib.import_module("tools.read_local_pdf")
                    local_tool = reader.make_read_local_pdf_tool(
                        workspace_root=root,
                        candidate_profile="/workspace/candidate",
                    )
                    self.assertEqual(local_tool.name, "read_local_pdf")
                    result = local_tool.invoke(
                        {
                            "file_path": "/workspace/candidate/evidence/sources/documents/example.pdf",
                        }
                    )
                    self.assertTrue(result["ok"], result)
                    self.assertIn(
                        "LOCAL_SOURCE_READ_TOKEN", result["pages"][0]["content"]
                    )
                    self.assertFalse(
                        local_tool.invoke({"file_path": "/etc/passwd"})["ok"]
                    )
                finally:
                    sys.modules.pop("tools.read_local_pdf", None)
                    if previous is not None:
                        sys.modules["tools.read_local_pdf"] = previous


if __name__ == "__main__":
    unittest.main()
