"""Per-user-turn guard for intentional candidate-profile request cancellations.

The LangGraph HITL `reject` response denies one tool invocation only. The
model may retry via another tool. This guard blocks further profile writes
in the same CLI user turn without asking the candidate again.

This does not replace the existing role/permission and HITL layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from profile_review import CANCEL_PROFILE_REQUEST_MESSAGE, reject

MAX_AUTO_REJECTS = 3


class RejectedWriteRetryLoop(RuntimeError):
    """Repeated cancelled writes; caller must abandon this graph invocation."""


@dataclass
class ProfileCancelGuard:
    cancelled: bool = False
    automatic_rejections: int = 0
    approved_paths: set[str] = None

    def __post_init__(self) -> None:
        if self.approved_paths is None:
            self.approved_paths = set()

    def record(
        self, decision: dict[str, Any], action: dict[str, Any] | None = None
    ) -> None:
        """Record cancellation or an authorization for one profile path in this turn.

        Authorizing a profile write is not proof that the tool succeeded; it
        simply prevents a second unrequested tool proposal from overwriting a
        candidate-reviewed revision in the same user turn.
        """
        if (
            decision.get("type") == "reject"
            and decision.get("message") == CANCEL_PROFILE_REQUEST_MESSAGE
        ):
            self.cancelled = True
        elif decision.get("type") in {"approve", "edit"} and action is not None:
            path = action.get("args", {}).get("file_path")
            if isinstance(path, str):
                self.approved_paths.add(path)

    def already_authorized_path(self, action: dict[str, Any]) -> bool:
        path = action.get("args", {}).get("file_path")
        return isinstance(path, str) and path in self.approved_paths

    def block_additional_write(self) -> dict[str, Any]:
        """Deny another write to an already authorized path during this turn."""
        self.automatic_rejections += 1
        if self.automatic_rejections > MAX_AUTO_REJECTS:
            raise RejectedWriteRetryLoop(
                "Model repeatedly proposed writes to an already authorized "
                "candidate profile path. Stop and start a new user turn."
            )
        return reject(
            "CANDIDATE_APPROVED_FINAL_PROFILE_CONTENT: An exact candidate-"
            "reviewed profile change was already authorized for this file "
            "in the current user turn. Do not overwrite or correct it without "
            "a NEW explicit candidate request."
        )

    def block_retry(self) -> dict[str, Any]:
        """Reject the next protected profile edit without asking the candidate."""
        if not self.cancelled:
            raise ValueError("block_retry called without candidate cancellation")
        self.automatic_rejections += 1
        if self.automatic_rejections > MAX_AUTO_REJECTS:
            raise RejectedWriteRetryLoop(
                "Agent repeatedly attempted profile writes after explicit candidate "
                "cancellation. No further tool operations approved. Abandon this "
                "invocation and use a fresh thread for the next user request."
            )
        return reject(CANCEL_PROFILE_REQUEST_MESSAGE)
