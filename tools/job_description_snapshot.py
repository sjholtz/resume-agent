"""Fetch a complete, bounded, public HTML source snapshot for an application.

Uses the existing web_fetch downloader: public-URL/DNS checking, per-redirect
validation, timeouts, byte limits and configured proxy/CA. The same caveat as
web_fetch applies: enforce network egress restrictions against DNS rebinding.
The retrieved text is untrusted external DATA, not agent instructions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from application_workspace import ApplicationWorkspaceError, RetrievedJobDescription

MAX_EXTRACTED_CHARS = 150_000
MIN_EXTRACTED_CHARS = 120


def fetch_job_description(url: str) -> RetrievedJobDescription:
    """Retrieve the *entire* extracted text, not a 15k/30k-character page chunk.

    Does not attempt JavaScript rendering, login, or PDFs. Caller must fail
    closed if the text cannot be extracted; there is no silent truncated save.
    """
    # Import lazily so offline path/provenance tests need no optional web deps.
    import requests
    from trafilatura import bare_extraction, html2txt
    from trafilatura.settings import DEFAULT_CONFIG
    from tools.web_fetch import download_html

    try:
        response = download_html(url)
        config = deepcopy(DEFAULT_CONFIG)
        config["DEFAULT"]["MAX_TREE_SIZE"] = "50000"
        document = bare_extraction(
            response.body,
            url=response.final_url,
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            config=config,
        )
        if document is not None and document.text and document.text.strip():
            content = document.text.strip()
            method = "main_content"
        else:
            content = (html2txt(response.body.decode("utf-8", errors="replace")) or "").strip()
            method = "fallback_all_text"
        if len(content) < MIN_EXTRACTED_CHARS:
            raise ApplicationWorkspaceError(
                "Job posting yielded too little readable text; it may require JavaScript/login. "
                "Supply the complete original description text instead."
            )
        if len(content) > MAX_EXTRACTED_CHARS:
            raise ApplicationWorkspaceError(
                "Extracted page is unusually large; refusing to save a potentially unrelated page. "
                "Supply the complete job description text explicitly."
            )
        return RetrievedJobDescription(
            text=content,
            final_url=response.final_url,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            extraction=method,
        )
    except (ValueError, requests.exceptions.RequestException, OSError, UnicodeError) as exc:
        if isinstance(exc, ApplicationWorkspaceError):
            raise
        raise ApplicationWorkspaceError(f"Unable to retrieve job description: {exc}") from exc
