"""Authoritative authorization feedback on delegated application-agent tasks.

The CLI records file-write HITL decisions. On task completion, middleware
replaces an unreliable application-agent task narrative if any file action was
rejected. This does *not* assert that authorized writes executed, and does not
change the original HITL rejection or filesystem permissions.

Single-process/one-active-application-task-per-thread prototype; not a durable
or concurrency-safe cross-process audit system. Other application tasks should
not run concurrently in the same thread while using this prototype.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import Any

from write_decision_audit import WriteDecision, WriteDecisionAudit

PREFIX = "APPLICATION_WRITE_AUTHORIZATION_V1: "


@dataclass
class _Turn:
    audit: WriteDecisionAudit
    active_tasks: dict[str, int]


class ApplicationTaskStatusRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._turns: dict[str, _Turn] = {}

    def begin_turn(self, thread_id: str, audit: WriteDecisionAudit) -> None:
        if not thread_id or not isinstance(audit, WriteDecisionAudit):
            raise ValueError("A thread ID and a WriteDecisionAudit are required")
        with self._lock:
            self._turns[thread_id] = _Turn(audit=audit, active_tasks={})

    def start_task(self, thread_id: str, call_id: str) -> None:
        if not thread_id or not call_id:
            raise ValueError("A thread ID and task call ID are required")
        with self._lock:
            turn = self._turns.get(thread_id)
            if turn is None:
                raise RuntimeError("Application audit not initialized for this thread")
            # An interrupted tool wrapper may restart on Command(resume=...).
            # Preserve the original start index through such restarts.
            if call_id not in turn.active_tasks:
                if turn.active_tasks:
                    raise RuntimeError(
                        "Concurrent application tasks require per-task audit routing"
                    )
                turn.active_tasks[call_id] = len(turn.audit.decisions)

    def finish_task(self, thread_id: str, call_id: str) -> list[WriteDecision]:
        with self._lock:
            turn = self._turns.get(thread_id)
            if turn is None or call_id not in turn.active_tasks:
                raise RuntimeError("No tracked application task to finalize")
            start = turn.active_tasks.pop(call_id)
            return list(turn.audit.decisions[start:])


REGISTRY = ApplicationTaskStatusRegistry()


def rejected_write_status(decisions: list[WriteDecision]) -> str | None:
    """Produce authoritative *authorization* outcome, not execution claim."""
    if not any(not d.authorized for d in decisions):
        return None
    actions = [
        {
            "tool": d.tool,
            "file_path": d.file_path,
            "authorization": "authorized_execution_unverified"
            if d.authorized
            else "rejected_not_authorized",
        }
        for d in decisions
    ]
    payload: dict[str, Any] = {
        "schema": "career.application_write_authorization.v1",
        "source": "cli_human_tool_decisions",
        "agent": "application-agent",
        "actions": actions,
        "verification": "authorization_only",
    }
    lines = [PREFIX + json.dumps(payload, sort_keys=True)]
    lines.append(
        "The application-agent task returned, but the human REJECTED at least "
        "one file operation. A rejected operation did not receive authorization "
        "and MUST NOT be reported as successfully executed."
    )
    if any(d.authorized for d in decisions):
        lines.append(
            "Other operations were authorized; this receipt does not verify "
            "whether any authorized operation actually modified the filesystem."
        )
    else:
        lines.append(
            "No filesystem modification was authorized for this application "
            "task. Report REJECTED, not COMPLETED; do not retry without a new request."
        )
    return "\n\n".join(lines)


def replace_task_narrative(result: Any, text: str, *, tool_call_id: str) -> Any:
    """Replace an untrustworthy task *narrative* without altering tool status.

    The `task` tool may complete normally even when a nested write is denied.
    Preserve the ToolMessage's `status`: it describes task execution, not the
    write. Supports ToolMessage and Command(update={messages: [...]}) results.
    """
    from dataclasses import replace

    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    def new_message(msg: ToolMessage) -> ToolMessage:
        return msg.model_copy(update={"content": text})

    if isinstance(result, ToolMessage):
        if result.tool_call_id != tool_call_id:
            raise ValueError("Task result tool_call_id mismatch")
        return new_message(result)
    if isinstance(result, Command) and isinstance(result.update, dict):
        msgs = result.update.get("messages")
        if isinstance(msgs, list):
            matches = [
                m
                for m in msgs
                if isinstance(m, ToolMessage) and m.tool_call_id == tool_call_id
            ]
            if len(matches) == 1:
                chosen = matches[0]
                return replace(
                    result,
                    update={
                        **result.update,
                        "messages": [
                            new_message(m) if m is chosen else m for m in msgs
                        ],
                    },
                )
    raise TypeError("Cannot safely replace an application task narrative")
