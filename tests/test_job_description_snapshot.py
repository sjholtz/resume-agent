"""Offline tests of the complete-public-page extraction; no network requests."""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application_workspace import ApplicationWorkspaceError
from tools import job_description_snapshot


class JobDescriptionSnapshotTests(unittest.TestCase):
    def _fake_modules(self, *, content, html_fallback=""):
        fake_trafilatura = ModuleType("trafilatura")
        fake_trafilatura.bare_extraction = lambda *a, **k: SimpleNamespace(text=content)
        fake_trafilatura.html2txt = lambda *a, **k: html_fallback
        fake_settings = ModuleType("trafilatura.settings")
        fake_settings.DEFAULT_CONFIG = {"DEFAULT": {}}
        return {"trafilatura": fake_trafilatura, "trafilatura.settings": fake_settings}

    def test_complete_extraction_is_not_chunked(self):
        from tools import web_fetch
        content = "Requirements and duties: " + "Build excellent APIs. " * 3500
        response = SimpleNamespace(body=b"<html>example</html>",
                                   final_url="https://jobs.example.org/careers/senior", content_type="text/html")
        with patch.dict(sys.modules, self._fake_modules(content=content)):
            with patch.object(web_fetch, "download_html", return_value=response) as mock_download:
                result = job_description_snapshot.fetch_job_description("https://example.org/jobs/1")
        self.assertEqual(result.text, content.strip())
        self.assertGreater(len(result.text), 30_000)
        self.assertEqual(result.final_url, response.final_url)
        self.assertEqual(result.extraction, "main_content")
        mock_download.assert_called_once_with("https://example.org/jobs/1")

    def test_empty_or_js_only_page_fails(self):
        from tools import web_fetch
        response = SimpleNamespace(body=b"<html>Log in</html>", final_url="https://example.org/jobs/2")
        with patch.dict(sys.modules, self._fake_modules(content="", html_fallback="Log in")):
            with patch.object(web_fetch, "download_html", return_value=response):
                with self.assertRaisesRegex(ApplicationWorkspaceError, "too little readable"):
                    job_description_snapshot.fetch_job_description("https://example.org/jobs/2")

    def test_oversize_page_fails_closed(self):
        from tools import web_fetch
        response = SimpleNamespace(body=b"<html>text</html>", final_url="https://example.org/jobs/3")
        with patch.dict(sys.modules, self._fake_modules(content="a" * 150_001)):
            with patch.object(web_fetch, "download_html", return_value=response):
                with self.assertRaisesRegex(ApplicationWorkspaceError, "unusually large"):
                    job_description_snapshot.fetch_job_description("https://example.org/jobs/3")


if __name__ == "__main__":
    unittest.main()
