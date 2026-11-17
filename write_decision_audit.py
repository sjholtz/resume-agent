"""Turn-local, deterministic record of non-profile filesystem HITL decisions.

This is an AUTHORIZATION audit, not proof that a tool executed or that a file
was modified. It deliberately does not access the filesystem or trust model text.
Candidate-profile writes are managed independently by profile_receipts.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FILE_MUTATIONS = frozenset({"write_file", "edit_file", "delete"})


@dataclass(frozen=True)
class WriteDecision:
    tool: str
    file_path: str
    authorized: bool


@dataclass
class WriteDecisionAudit:
    decisions: list[WriteDecision] = field(default_factory=list)

    def record(self, action: dict[str, Any], *, authorized: bool) -> None:
        name = action.get("name")
        if name not in FILE_MUTATIONS:
            return
        args = action.get("args") or {}
        path = args.get("file_path") if isinstance(args, dict) else None
        if not isinstance(path, str):
            path = "(unknown path)"
        self.decisions.append(WriteDecision(name, path, authorized))

    @property
    def denied(self) -> list[WriteDecision]:
        return [d for d in self.decisions if not d.authorized]

    @property
    def authorized(self) -> list[WriteDecision]:
        return [d for d in self.decisions if d.authorized]

    def rejection_report(self) -> str | None:
        """A canonical report to use instead of misleading model success text.

        When any write was approved in the same turn, we cannot conclude that
        the overall requested operation failed; therefore the message states
        authorization facts only and explicitly requires outcome verification.
        """
        if not self.denied:
            return None
        lines = ["Filesystem authorization report (recorded by CLI):"]
        for item in self.denied:
            lines.append(f"  REJECTED: {item.tool} {item.file_path}")
        if self.authorized:
            for item in self.authorized:
                lines.append(
                    f"  AUTHORIZED (execution not verified): {item.tool} {item.file_path}"
                )
            lines.append(
                "Actual outcomes of authorized operations require verification."
            )
        else:
            lines.append(
                "No non-profile file modification was authorized in this turn."
            )
        lines.append("The agent's claimed file-write success is not a verified result.")
        return "\n".join(lines)
