"""Offline tests: python -m unittest discover -s tests -v"""

import socket
import unittest
from unittest.mock import patch

import tools.web_fetch

PUBLIC_IP = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
MIXED_IP = PUBLIC_IP + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


class FakeResponse:
    def __init__(
        self, *, status=200, content_type="text/html", body=b"OK", headers=None
    ):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise web_fetch.requests.exceptions.HTTPError("Bad HTTP status")

    def iter_content(self, chunk_size):
        yield from (
            self.body[i : i + chunk_size] for i in range(0, len(self.body), chunk_size)
        )


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs["timeout"][0] > 0
        return next(self.responses)


class PublicURLTests(unittest.TestCase):
    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_public_url_allowed(self, _):
        tools.web_fetch.validate_public_url("https://example.com/path?q=1")

    def test_localhost_denied_without_dns(self):
        for url in ["http://localhost/", "http://127.0.0.1/", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                tools.web_fetch.validate_public_url(url)

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=MIXED_IP)
    def test_mixed_dns_response_denied(self, _):
        with self.assertRaisesRegex(ValueError, "non-public"):
            tools.web_fetch.validate_public_url("https://example.com")

    def test_bad_ports_and_credentials_denied(self):
        for url in [
            "https://example.com:444/",
            "https://u:p@example.com/",
            "https://example.com:bad/",
            "https://example.com\\@localhost/",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                tools.web_fetch.validate_public_url(url)

    def test_embedded_whitespace_denied(self):
        for url in ["https://example.com/a b", "\nhttps://example.com"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                tools.web_fetch.validate_public_url(url)


class DownloadTests(unittest.TestCase):
    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_html_response(self, _):
        fake = FakeSession([FakeResponse(body=b"<html>Hello</html>")])
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            result = tools.web_fetch.download_html("https://example.com")
        self.assertEqual(result.body, b"<html>Hello</html>")
        self.assertEqual(result.final_url, "https://example.com")
        self.assertFalse(fake.trust_env)

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_relative_redirect(self, _):
        fake = FakeSession(
            [
                FakeResponse(status=302, headers={"Location": "/final"}),
                FakeResponse(body=b"<html>done</html>"),
            ]
        )
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            result = tools.web_fetch.download_html("https://example.com/start")
        self.assertEqual(result.final_url, "https://example.com/final")
        self.assertEqual(len(fake.urls), 2)

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_private_redirect_rejected(self, _):
        fake = FakeSession(
            [
                FakeResponse(
                    status=302, headers={"Location": "http://127.0.0.1/secret"}
                ),
            ]
        )
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            with self.assertRaises(ValueError):
                tools.web_fetch.download_html("https://example.com")
        self.assertEqual(len(fake.urls), 1)

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_pdf_octet_stream_and_signature(self, _):
        fake = FakeSession(
            [
                FakeResponse(
                    content_type="application/octet-stream", body=b"%PDF-1.7\n..."
                ),
            ]
        )
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            result = tools.web_fetch.download_pdf("https://example.com/a.pdf")
        self.assertTrue(result.body.startswith(b"%PDF-"))

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_pdf_rejects_invalid_magic(self, _):
        fake = FakeSession(
            [FakeResponse(content_type="application/pdf", body=b"not pdf")]
        )
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            with self.assertRaisesRegex(ValueError, "PDF header"):
                tools.web_fetch.download_pdf("https://example.com/a.pdf")

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_html_rejects_wrong_type(self, _):
        fake = FakeSession([FakeResponse(content_type="application/pdf")])
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            with self.assertRaisesRegex(ValueError, "content type"):
                tools.web_fetch.download_html("https://example.com/document.pdf")

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_declared_length_limit(self, _):
        fake = FakeSession([FakeResponse(headers={"Content-Length": "99999999"})])
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            with self.assertRaisesRegex(ValueError, "download limit"):
                tools.web_fetch.download_html("https://example.com")

    @patch.object(tools.web_fetch.socket, "getaddrinfo", return_value=PUBLIC_IP)
    def test_streamed_length_limit(self, _):
        fake = FakeSession(
            [FakeResponse(body=b"x" * (tools.web_fetch.HTML_DOWNLOAD_LIMIT + 1))]
        )
        with patch.object(tools.web_fetch.requests, "Session", return_value=fake):
            with self.assertRaisesRegex(ValueError, "download limit"):
                tools.web_fetch.download_html("https://example.com")


if __name__ == "__main__":
    unittest.main()
