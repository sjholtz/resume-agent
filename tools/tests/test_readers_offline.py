"""Offline reader tests. Skips readers whose optional packages are unavailable."""

import unittest
from importlib.util import find_spec
from unittest.mock import patch

from web_fetch import DownloadedResource


@unittest.skipUnless(
    find_spec("langchain_core") and find_spec("pymupdf"),
    "Install langchain-core and pymupdf for PDF reader tests",
)
class PDFReaderTests(unittest.TestCase):
    def test_pdf_page_and_character_continuation(self):
        import pymupdf
        from read_web_pdf import read_web_pdf

        doc = pymupdf.open()
        for idx in range(3):
            page = doc.new_page()
            test_line = "word " * 18
            test_page = "\n".join([test_line] * 20)
            page.insert_text((72, 72), "Page " + str(idx + 1) + ": " + test_page)
        pdf = doc.tobytes()
        doc.close()
        fixture = DownloadedResource(
            pdf, "https://example.com/doc.pdf", "application/pdf"
        )
        with patch("read_web_pdf.download_pdf", return_value=fixture):
            first = read_web_pdf.invoke(
                {"url": fixture.final_url, "max_chars": 1000, "max_pages": 3}
            )
            self.assertTrue(first["ok"])
            self.assertTrue(first["has_more"])
            self.assertEqual(first["page_count"], 3)
            second = read_web_pdf.invoke(
                {
                    "url": fixture.final_url,
                    "max_chars": 1000,
                    "max_pages": 3,
                    "start_page": first["next_start_page"],
                    "start_char": first["next_start_char"],
                }
            )
            self.assertTrue(second["ok"])
            self.assertEqual(first["sha256"], second["sha256"])


@unittest.skipUnless(
    find_spec("langchain_core") and find_spec("trafilatura"),
    "Install langchain-core and trafilatura for HTML reader tests",
)
class HTMLReaderTests(unittest.TestCase):
    def test_html_extraction(self):
        from read_web_page import read_web_page

        html = (
            b"<html><head><title>A</title></head><body><article><h1>Hello</h1>"
            + (b"<p>Some relevant article text from an employer.</p>" * 40)
            + b"</article></body></html>"
        )
        fixture = DownloadedResource(html, "https://example.com/post", "text/html")
        with patch("read_web_page.download_html", return_value=fixture):
            result = read_web_page.invoke({"url": fixture.final_url, "max_chars": 1000})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["content"])
        self.assertEqual(result["url"], fixture.final_url)


if __name__ == "__main__":
    unittest.main()
