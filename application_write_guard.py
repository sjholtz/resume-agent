"""Canonical initialized-application write gate for CLI-approved file operations.

Apply before human approval of ordinary write_file/edit_file/delete. The guard
cannot substitute for the existing Deep Agents permissions or OS sandbox.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from application_workspace import ApplicationWorkspaceError, canonical_paths, _physical


class ApplicationWriteError(ValueError):
    """Ordinary write is outside a canonical initialized application."""


SLUG = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")
JOB = re.compile(r"(\d{4}-\d{2}-\d{2})-([a-z0-9]+(?:_[a-z0-9]+)*)\Z")
RECORDS = {"job_description.md", "company_inf.md", "additional_inf.md"}
INTERNALS = {"claim_audit.md", ".latex_source_manifest.json"}


def is_application_path(value: object) -> bool:
    return isinstance(value, str) and (value == "/workspace/applications"
                                       or value.startswith("/workspace/applications/"))


def require_initialized_document_path(workspace_root: Path, virtual: str) -> str:
    """Return canonical job directory or fail closed without changing files.

    Require the source record created by initialize_application; reject
    competing slug conventions, uninitialized jobs, source-record rewrites,
    build outputs, and symlinks.
    """
    if not is_application_path(virtual):
        raise ApplicationWriteError("Not an application path")
    if "\\" in virtual or "\x00" in virtual or "//" in virtual:
        raise ApplicationWriteError("Invalid application path syntax")
    segments = virtual.split("/")
    if any(s in {".", ".."} for s in segments):
        raise ApplicationWriteError("Path traversal is forbidden")
    parts = PurePosixPath(virtual).parts
    if len(parts) < 6 or parts[:3] != ("/", "workspace", "applications"):
        raise ApplicationWriteError("Writes require an initialized position directory")
    company, job = parts[3], parts[4]
    match = JOB.fullmatch(job)
    if not SLUG.fullmatch(company) or match is None:
        raise ApplicationWriteError(
            "Noncanonical application path; use exact position_directory from initialize_application"
        )
    date, role = match.groups()
    try:
        canonical = canonical_paths(company, role, date)
    except ApplicationWorkspaceError as exc:
        raise ApplicationWriteError(str(exc)) from exc
    if canonical.position_directory != f"/workspace/applications/{company}/{job}":
        raise ApplicationWriteError("Application path is not canonical")
    rel = parts[5:]
    if (rel[0] == "build" or rel[-1] in RECORDS | INTERNALS
            or any(s.startswith(".") for s in rel)):
        raise ApplicationWriteError(
            "Source records, manifests and build files are tool-owned; use their approved tools"
        )
    root = Path(workspace_root)
    if not root.is_dir() or root.is_symlink():
        raise ApplicationWriteError("Configured workspace root is invalid")
    root = root.resolve(strict=True)
    try:
        _physical(root, virtual)  # also rejects every symlink component
        record = _physical(root, canonical.job_description)
    except ApplicationWorkspaceError as exc:
        raise ApplicationWriteError(str(exc)) from exc
    if not record.is_file():
        raise ApplicationWriteError(
            "Captured canonical job_description.md is missing; initialize_application first"
        )
    try:
        header = record.read_text(encoding="utf-8")[:1_000_000]
    except (OSError, UnicodeError) as exc:
        raise ApplicationWriteError("Cannot read the initializer source record") from exc
    if (not header.startswith("# Job Description\n") or "- Status: captured\n" not in header
            or "## Source content\n\n" not in header
            or not header.partition("## Source content\n\n")[2].strip()):
        raise ApplicationWriteError("job_description.md is not a captured initializer record")
    return canonical.position_directory
