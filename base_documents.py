"""Safe, dependency-free selection of user-owned historical LaTeX documents.

The CLI uses this only to choose names; file content is opened later by an
explicitly HITL-approved staging tool. Names are constrained to one .tex
basename under /workspace/candidate/base-documents.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Callable


class BaseDocumentSelectionError(ValueError):
    """Invalid source selection or unavailable interactive document."""


_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,199}\.tex\Z", re.IGNORECASE)
BASE_DOCUMENTS_VIRTUAL = "/workspace/candidate/base-documents"


def validate_filename(filename: str) -> str:
    if (not isinstance(filename, str) or not _FILENAME.fullmatch(filename)
            or filename.startswith(".") or ".." in filename or "/" in filename
            or "\\" in filename):
        raise BaseDocumentSelectionError(
            "Base document must be a single .tex filename (no directories, '..', or special paths)"
        )
    return filename


def _library_directory(workspace_root: Path) -> Path:
    root = Path(workspace_root)
    if root.is_symlink() or not root.is_dir():
        raise BaseDocumentSelectionError("Workspace must be an existing non-symlink directory")
    directory = root / "candidate" / "base-documents"
    if (root / "candidate").is_symlink() or directory.is_symlink():
        raise BaseDocumentSelectionError("Candidate/base-documents symlinks are forbidden")
    return directory


def list_base_documents(workspace_root: Path) -> list[str]:
    """List direct regular .tex files, ignoring symlinks and subdirectories."""
    directory = _library_directory(workspace_root)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise BaseDocumentSelectionError("base-documents exists but is not a directory")
    return sorted(
        (p.name for p in directory.iterdir()
         if p.is_file() and not p.is_symlink()
         and _FILENAME.fullmatch(p.name) and ".." not in p.name),
        key=str.casefold,
    )


def resolve_base_document(workspace_root: Path, option: str | None, *, kind: str,
                          interactive: bool = False, input_fn: Callable[[str], str] = input,
                          output_fn: Callable[[str], None] = print) -> str | None:
    """Return selected basename or None to use original template fallback.

    The list picker is deliberately stdlib-only and requires a real terminal;
    --list-base-documents supports automated/noninteractive discovery.
    """
    if option is None:
        return None
    if option == "?":
        if not interactive:
            raise BaseDocumentSelectionError(
                f"--base-{kind} ? needs an interactive terminal; use --list-base-documents first"
            )
        entries = list_base_documents(workspace_root)
        if not entries:
            raise BaseDocumentSelectionError(
                "No .tex historical documents in /workspace/candidate/base-documents"
            )
        output_fn(f"Select {kind.replace('_', ' ')} from {BASE_DOCUMENTS_VIRTUAL}:")
        for number, filename in enumerate(entries, 1):
            output_fn(f"  {number:>2}. {filename}")
        answer = input_fn("Document number (or q to cancel): ").strip()
        if answer.lower() in {"q", "quit", ""}:
            raise BaseDocumentSelectionError("Selection cancelled")
        if not answer.isdecimal() or not 1 <= int(answer) <= len(entries):
            raise BaseDocumentSelectionError("Invalid selection number")
        return entries[int(answer) - 1]
    filename = validate_filename(option)
    if filename not in list_base_documents(workspace_root):
        raise BaseDocumentSelectionError(
            f"Historical document not found or not a regular file: {BASE_DOCUMENTS_VIRTUAL}/{filename}"
        )
    return filename
