"""Canonical application paths and lossless, approval-gated source capture.

The initializer is intentionally independent of the model/Deep Agents. It must
ONLY be invoked through the approval-interrupted initialize_application tool
(or explicitly by the human through the CLI); it does not grant write authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


class ApplicationWorkspaceError(ValueError):
    """Invalid source data, unsafe path or conflicting existing records."""


def _slug(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(c in value for c in "/\\\x00")
    ):
        raise ApplicationWorkspaceError(
            f"{field} must be nonempty and contain no path separators"
        )
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    result = re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")
    if not result or len(result) > 64:
        raise ApplicationWorkspaceError(
            f"{field} produces an empty or overly long path component"
        )
    return result


def _date(value: str) -> str:
    if not isinstance(value, str):
        raise ApplicationWorkspaceError("application_date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ApplicationWorkspaceError(
            "application_date must be a valid YYYY-MM-DD date"
        ) from exc
    if parsed.isoformat() != value:
        raise ApplicationWorkspaceError("application_date must be YYYY-MM-DD")
    return value


def _metadata(value: str, *, field: str, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ApplicationWorkspaceError(
            f"{field} must be a nonempty string"
            if required
            else f"{field} must be a string"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value) > 500:
        raise ApplicationWorkspaceError(
            f"{field} must be single-line and <= 500 characters"
        )
    return value.strip()


def _url(value: str) -> str:
    value = _metadata(value, field="source URL")
    if value:
        try:
            parts = urlsplit(value)
        except ValueError as exc:
            raise ApplicationWorkspaceError("Invalid source URL") from exc
        if (
            parts.scheme not in {"https", "http"}
            or not parts.netloc
            or parts.username
            or parts.password
        ):
            raise ApplicationWorkspaceError(
                "source URL must be an HTTP(S) URL without credentials"
            )
    return value


@dataclass(frozen=True)
class ApplicationPaths:
    company_directory: str
    position_directory: str
    company_information: str
    company_additional: str
    job_description: str
    position_additional: str
    resume: str
    cover_letter: str


def canonical_paths(
    company_name: str, position_title: str, application_date: str
) -> ApplicationPaths:
    """Construct virtual paths; does not access the host filesystem."""
    company = _slug(company_name, field="company_name")
    position = _slug(position_title, field="position_title")
    day = _date(application_date)
    base = f"/workspace/applications/{company}"
    job = f"{base}/{day}-{position}"
    return ApplicationPaths(
        company_directory=base,
        position_directory=job,
        company_information=f"{base}/company_inf.md",
        company_additional=f"{base}/additional_inf.md",
        job_description=f"{job}/job_description.md",
        position_additional=f"{job}/additional_inf.md",
        resume=f"{job}/resume.tex",
        cover_letter=f"{job}/cover_letter.tex",
    )


def _record(title: str, *, text: str, source: str, url: str, captured: str) -> str:
    if not isinstance(text, str):
        raise ApplicationWorkspaceError("source text must be a string")
    if not isinstance(source, str):
        raise ApplicationWorkspaceError("source label must be a string")
    present = bool(text.strip())
    if present:
        source = _metadata(source, field=f"{title} source", required=True)
    else:
        if source.strip() or url:
            raise ApplicationWorkspaceError(
                f"{title}: cannot claim a source without source content"
            )
        source = "none"
    url = _url(url)
    # The sentinel marks original text, distinct from the derived metadata.
    return (
        f"# {title}\n\n"
        f"- Status: {'captured' if present else 'not_provided'}\n"
        f"- Source: {source}\n"
        f"- Original URL: {url or 'not_provided'}\n"
        f"- Recorded on (UTC): {captured}\n\n"
        "## Source content\n\n"
        + (
            text.rstrip("\n") + "\n"
            if present
            else "Not provided. Do not infer missing facts.\n"
        )
    )


def _existing_matches(existing: str, new: str) -> bool:
    """Ignore recording timestamp for safe, idempotent replays."""

    def normalized(value: str) -> str:
        return re.sub(
            r"(?m)^- Recorded on \(UTC\): .*?$",
            "- Recorded on (UTC): <existing>",
            value,
        )

    return normalized(existing) == normalized(new)


def _no_symlink_components(root: Path, dest: Path) -> None:
    try:
        relative = dest.relative_to(root)
    except ValueError as exc:
        raise ApplicationWorkspaceError(
            "Destination escapes the configured workspace"
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ApplicationWorkspaceError(
                f"Refusing symlink in application path: {current}"
            )


def _physical(root: Path, virtual: str) -> Path:
    path = PurePosixPath(virtual)
    if not path.is_relative_to("/workspace/applications"):
        raise ApplicationWorkspaceError("Refusing path outside applications")
    result = root.joinpath(*path.parts[2:])
    _no_symlink_components(root, result)
    return result


def initialize_application(
    workspace_root: Path,
    *,
    company_name: str,
    position_title: str,
    application_date: str,
    job_description_text: str,
    job_description_source: str,
    job_description_url: str = "",
    company_information_text: str = "",
    company_information_source: str = "",
    company_information_url: str = "",
    company_additional_text: str = "",
    company_additional_source: str = "",
    position_additional_text: str = "",
    position_additional_source: str = "",
) -> dict[str, object]:
    """Create canonical directories and source files without overwriting records.

    Preflight checks *all* paths and data before writing anything. Any
    conflicting supplied record aborts rather than silently losing input.
    This is a narrowly scoped write action and needs separate HITL approval.
    """
    paths = canonical_paths(company_name, position_title, application_date)
    root = Path(workspace_root)
    if not root.is_dir() or root.is_symlink():
        raise ApplicationWorkspaceError(
            "Configured workspace root must be an existing non-symlink directory"
        )
    root = root.resolve(strict=True)
    if not isinstance(job_description_text, str) or not job_description_text.strip():
        raise ApplicationWorkspaceError(
            "Nonempty original job_description_text is required before initialization"
        )
    # Do not claim the source date was an external retrieval date; this is only
    # the date our source snapshot was recorded.
    captured = datetime.now(UTC).date().isoformat()
    specs = [
        (
            paths.job_description,
            "Job Description",
            job_description_text,
            job_description_source,
            job_description_url,
        ),
        (
            paths.company_information,
            "Company Information",
            company_information_text,
            company_information_source,
            company_information_url,
        ),
        (
            paths.company_additional,
            "Additional Company Information",
            company_additional_text,
            company_additional_source,
            "",
        ),
        (
            paths.position_additional,
            "Additional Position Information",
            position_additional_text,
            position_additional_source,
            "",
        ),
    ]
    writes: list[tuple[Path, str, str]] = []
    preserved: list[str] = []
    for virtual, title, value, source, url in specs:
        content = _record(title, text=value, source=source, url=url, captured=captured)
        dest = _physical(root, virtual)
        if dest.exists():
            if not dest.is_file():
                raise ApplicationWorkspaceError(
                    f"Source record is not a regular file: {virtual}"
                )
            current = dest.read_text(encoding="utf-8")
            if not value.strip() or _existing_matches(current, content):
                preserved.append(virtual)
                continue
            raise ApplicationWorkspaceError(
                f"Conflicting existing source record: {virtual}; manually review before changing it"
            )
        writes.append((dest, content, virtual))
    company_dir = _physical(root, paths.company_directory)
    position_dir = _physical(root, paths.position_directory)
    if company_dir.exists() and not company_dir.is_dir():
        raise ApplicationWorkspaceError("Company destination is not a directory")
    if position_dir.exists() and not position_dir.is_dir():
        raise ApplicationWorkspaceError("Position destination is not a directory")
    position_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for dest, content, virtual in writes:
        # Exclusive mode stops accidental replacement even under concurrency.
        with dest.open("x", encoding="utf-8") as handle:
            handle.write(content)
        created.append(virtual)
    return {
        "paths": vars(paths),
        "created": created,
        "preserved_existing": preserved,
        "job_description_status": "captured",
        "note": "Read the written source records before drafting. No templates or candidate files were changed.",
    }
