"""Schema checks for optional candidate context and employer connections.

CTX records are authoritative *candidate* data, updated only by the
candidate-profile agent through existing two-stage approval. Application-side
connection records are working notes, not proof of consent or new profile facts.

This module deliberately has no network or file-write operations. Validation is
an additional offline quality check, not a substitute for HITL authorization.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

CONTEXT_ID = re.compile(r"CTX-[0-9]{4,}\Z")
CONNECTION_ID = re.compile(r"CONN-[0-9]{4,}\Z")
RECORD_ID = re.compile(
    r"(?:EXP|PROJECT|ACC|SKILL|EDU|CERT|EVID|FUP|SRC|COMP|CTX)-[A-Za-z0-9][A-Za-z0-9-]*\Z"
)
KINDS = frozenset(
    {
        "product_familiarity",
        "past_career",
        "hobby",
        "volunteering",
        "customer_perspective",
        "community_activity",
        "other",
    }
)
BASIS = frozenset({"candidate_reported", "documented", "unverified"})
DISCLOSURE = frozenset({"ask_per_application", "do_not_use"})
CONFIRMATION = frozenset({"unknown", "candidate_confirmed", "candidate_declined"})
AUTHORIZATION = frozenset({"not_requested", "approved", "declined"})
DECISIONS = frozenset({"consider", "omit", "include"})
PLACEHOLDER = re.compile(r"\[[^\]\n]{1,80}\]|\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)


class ContextRecordError(ValueError):
    """Invalid candidate context data; fail closed without changing files."""


def _text(obj: Mapping[str, Any], key: str, *, required: bool = True) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ContextRecordError(
            f"{key}: required nonempty string"
            if required
            else f"{key}: expected string"
        )
    if PLACEHOLDER.search(value):
        raise ContextRecordError(f"{key}: unresolved placeholder")
    return value.strip()


def _choice(obj: Mapping[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _text(obj, key)
    if value not in allowed:
        raise ContextRecordError(
            f"{key}: unexpected value {value!r}; expected {sorted(allowed)}"
        )
    return value


def _list_of_strings(obj: Mapping[str, Any], key: str) -> list[str]:
    values = obj.get(key, [])
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ContextRecordError(f"{key}: expected list of strings")
    for value in values:
        if not value.strip() or PLACEHOLDER.search(value):
            raise ContextRecordError(f"{key}: empty or unresolved list item")
    return values


def validate_context_record(
    record: Mapping[str, Any], *, filename: str | None = None
) -> None:
    """Validate one source-of-truth CTX entry. Does not imply source verification."""
    if not isinstance(record, dict):
        raise ContextRecordError("context: expected JSON object")
    identifier = _text(record, "id")
    if not CONTEXT_ID.fullmatch(identifier):
        raise ContextRecordError("id: expected CTX- followed by at least four digits")
    if filename is not None and Path(filename).name != identifier + ".json":
        raise ContextRecordError("id: does not match filename")
    _choice(record, "kind", KINDS)
    for key in ("title", "description", "candidate_relationship"):
        _text(record, key)
    _choice(record, "evidence_status", BASIS)
    _choice(record, "disclosure_preference", DISCLOSURE)
    for name in ("related_records", "supporting_references"):
        values = _list_of_strings(record, name)
        if name == "related_records" and any(
            not RECORD_ID.fullmatch(v) for v in values
        ):
            raise ContextRecordError("related_records: invalid profile ID")
    if "notes" in record:
        _text(record, "notes", required=False)


def _url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def validate_connection_record(
    connection: Mapping[str, Any], contexts: Mapping[str, Mapping[str, Any]]
) -> None:
    """Validate one employer connection and its self-reported disclosure status.

    Only an external candidate conversation/approval event can establish that
    authorization was genuinely given; this validator checks consistency, not
    truthfulness of an agent-entered approval_reference.
    """
    if not isinstance(connection, dict):
        raise ContextRecordError("connection: expected JSON object")
    ident = _text(connection, "id")
    if not CONNECTION_ID.fullmatch(ident):
        raise ContextRecordError("id: expected CONN- followed by at least four digits")
    context_id = _text(connection, "context_id")
    if context_id not in contexts:
        raise ContextRecordError(
            f"context_id: no matching profile record for {context_id}"
        )
    ctx = contexts[context_id]
    validate_context_record(ctx)
    for key in ("company", "role", "company_observation", "connection_rationale"):
        _text(connection, key)
    urls = _list_of_strings(connection, "company_source_urls")
    if not urls or any(not _url(url) for url in urls):
        raise ContextRecordError(
            "company_source_urls: at least one public HTTP(S) citation required"
        )
    confirmation = _choice(connection, "candidate_confirmation", CONFIRMATION)
    authorization = _choice(connection, "disclosure_authorization", AUTHORIZATION)
    decision = _choice(connection, "use_decision", DECISIONS)
    scope = _text(connection, "disclosure_scope")
    if scope != "cover_letter":
        raise ContextRecordError("disclosure_scope: only cover_letter supported in v1")
    if authorization == "approved":
        if confirmation != "candidate_confirmed":
            raise ContextRecordError(
                "disclosure_authorization: approval needs candidate-confirmed fact"
            )
        _text(connection, "approval_reference")
    elif connection.get("approval_reference"):
        raise ContextRecordError(
            "approval_reference: cannot exist without approved authorization"
        )
    if ctx["disclosure_preference"] == "do_not_use" and decision != "omit":
        raise ContextRecordError(
            "use_decision: candidate marked this context do_not_use"
        )
    if decision == "include":
        if confirmation != "candidate_confirmed" or authorization != "approved":
            raise ContextRecordError(
                "use_decision: include requires confirmed fact and scoped approval"
            )
        if ctx["evidence_status"] == "unverified":
            raise ContextRecordError(
                "use_decision: unverified candidate fact cannot be included"
            )
    if authorization == "declined" and decision != "omit":
        raise ContextRecordError("use_decision: declined authorization requires omit")
    if confirmation == "candidate_declined" and decision != "omit":
        raise ContextRecordError("use_decision: declined candidate fact requires omit")


def validate_context_profile_write(
    file_path: str, proposed_content: str, *, candidate_prefix: str
) -> None:
    """Check a proposed CTX record from the *actual* profile write boundary.

    Call before interactive approval and again when registering a verified
    receipt; both validations work only on projected text and perform no IO.
    Other candidate records are not affected. This protects the JSON schema,
    not the truth of the candidate's claim or their future disclosure consent.
    """
    target = PurePosixPath(file_path)
    context_directory = PurePosixPath(candidate_prefix) / "context"
    if target.parent != context_directory:
        return
    if not (target.suffix == ".json" or target.name.startswith("CTX-")):
        return
    if target.suffix != ".json":
        raise ContextRecordError("context record: CTX files must be .json")
    try:
        content = json.loads(proposed_content)
    except ValueError as exc:
        raise ContextRecordError(f"context record: invalid JSON: {exc}") from exc
    validate_context_record(content, filename=target.name)


def load_context_directory(path: Path) -> dict[str, dict[str, Any]]:
    """Read records, validate IDs, and detect duplicate IDs without writing."""
    if not path.is_dir():
        raise ContextRecordError(f"context directory not found: {path}")
    contexts: dict[str, dict[str, Any]] = {}
    for file in sorted(path.glob("CTX-*.json")):
        if file.is_symlink() or not file.is_file():
            raise ContextRecordError(f"not a regular context file: {file}")
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ContextRecordError(f"cannot parse {file.name}: {exc}") from exc
        validate_context_record(value, filename=file.name)
        if value["id"] in contexts:
            raise ContextRecordError(f"duplicate context ID: {value['id']}")
        contexts[value["id"]] = value
    return contexts


def validate_connections_file(
    path: Path, contexts: Mapping[str, Mapping[str, Any]]
) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ContextRecordError(f"cannot read connections: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(
        payload.get("connections"), list
    ):
        raise ContextRecordError(
            "connections file: expected object with connections array"
        )
    seen: set[str] = set()
    for connection in payload["connections"]:
        validate_connection_record(connection, contexts)
        identifier = connection["id"]
        if identifier in seen:
            raise ContextRecordError("duplicate connection ID: " + identifier)
        seen.add(identifier)
    return len(seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only candidate context schema checker"
    )
    parser.add_argument("--context-dir", required=True, type=Path)
    parser.add_argument(
        "--connections",
        type=Path,
        help="Optional application CANDIDATE_CONNECTIONS.json",
    )
    args = parser.parse_args(argv)
    try:
        records = load_context_directory(args.context_dir)
        count = (
            validate_connections_file(args.connections, records)
            if args.connections
            else 0
        )
    except ContextRecordError as exc:
        print(f"FAIL: candidate-context: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: candidate-context ({len(records)} CTX, {count} connections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
