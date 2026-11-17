"""Shared, bounded HTTP downloader for UNTRUSTED PUBLIC web resources.

Used by read_web_page and read_web_pdf; not by web_search as it uses the
localhost SearXNG client.

Security model: URL and DNS checks are best-effort only. DNS may change
after validation and before Requests connects (TOCTOU / DNS
rebinding). Production systems MUST also enforce outbound network rules
preventing access to private, loopback, link-local, and cloud metadata
addresses. Do not run behind an untrusted proxy. No JavaScript, browser,
credentials, or local file access.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests

MAX_REDIRECTS = 3
HTML_DOWNLOAD_LIMIT = 2_000_000
PDF_DOWNLOAD_LIMIT = 15_000_000
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
PDF_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "binary/octet-stream",
    }
)


@dataclass(frozen=True)
class DownloadedResource:
    body: bytes
    final_url: str
    content_type: str


def validate_public_url(url: str) -> None:
    """Reject malformed, credential-bearing, and non-public HTTP(S) URLs.

    Validates every DNS A/AAAA result, not only the first one. This is NOT a
    network sandbox: enforce egress protections independently.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a nonempty string")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("URL contains whitespace or control characters")

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Malformed URL or invalid port") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Only absolute http:// and https:// URLs are supported")
    if "\\" in parts.netloc:
        raise ValueError("Backslashes in the URL authority are not allowed")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs containing credentials are not allowed")
    if port not in {None, 80, 443}:
        raise ValueError("Only standard HTTP/HTTPS ports (80, 443) are allowed")

    hostname = parts.hostname.rstrip(".").lower()
    if (
        not hostname
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("Local or internal hosts are not allowed")

    # Reject literal private addresses before DNS resolution as a separate
    # defense. DNS validation still applies to hostnames and aliases.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError("Destination is a non-public IP address")

    effective_port = port or (443 if parts.scheme == "https" else 80)
    try:
        answers = socket.getaddrinfo(hostname, effective_port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValueError("Could not resolve the destination host") from exc

    if not answers:
        raise ValueError("Destination host resolved to no addresses")
    for record in answers:
        address = record[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Destination resolved to an invalid IP address") from exc
        if not ip.is_global:
            raise ValueError("Destination resolves to a non-public IP address")


def _download(
    url: str,
    *,
    accepted_types: frozenset[str],
    accept_header: str,
    max_bytes: int,
    timeout: tuple[int, int],
    resource_name: str,
    require_content_type: bool,
    pdf_signature: bool,
) -> DownloadedResource:
    """Validate each redirect, stream within a byte limit, and return bytes."""
    current_url = url
    headers = {
        "User-Agent": "CareerResearchAgent/1.0 (public-resource-reader)",
        "Accept": accept_header,
    }

    # Explicitly use the configured proxy and CA bundle. Keep
    # trust_env=False so unrelated environment settings or NO_PROXY
    # cannot silently bypass it.
    http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
    https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    proxies = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    ca_bundle = (
        os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True
    )

    with requests.Session() as session:
        # Avoid accidentally using proxy credentials or redirecting through
        # enterprise/environment proxies that bypass our destination checks.
        session.trust_env = False
        for hop in range(MAX_REDIRECTS + 1):
            validate_public_url(current_url)
            with session.get(
                current_url,
                headers=headers,
                allow_redirects=False,
                stream=True,
                timeout=timeout,
                proxies=proxies,
                verify=ca_bundle,
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("Redirect without a Location header")
                    if hop == MAX_REDIRECTS:
                        raise ValueError("Too many redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if (require_content_type and not content_type) or (
                    content_type and content_type not in accepted_types
                ):
                    raise ValueError(
                        f"Unsupported content type for {resource_name}: "
                        f"{content_type or 'unknown'}"
                    )

                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError:
                        declared_bytes = None
                    if declared_bytes is not None and declared_bytes > max_bytes:
                        raise ValueError(
                            f"{resource_name} exceeds the {max_bytes:,}-byte download limit"
                        )

                chunks: list[bytes] = []
                downloaded_bytes = 0
                for chunk in response.iter_content(chunk_size=65_536):
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError(
                            f"{resource_name} exceeds the {max_bytes:,}-byte download limit"
                        )
                    chunks.append(chunk)

                body = b"".join(chunks)
                if pdf_signature and b"%PDF-" not in body[:1024]:
                    raise ValueError(
                        "Response did not contain a recognizable PDF header"
                    )
                return DownloadedResource(body, current_url, content_type)

    raise ValueError("Unable to download the resource")


def download_html(url: str) -> DownloadedResource:
    """Download public HTML (2 MB max; strict HTML content type)."""
    return _download(
        url,
        accepted_types=HTML_CONTENT_TYPES,
        accept_header="text/html,application/xhtml+xml",
        max_bytes=HTML_DOWNLOAD_LIMIT,
        timeout=(4, 20),
        resource_name="HTML",
        require_content_type=True,
        pdf_signature=False,
    )


def download_pdf(url: str) -> DownloadedResource:
    """Download public PDF (15 MB max; check PDF magic bytes)."""
    return _download(
        url,
        accepted_types=PDF_CONTENT_TYPES,
        accept_header="application/pdf,application/octet-stream;q=0.8",
        max_bytes=PDF_DOWNLOAD_LIMIT,
        timeout=(4, 25),
        resource_name="PDF",
        require_content_type=False,
        pdf_signature=True,
    )
