"""Minimal standalone verifier for separate 8R append / 8F field-update smokes.

Run on host with a BEFORE snapshot taken prior to starting the Guix agent.
This deliberately does not mutate the fixture or infer authorizations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

FIELD = b"Candidate work-arrangement preference: NOT_RECORDED"
NEW = b"Candidate work-arrangement preference: remote"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["append-revise", "field-update"], required=True
    )
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    before = args.before.read_bytes()
    after = args.after.read_bytes()

    if args.mode == "append-revise":
        line = b"SMOKE-APPEND-REVISION-TEST: revised"
        # This scenario requests EXACTLY ONE appended line. Do not treat
        # additional blank lines as harmless by stripping newlines.
        separator = b"" if not before or before.endswith(b"\n") else b"\n"
        expected = before + separator + line + b"\n"
        ok = (
            after == expected
            and line not in before
            and b"SMOKE-APPEND-REVISION-TEST: initial" not in after
        )
    else:
        ok = (
            before.count(FIELD) == 1
            and after == before.replace(FIELD, NEW, 1)
            and after.count(NEW) == 1
            and b"SMOKE-CANDIDATE-PREFERENCE:" not in after
        )
    print(("PASS" if ok else "FAIL") + ": " + args.mode)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
