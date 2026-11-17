"""Offline tests for the bounded, read-only local candidate PDF extractor."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from tools.local_pdf_core import MAX_PDF_BYTES, extract_local_pdf

VIRTUAL = "/workspace/candidate/evidence/sources/documents/thesis.pdf"


class LocalPDFTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.docs = self.workspace / "candidate/evidence/sources/documents"
        self.docs.mkdir(parents=True)
        self.pdf = self.docs / "thesis.pdf"
        document = pymupdf.open()
        for i in range(3):
            page = document.new_page(width=612, height=2200)
            lines = [f"TOKEN_PAGE_{i + 1} information retrieval"]
            lines.extend(
                f"Line {j:03d} retrieval evaluation relevance precision recall"
                for j in range(110)
            )
            page.insert_text((40, 40), "\n".join(lines), fontsize=9)
        self.pdf.write_bytes(document.tobytes())
        document.close()

    def read(self, file_path=VIRTUAL, **kwargs):
        return extract_local_pdf(
            file_path,
            workspace_root=self.workspace,
            candidate_profile="/workspace/candidate",
            **kwargs,
        )

    def test_first_page_hash_and_numbering(self):
        result = self.read(start_page=2, max_pages=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(
            result["sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        )
        self.assertEqual(result["pages"][0]["page"], 2)
        self.assertIn("TOKEN_PAGE_2", result["pages"][0]["content"])
        self.assertNotIn("TOKEN_PAGE_1", result["pages"][0]["content"])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_start_page"], 3)

    def test_character_continuation_preserves_hash_and_no_overlap(self):
        a = self.read(max_pages=3, max_chars=1000)
        self.assertTrue(a["ok"], a)
        self.assertTrue(a["has_more"])
        b = self.read(
            max_pages=3,
            max_chars=1000,
            start_page=a["next_start_page"],
            start_char=a["next_start_char"],
        )
        self.assertTrue(b["ok"], b)
        self.assertEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["pages"][-1]["end_char"], b["pages"][0]["start_char"])
        self.assertEqual(len(a["pages"][-1]["content"]), 1000)
        self.assertNotEqual(a["pages"][-1]["content"], b["pages"][0]["content"])

    def test_missing_text_reports_ocr_without_hallucinating_content(self):
        document = pymupdf.open()
        document.new_page()
        (self.docs / "image.pdf").write_bytes(document.tobytes())
        document.close()
        result = self.read(VIRTUAL.replace("thesis.pdf", "image.pdf"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_ocr"])
        self.assertEqual(result["pages_without_text"], [1])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["extracted_chars"], 0)

    def test_corrupt_pdf_is_not_read_as_text(self):
        (self.docs / "bad.pdf").write_bytes(b"%PDF-not-a-real-document")
        result = self.read(VIRTUAL.replace("thesis.pdf", "bad.pdf"))
        self.assertFalse(result["ok"])

    def test_pdf_signature_checked(self):
        (self.docs / "fake.pdf").write_bytes(b"junk")
        result = self.read(VIRTUAL.replace("thesis.pdf", "fake.pdf"))
        self.assertFalse(result["ok"])
        self.assertIn("signature", result["error"])

    def test_oversize_rejected_without_parsing(self):
        (self.docs / "large.pdf").write_bytes(b"%PDF-" + b"a" * MAX_PDF_BYTES)
        result = self.read(VIRTUAL.replace("thesis.pdf", "large.pdf"))
        self.assertFalse(result["ok"])
        self.assertIn("limit", result["error"])

    def test_encrypted_pdf_rejected(self):
        document = pymupdf.open()
        document.new_page()
        raw = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner-secret",
            user_pw="secret",
        )
        document.close()
        (self.docs / "locked.pdf").write_bytes(raw)
        result = self.read(VIRTUAL.replace("thesis.pdf", "locked.pdf"))
        self.assertFalse(result["ok"])
        self.assertIn("password-protected", result["error"])

    def test_out_of_range_page_and_character_offset(self):
        self.assertIn("exceeds", self.read(start_page=4)["error"])
        self.assertIn("exceeds", self.read(start_char=100000)["error"])

    def test_invalid_integer_parameters_fail_closed(self):
        cases = (
            {"start_page": True},
            {"start_page": 0},
            {"start_char": -1},
            {"max_pages": 11},
            {"max_pages": False},
            {"max_chars": 999},
            {"max_chars": 30001},
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.read(**case)["ok"])

    def test_path_escape_and_non_pdf_rejected(self):
        forbidden = [
            "/etc/passwd",
            "/workspace/candidate/profile/secret.pdf",
            "/workspace/applications/my.pdf",
            "/workspace/candidate/evidence/sources/documents/../secret.pdf",
            "/workspace/candidate/evidence/sources/documents/./thesis.pdf",
            "/workspace/candidate/evidence/sources/documents//thesis.pdf",
            "/workspace/candidate/evidence/sources/documents/thesis.txt",
            "candidate/evidence/sources/documents/thesis.pdf",
            "/workspace/candidate/evidence/sources/documents/",
        ]
        for path in forbidden:
            with self.subTest(path=path):
                self.assertFalse(self.read(path)["ok"])

    def test_symlink_file_and_directory_blocked(self):
        outside = self.workspace / "outside.pdf"
        outside.write_bytes(self.pdf.read_bytes())
        (self.docs / "link.pdf").symlink_to(outside)
        self.assertFalse(self.read(VIRTUAL.replace("thesis.pdf", "link.pdf"))["ok"])
        (self.docs / "linked_dir").symlink_to(self.workspace, target_is_directory=True)
        self.assertFalse(
            self.read(VIRTUAL.replace("thesis.pdf", "linked_dir/outside.pdf"))["ok"]
        )

    def test_nonregular_source_blocked(self):
        (self.docs / "fifo.pdf").parent.mkdir(exist_ok=True)
        import os

        os.mkfifo(self.docs / "fifo.pdf")
        result = self.read(VIRTUAL.replace("thesis.pdf", "fifo.pdf"))
        self.assertFalse(result["ok"])
        self.assertIn("regular", result["error"])

    def test_missing_file_is_not_created(self):
        path = self.docs / "missing.pdf"
        result = self.read(VIRTUAL.replace("thesis.pdf", "missing.pdf"))
        self.assertFalse(result["ok"])
        self.assertFalse(path.exists())

    def test_no_network_or_writes(self):
        original = self.pdf.read_bytes()
        with patch(
            "socket.create_connection", side_effect=AssertionError("network used")
        ):
            self.assertTrue(self.read()["ok"])
        self.assertEqual(self.pdf.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.docs.iterdir()), ["thesis.pdf"])

    def test_custom_candidate_prefix(self):
        target = self.workspace / "profiles/primary/evidence/sources/documents"
        target.mkdir(parents=True)
        (target / "thesis.pdf").write_bytes(self.pdf.read_bytes())
        result = extract_local_pdf(
            "/workspace/profiles/primary/evidence/sources/documents/thesis.pdf",
            workspace_root=self.workspace,
            candidate_profile="/workspace/profiles/primary",
            max_pages=1,
        )
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
