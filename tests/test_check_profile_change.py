"""Offline test of the strict 8R smoke checker; no Guix or agent required."""

from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Run with PYTHONPATH pointing at the smoke-tests directory.
from check_profile_change import main

BEFORE = (
    b"# FICTIONAL TEST CANDIDATE\n"
    b"Candidate work-arrangement preference: NOT_RECORDED\n"
    b"Disclaimer: fictional data only.\n"
)
MARKER = b"SMOKE-APPEND-REVISION-TEST: revised\n"


class CheckProfileChangeTests(unittest.TestCase):
    def check(self, before: bytes, after: bytes) -> int:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            a, b = path / "before", path / "after"
            a.write_bytes(before)
            b.write_bytes(after)
            args = [
                "check_profile_change.py",
                "--mode",
                "append-revise",
                "--before",
                str(a),
                "--after",
                str(b),
            ]
            with patch.object(sys, "argv", args), redirect_stdout(StringIO()):
                return main()

    def test_exact_single_line_passes(self):
        self.assertEqual(self.check(BEFORE, BEFORE + MARKER), 0)

    def test_current_8r_extra_blank_line_fails(self):
        self.assertEqual(self.check(BEFORE, BEFORE + b"\n" + MARKER), 1)

    def test_multiple_blank_lines_fail(self):
        self.assertEqual(self.check(BEFORE, BEFORE + b"\n\n" + MARKER), 1)

    def test_additional_line_fails(self):
        self.assertEqual(self.check(BEFORE, BEFORE + MARKER + b"extra\n"), 1)

    def test_missing_trailing_newline_fails(self):
        self.assertEqual(self.check(BEFORE, BEFORE + MARKER.rstrip(b"\n")), 1)

    def test_unmodified_baseline_fails(self):
        self.assertEqual(self.check(BEFORE, BEFORE), 1)

    def test_original_contents_changed_fails(self):
        changed = BEFORE.replace(b"NOT_RECORDED", b"remote")
        self.assertEqual(self.check(BEFORE, changed + MARKER), 1)

    def test_no_trailing_newline_in_baseline_supported(self):
        before = BEFORE.rstrip(b"\n")
        self.assertEqual(self.check(before, before + b"\n" + MARKER), 0)


if __name__ == "__main__":
    unittest.main()
