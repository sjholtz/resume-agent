"""Bounded, read-only extraction of candidate-supplied local PDFs.

This module contains no LangChain dependency. It maps validated *virtual*
/workspace paths to the configured physical workspace, and uses descriptor-
relative, no-symlink opens so path components cannot escape the approved tree.
It never creates, modifies, caches or sends a document over the network.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path, PurePosixPath

import pymupdf

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_DOCUMENT_PAGES = 500
MAX_PAGES_PER_CALL = 10
MIN_CHARS_PER_CALL = 1_000
MAX_CHARS_PER_CALL = 30_000
# Best-effort elapsed-time budget between page operations; it cannot
# interrupt a single PyMuPDF C call. Hard CPU/memory caps require OS limits.
MAX_ELAPSED_SECONDS = 30.0


class LocalPDFError(ValueError):
    """An invalid or unavailable local-PDF source."""


def _relative_parts(file_path: str, candidate_profile: str) -> tuple[str, ...]:
    """Validate a virtual path without letting POSIX normalization mask '..'."""
    if not isinstance(file_path, str) or not file_path.startswith("/"):
        raise LocalPDFError("file_path must be an absolute virtual /workspace path")
    if "\\" in file_path or "\x00" in file_path or "//" in file_path:
        raise LocalPDFError("file_path contains an unsafe path component")
    if any(part in (".", "..") for part in file_path.split("/")):
        raise LocalPDFError("file_path must not contain '.' or '..' components")
    if file_path.endswith("/"):
        raise LocalPDFError("file_path must identify a PDF file")
    if not isinstance(candidate_profile, str):
        raise LocalPDFError("invalid candidate profile prefix")
    # profile_prefix() validates the customizable prefix consistently with
    # Deep Agents permissions; it does not inspect the host filesystem.
    from permissions import profile_prefix

    protected = profile_prefix(candidate_profile)
    prefix = protected + "/evidence/sources/documents/"
    if not file_path.startswith(prefix) or len(file_path) <= len(prefix):
        raise LocalPDFError(
            "file_path must be under " + protected + "/evidence/sources/documents/"
        )
    if PurePosixPath(file_path).suffix.lower() != ".pdf":
        raise LocalPDFError("only .pdf documents are supported")
    return tuple(PurePosixPath(file_path).relative_to("/workspace").parts)


def _read_regular_file_no_symlinks(
    workspace_root: Path, parts: tuple[str, ...]
) -> bytes:
    """Walk from a trusted workspace root using openat / O_NOFOLLOW."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(workspace_root, directory_flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], file_flags, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise LocalPDFError("source must be a regular PDF file")
            if info.st_size > MAX_PDF_BYTES:
                raise LocalPDFError(f"PDF exceeds {MAX_PDF_BYTES} byte limit")
            if info.st_size == 0:
                raise LocalPDFError("PDF is empty")
            with os.fdopen(os.dup(file_fd), "rb") as source:
                contents = source.read(MAX_PDF_BYTES + 1)
            if len(contents) > MAX_PDF_BYTES:
                raise LocalPDFError(f"PDF exceeds {MAX_PDF_BYTES} byte limit")
            if len(contents) != info.st_size:
                raise LocalPDFError("PDF changed while being read; retry")
            if not contents.startswith(b"%PDF-"):
                raise LocalPDFError("file does not have a PDF signature")
            return contents
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def extract_local_pdf(
    file_path: str,
    *,
    workspace_root: Path,
    candidate_profile: str,
    start_page: int = 1,
    start_char: int = 0,
    max_pages: int = 3,
    max_chars: int = 12_000,
) -> dict:
    """Extract a bounded window of page-numbered text from a source PDF.

    Character cursors are offsets into PyMuPDF's sorted/stripped text for
    *one page*. The source's SHA-256 should be stable across continuation
    calls; if it changes, restart the evidence review.
    """
    # No implicit coercion: bool is a subclass of int but not a valid cursor.
    arguments = (
        (start_page, "start_page", 1, None),
        (start_char, "start_char", 0, None),
        (max_pages, "max_pages", 1, MAX_PAGES_PER_CALL),
        (max_chars, "max_chars", MIN_CHARS_PER_CALL, MAX_CHARS_PER_CALL),
    )
    for value, name, minimum, maximum in arguments:
        if (
            type(value) is not int
            or value < minimum
            or (maximum is not None and value > maximum)
        ):
            limit = f" through {maximum}" if maximum is not None else ""
            return {
                "ok": False,
                "file_path": file_path,
                "error": f"{name} must be an integer from {minimum}{limit}",
            }

    started = time.monotonic()
    try:
        parts = _relative_parts(file_path, candidate_profile)
        raw = _read_regular_file_no_symlinks(Path(workspace_root), parts)
        sha256 = hashlib.sha256(raw).hexdigest()
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise LocalPDFError(
                    "PDF is password-protected; decryption is not supported"
                )
            page_count = pdf.page_count
            if not 1 <= page_count <= MAX_DOCUMENT_PAGES:
                raise LocalPDFError(
                    f"PDF page count must be 1 through {MAX_DOCUMENT_PAGES}; found {page_count}"
                )
            if start_page > page_count:
                raise LocalPDFError(
                    f"start_page {start_page} exceeds {page_count} pages"
                )

            metadata = pdf.metadata or {}
            page_limit = min(page_count, start_page + max_pages - 1)
            remaining = max_chars
            segments = []
            missing_text = []
            next_page = None
            next_char = None
            extracted = 0

            for number in range(start_page, page_limit + 1):
                if time.monotonic() - started > MAX_ELAPSED_SECONDS:
                    raise LocalPDFError(
                        "PDF extraction exceeded the elapsed-time budget"
                    )
                text = pdf[number - 1].get_text("text", sort=True).strip()
                if not text:
                    missing_text.append(number)
                offset = start_char if number == start_page else 0
                if offset > len(text):
                    raise LocalPDFError(
                        f"start_char {offset} exceeds page {number} text length {len(text)}"
                    )
                selected = text[offset : offset + remaining]
                end = offset + len(selected)
                segments.append(
                    {
                        "page": number,
                        "start_char": offset,
                        "end_char": end,
                        "total_chars": len(text),
                        "content": selected,
                    }
                )
                extracted += len(selected)
                remaining -= len(selected)
                if end < len(text):
                    next_page, next_char = number, end
                    break
                if remaining == 0:
                    if number < page_count:
                        next_page, next_char = number + 1, 0
                    break
            if next_page is None and page_limit < page_count:
                next_page, next_char = page_limit + 1, 0

            result = {
                "ok": bool(extracted),
                "file_path": file_path,
                "content_type": "application/pdf",
                "file_bytes": len(raw),
                "sha256": sha256,
                "title": metadata.get("title") or None,
                "author": metadata.get("author") or None,
                "subject": metadata.get("subject") or None,
                "pdf_creation_date": metadata.get("creationDate") or None,
                "page_count": page_count,
                "start_page": start_page,
                "start_char": start_char,
                "max_pages": max_pages,
                "max_chars": max_chars,
                "pages": segments,
                "extracted_chars": extracted,
                "pages_without_text": missing_text,
                "needs_ocr": bool(missing_text),
                "has_more": next_page is not None,
                "next_start_page": next_page,
                "next_start_char": next_char,
            }
            if not extracted:
                result["error"] = (
                    "No selectable text found on requested pages; "
                    "PDF may be scanned/image-only (OCR not supported)"
                )
            return result
    except (OSError, ValueError, RuntimeError) as exc:
        # Never fall back to unbounded reads or alternative locations.
        return {"ok": False, "file_path": file_path, "error": str(exc)}
