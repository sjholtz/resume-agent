"""Reject newly introduced fill-in placeholders in authoritative candidate records.

Only inserted or replaced lines are inspected, so legacy records with an old
placeholder can still be edited to repair them without freezing unrelated edits.
No source file is ever rewritten automatically.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATE_FIELD = re.compile(
    r"^\s*(?:\*\*)?Last Updated(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*?)\s*$", re.IGNORECASE
)
_BRACKET = re.compile(r"\[([^\]\n]{1,120})\]")
_TEMPLATE = re.compile(
    r"^(?:current\s+date|date|yyyy[-/]?mm[-/]?dd|mm[-/]dd[-/]yyyy|"
    r"tbd|todo|tba|placeholder|to be determined|"
    r"name|title|role|company|employer|project|year|details|description|location|"
    r"email|phone|url|link|metric|number|value|technology|framework|achievement|result|"
    r"(?:insert|enter|your|provide|replace|fill in|add|specify)\b.*|"
    r".*\bif known\b.*|.*\bgoes here\b.*)$",
    re.IGNORECASE,
)


def local_date_iso() -> str:
    """Current date according to the process's local timezone, at call time."""
    timezone_name = os.environ.get("RESUME_AGENT_TIMEZONE")
    if timezone_name:
        try:
            return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Invalid/unavailable RESUME_AGENT_TIMEZONE: {timezone_name!r}"
            ) from exc
    return datetime.now().astimezone().date().isoformat()


def _new_lines(before: str | None, after: str):
    previous = (before or "").splitlines()
    proposed = after.splitlines()
    matcher = SequenceMatcher(a=previous, b=proposed, autojunk=False)
    for opcode, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if opcode in {"insert", "replace"}:
            for lineno in range(j1, j2):
                yield lineno + 1, proposed[lineno]


def profile_content_issues(
    before: str | None, after: str, *, today: str | None = None
) -> list[str]:
    """Report newly introduced template placeholders and invalid update dates.

    Existing unrelated placeholders remain untouched, but a newly written
    Last Updated field must specify the actual local calendar date.
    """
    if today is None:
        today = local_date_iso()
    issues: list[str] = []
    for lineno, line in _new_lines(before, after):
        for match in _BRACKET.finditer(line):
            value = match.group(1).strip()
            if _TEMPLATE.fullmatch(value):
                issues.append(
                    f"New line {lineno}: unresolved template placeholder [{value}]; supply actual content"
                )
        metadata = _DATE_FIELD.match(line)
        if metadata:
            value = metadata.group(1).strip().rstrip("*").strip()
            if value != today:
                issues.append(
                    f"New line {lineno}: Last Updated must be the actual local date {today} (YYYY-MM-DD), not {value!r}"
                )
    return issues
