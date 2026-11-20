"""Verified, turn-scoped receipts for candidate-approved profile file changes.

The CLI records a proposed write ONLY after both human approval gates pass.
The orchestrator's `task` tool wrapper verifies the resulting on-disk bytes
before forwarding a structured receipt to the orchestrator. This does not
perform or authorize writes; existing HITL and path permissions remain intact.

Prototype limitations: one in-process CLI; physical /workspace mount; best-effort
post-write verification (not transactional); per-thread registry; no server
multi-process coordination. Candidate content must not be logged or traced
without the operator's explicit opt-in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from candidate_context_records import ContextRecordError, validate_context_profile_write
from profile_conflicts import newly_introduced_profile_conflicts
from profile_content_quality import profile_content_issues
from profile_review import (
    _checked_path,
    _project,
    _read_snapshot,
    _single_insertion,
    _text,
    is_explicit_begin_prepend_request,
    is_explicit_end_append_request,
)

SCHEMA = "career.profile_change_receipt.v1"
RECEIPT_PREFIX = "PROFILE_CHANGE_RECEIPT_V1: "
MAX_DIFF_CHARS = 12000


def _hash(data: bytes | None) -> str | None:
    return sha256(data).hexdigest() if data is not None else None


def _diff(before: str, after: str) -> tuple[str, bool]:
    parts = unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="original",
        tofile="candidate-approved",
    )
    content = "".join(parts)
    if len(content) > MAX_DIFF_CHARS:
        return content[:MAX_DIFF_CHARS], True
    return content, False


@dataclass(frozen=True)
class PendingChange:
    file_path: str
    tool: str
    originally_proposed_sha256: str
    candidate_approved_sha256: str
    baseline_sha256: str | None
    expected_bytes: bytes
    original_diff: str
    approved_diff: str
    approved_diff_truncated: bool
    changed_by_candidate: bool


class ProfileReceiptRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._pending: dict[str, list[PendingChange]] = {}
        self._completed: dict[str, list[dict[str, Any]]] = {}

    def begin_turn(self, thread_id: str) -> None:
        if not thread_id:
            raise ValueError("A thread_id is required for a safe receipt")
        with self._lock:
            self._pending[thread_id] = []
            self._completed[thread_id] = []

    def record_authorized(
        self,
        thread_id: str,
        action: dict[str, Any],
        decision: dict[str, Any],
        *,
        profile_prefix: str,
        original_request: str = "",
    ) -> None:
        """Snapshot an authorized change BEFORE the interrupted tool resumes.

        A rejection cannot yield a receipt. The CLI must call this only after
        the candidate accepts the content AND the tool operation.
        """
        if decision.get("type") not in {"approve", "edit"}:
            return
        original_path = _checked_path(action, profile_prefix)
        approved = action
        if decision["type"] == "edit":
            edited = decision.get("edited_action")
            if not isinstance(edited, dict):
                raise ValueError("Missing edited_action")
            if edited.get("name") != action.get("name"):
                raise ValueError("A candidate revision cannot switch the tool")
            if edited.get("args", {}).get("file_path") != action["args"]["file_path"]:
                raise ValueError("A candidate revision cannot switch target files")
            approved = edited
        baseline = _read_snapshot(original_path)
        before = _text(baseline)
        original_after = _project(action, None if baseline is None else before)
        revised_after = _project(approved, None if baseline is None else before)
        # Defense in depth: authorization/receipt calls may bypass interactive review.
        # Validate the approved projection, not just the originally proposed edit.
        issues = profile_content_issues(None if baseline is None else before, revised_after)
        if issues:
            raise ValueError("Authoritative profile content check failed: " + "; ".join(issues))
        try:
            validate_context_profile_write(
                str(original_path), revised_after, candidate_prefix=profile_prefix
            )
        except ContextRecordError as exc:
            raise ValueError("Invalid candidate-context write: " + str(exc)) from exc
        for conflict in newly_introduced_profile_conflicts(
            None if baseline is None else before, revised_after
        ):
            raise ValueError("Candidate profile conflict: " + conflict)
        if (
            decision["type"] == "edit"
            and action["name"] == "edit_file"
            and (approved["args"].get("old_string") != action["args"].get("old_string"))
        ):
            # Only a literal end-append request may re-anchor an edit. In that
            # case BOTH tool projections must preserve all old text: the
            # original is a pure insertion, the approved result is an append.
            # No general replacement or deletion can exploit this exception.
            if not (
                is_explicit_end_append_request(original_request)
                and baseline is not None
                and _single_insertion(before, original_after) is not None
                and revised_after.startswith(before)
                and bool(revised_after[len(before) :].strip())
            ):
                raise ValueError("A candidate revision cannot switch the edit anchor")
        # Defense in depth: edited insertions must remain insertions at the
        # same position even if a caller bypasses the terminal review UI.
        if decision["type"] == "edit" and baseline is not None:
            original_insertion = _single_insertion(before, original_after)
            if original_insertion is not None:
                approved_insertion = _single_insertion(before, revised_after)
                relocated_for_explicit_append = (
                    is_explicit_end_append_request(original_request)
                    and approved_insertion is not None
                    and approved_insertion[0] == len(before)
                )
                if approved_insertion is None or (
                    approved_insertion[0] != original_insertion[0]
                    and not relocated_for_explicit_append
                ):
                    raise ValueError(
                        "Candidate insertion revision changed original content or location"
                    )
        if is_explicit_begin_prepend_request(original_request):
            if is_explicit_end_append_request(original_request):
                raise ValueError("Conflicting prepend and append request")
            original_insertion = _single_insertion(before, original_after)
            approved_insertion = _single_insertion(before, revised_after)
            if (
                baseline is None
                or original_insertion is None
                or approved_insertion is None
                or original_insertion[0] != 0
                or approved_insertion[0] != 0
            ):
                raise ValueError(
                    "Prepend-only request must preserve the original at the start"
                )
        if is_explicit_end_append_request(original_request) and (
            baseline is None
            or _single_insertion(before, original_after) is None
            or not revised_after.startswith(before)
            or not revised_after[len(before) :].strip()
        ):
            raise ValueError(
                "Append-only request must preserve all original text at EOF"
            )
        orig_diff, _ = _diff(before, original_after)
        approved_diff, truncated = _diff(before, revised_after)
        change = PendingChange(
            file_path=str(original_path),
            tool=action["name"],
            originally_proposed_sha256=_hash(original_after.encode("utf-8")),
            candidate_approved_sha256=_hash(revised_after.encode("utf-8")),
            baseline_sha256=_hash(baseline),
            expected_bytes=revised_after.encode("utf-8"),
            original_diff=orig_diff,
            approved_diff=approved_diff,
            approved_diff_truncated=truncated,
            changed_by_candidate=(original_after != revised_after),
        )
        with self._lock:
            if thread_id not in self._pending:
                raise ValueError("Receipt turn has not been started")
            if any(p.file_path == change.file_path for p in self._pending[thread_id]):
                raise ValueError("Duplicate authorized write to same file in turn")
            self._pending[thread_id].append(change)

    @staticmethod
    def _verify(change: PendingChange, task_success: bool) -> dict[str, Any]:
        try:
            observed = _read_snapshot(Path(change.file_path))
            observed_hash = _hash(observed)
            verified = observed == change.expected_bytes and task_success
            if observed == change.expected_bytes and not task_success:
                status = "file_matches_but_task_failed"
            elif verified:
                status = "applied_verified"
            else:
                status = "not_verified"
            issue = None
        except (ValueError, OSError) as exc:
            observed_hash, verified, status = None, False, "not_verified"
            issue = f"File verification failed: {type(exc).__name__}"
        return {
            "schema": SCHEMA,
            "source": "cli_verified_candidate_profile_task",
            "agent": "candidate-profile-agent",
            "file_path": change.file_path,
            "tool": change.tool,
            "candidate_content_approved": True,
            "tool_execution_authorized": True,
            "candidate_revised": change.changed_by_candidate,
            "original_proposal_diff": change.original_diff,
            "approved_change_diff": change.approved_diff,
            "approved_change_diff_truncated": change.approved_diff_truncated,
            "baseline_sha256": change.baseline_sha256,
            "original_proposal_sha256": change.originally_proposed_sha256,
            "approved_result_sha256": change.candidate_approved_sha256,
            "observed_result_sha256": observed_hash,
            "status": status,
            "verified": verified,
            "verification_issue": issue,
        }

    def finish_profile_task(
        self, thread_id: str, *, task_success: bool
    ) -> list[dict[str, Any]]:
        """Finalize only once the candidate-profile-agent task returns."""
        with self._lock:
            pending = self._pending.pop(thread_id, [])
        results = [self._verify(change, task_success) for change in pending]
        with self._lock:
            self._completed.setdefault(thread_id, []).extend(results)
        return results

    def completed(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._completed.get(thread_id, [])]

    def unreported(self, thread_id: str) -> int:
        with self._lock:
            return len(self._pending.get(thread_id, []))


REGISTRY = ProfileReceiptRegistry()


def receipt_text(receipts: list[dict[str, Any]]) -> str:
    return "\n".join(
        RECEIPT_PREFIX + json.dumps(r, sort_keys=True, ensure_ascii=False)
        for r in receipts
    )


def add_receipts_to_tool_result(
    result: Any,
    receipts: list[dict[str, Any]],
    *,
    tool_call_id: str | None = None,
) -> Any:
    """Enrich a ToolMessage or Command[ToolMessage] without discarding it.

    Only called for `task(subagent_type='candidate-profile-agent')`. If a
    provider returns an unknown shape we fail rather than fabricate a success.
    """
    if not receipts:
        return result
    from dataclasses import replace

    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    def append(message: ToolMessage) -> ToolMessage:
        extra = receipt_text(receipts)
        if isinstance(message.content, str):
            content = message.content + "\n\n" + extra
        elif isinstance(message.content, list):
            content = [*message.content, {"type": "text", "text": extra}]
        else:
            raise TypeError("Unsupported ToolMessage content for profile receipt")
        return message.model_copy(update={"content": content})

    if isinstance(result, ToolMessage):
        return append(result)
    if isinstance(result, Command) and isinstance(result.update, dict):
        messages = result.update.get("messages")
        if isinstance(messages, list):
            # Commands can contain multiple messages. Only enrich the
            # ToolMessage from the actual task that just returned.
            matches = [
                m
                for m in messages
                if isinstance(m, ToolMessage)
                and (tool_call_id is None or m.tool_call_id == tool_call_id)
            ]
            if len(matches) == 1:
                chosen = matches[0]
                return replace(
                    result,
                    update={
                        **result.update,
                        "messages": [append(m) if m is chosen else m for m in messages],
                    },
                )
    raise TypeError("Cannot attach authoritative profile receipt to task result")


def thread_from_runtime(runtime: Any) -> str:
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        thread_id = config.get("configurable", {}).get("thread_id")
        if isinstance(thread_id, str):
            return thread_id
    return ""


def canonical_cli_report(receipts: list[dict[str, Any]]) -> None:
    """Deterministic user-visible result; does not depend on model wording."""
    for receipt in receipts:
        print("\nCandidate profile operation — verified runtime receipt")
        print("File:", receipt["file_path"])
        print("Status:", receipt["status"])
        print(
            "Candidate revised proposal:",
            "yes" if receipt["candidate_revised"] else "no",
        )
        print("Candidate-approved change:")
        print(receipt["approved_change_diff"])
        if receipt["approved_change_diff_truncated"]:
            print("[diff abbreviated; use file/hash checks for full verification]")
        if not receipt["verified"]:
            print(
                "WARNING: File content was not independently verified as the approved result."
            )
