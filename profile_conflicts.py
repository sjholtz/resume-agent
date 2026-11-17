"""Conservative, deterministic checks for duplicated or conflicting profile fields.

This is NOT a general semantic reasoner. A small explicit alias map covers one
work-arrangement preference field, plus a synthetic smoke-test spelling.
Other real-world contradictions still require review by the candidate.
"""

from __future__ import annotations

import re
from collections import defaultdict

_WORK_PREFERENCE_ALIASES = frozenset(
    {
        "candidate work arrangement preference",
        "work arrangement preference",
        "smoke candidate preference",
    }
)
_UNKNOWN = frozenset(
    {
        "not_recorded",
        "not recorded",
        "unknown",
        "not provided",
        "unspecified",
    }
)
_FIELD_LINE = re.compile(r"^\s*([^:\n]{1,100}):\s*(\S[^\n]*?)\s*$")


def _key(label: str) -> str | None:
    key = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
    if key in _WORK_PREFERENCE_ALIASES:
        return "candidate work-arrangement preference"
    return None


def _value(value: str) -> str:
    value = " ".join(value.casefold().split())
    return "NOT_RECORDED" if value in _UNKNOWN else value


def _fields(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for line in text.splitlines():
        match = _FIELD_LINE.fullmatch(line)
        if match:
            key = _key(match.group(1))
            if key:
                values[key].append(_value(match.group(2)))
    return values


def newly_introduced_profile_conflicts(before: str | None, after: str) -> list[str]:
    """Refuse newly introduced conflicts *or extra copies of a known field*.

    An existing inconsistency is not retroactively treated as an authorized
    reason to delete unrelated content. An exact in-place field update from
    NOT_RECORDED to a confirmed value is allowed (one field before and after).
    """
    previous = _fields(before or "")
    proposed = _fields(after)
    issues: list[str] = []
    for field, values in sorted(proposed.items()):
        old = previous.get(field, [])
        if len(set(values)) > 1 and (len(set(old)) <= 1 or set(values) != set(old)):
            issues.append(
                f"Conflicting {field} entries: {', '.join(sorted(set(values)))}. "
                "Update the existing field in place instead of appending a competing value."
            )
        elif len(values) > 1 and len(values) > len(old):
            issues.append(
                f"Duplicate {field} field. Update the existing field in place; "
                "do not add another copy."
            )
    return issues
