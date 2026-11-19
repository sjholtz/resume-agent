"""Patch B revised: CLI choice must stay within historical document library."""

from __future__ import annotations

import contextlib
from io import StringIO
from pathlib import Path
from types import ModuleType
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import base_documents
import cli


class BaseDocumentTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.lib = self.root / "candidate" / "base-documents"
        self.lib.mkdir(parents=True)
        (self.lib / "backend.tex").write_text("resume text")
        (self.lib / "cover.tex").write_text("cover text")
        (self.lib / "unsupported.pdf").write_text("PDF")
        (self.lib / "notes").mkdir()
        (self.lib / "symlink.tex").symlink_to(self.lib / "backend.tex")

    def test_list_filters_non_tex_and_symlinks(self):
        self.assertEqual(base_documents.list_base_documents(self.root), ["backend.tex", "cover.tex"])

    def test_independent_defaults(self):
        self.assertIsNone(base_documents.resolve_base_document(self.root, None, kind="resume"))
        self.assertEqual(base_documents.resolve_base_document(self.root, "cover.tex", kind="cover"), "cover.tex")

    def test_numeric_picker(self):
        lines = []
        selection = base_documents.resolve_base_document(
            self.root, "?", kind="resume", interactive=True,
            input_fn=lambda prompt: "2", output_fn=lines.append,
        )
        self.assertEqual(selection, "cover.tex")
        self.assertTrue(any("backend.tex" in line for line in lines))

    def test_picker_requires_tty(self):
        with self.assertRaisesRegex(base_documents.BaseDocumentSelectionError, "interactive terminal"):
            base_documents.resolve_base_document(self.root, "?", kind="resume", interactive=False)

    def test_picker_cancel_and_invalid_choice(self):
        for answer in ["", "q", "99", "x"]:
            with self.subTest(answer=answer):
                with self.assertRaises(base_documents.BaseDocumentSelectionError):
                    base_documents.resolve_base_document(
                        self.root, "?", kind="resume", interactive=True, input_fn=lambda _: answer,
                        output_fn=lambda _: None,
                    )

    def test_paths_and_nonexistent_inputs_fail_closed(self):
        for option in ["../backend.tex", "/tmp/backend.tex", "subdir/backend.tex",
                       "missing.tex", "unsupported.pdf", "symlink.tex", "..evil.tex", ".hidden.tex"]:
            with self.subTest(option=option):
                with self.assertRaises(base_documents.BaseDocumentSelectionError):
                    base_documents.resolve_base_document(self.root, option, kind="resume")

    def test_library_symlink_rejected(self):
        other = self.root / "other"
        other.mkdir()
        (self.lib / "backend.tex").unlink()
        (self.lib / "cover.tex").unlink()
        (self.lib / "symlink.tex").unlink()
        # A source-library symlink cannot be followed by CLI or tool.
        # Remove leftover non-TeX entries first.
        for child in list(self.lib.iterdir()):
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        self.lib.rmdir()
        self.lib.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(base_documents.BaseDocumentSelectionError, "symlinks"):
            base_documents.list_base_documents(self.root)

    def test_list_cli_exits_before_graph_or_model_load(self):
        stdout = StringIO()
        with patch.object(cli.Path, "cwd", return_value=self.root), contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["--list-base-documents"]), 0)
        self.assertIn("backend.tex", stdout.getvalue())
        self.assertNotIn("unsupported.pdf", stdout.getvalue())

    def test_cli_pins_selected_sources_to_agent_graph_and_first_turn(self):
        @contextlib.contextmanager
        def fake_tracing(**kwargs):
            yield
        langsmith = ModuleType("langsmith")
        langsmith.tracing_context = fake_tracing
        agent = ModuleType("agent")
        captured = []
        def fake_make_graph(**kwargs):
            captured.append(kwargs)
            class FakeGraph:
                def invoke(self, payload, *, config):
                    captured.append(payload)
                    return {"messages": []}
            return FakeGraph()
        agent.make_graph = fake_make_graph
        output = StringIO()
        with patch.object(cli.Path, "cwd", return_value=self.root), \
             patch.dict(sys.modules, {"agent": agent, "langsmith": langsmith}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cli.main([
                "--base-resume", "backend.tex", "--base-cover", "cover.tex",
                "--message", "Apply using the job description URL",
            ]), 0)
        self.assertEqual(captured[0], {"base_resume": "backend.tex", "base_cover": "cover.tex"})
        submitted = captured[1]["messages"][0]["content"]
        self.assertIn("backend.tex", submitted)
        self.assertIn("cover.tex", submitted)
        self.assertIn("Apply using the job description URL", submitted)

    def test_cli_rejects_noninteractive_picker_without_graph(self):
        with patch.object(cli.Path, "cwd", return_value=self.root), \
             patch.object(cli.sys.stdin, "isatty", return_value=False), \
             contextlib.redirect_stderr(StringIO()) as err:
            self.assertEqual(cli.main(["--base-resume", "?", "--message", "test"]), 2)
        self.assertIn("interactive terminal", err.getvalue())


if __name__ == "__main__":
    unittest.main()
