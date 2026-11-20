"""Local, opt-in LangChain callback diagnostics; completely separate from LangSmith.

Only event metadata is shown. Never serialize prompts, inputs, tool arguments,
model text, tool-result bodies, exception messages or candidate file paths.
Callback coverage follows the installed LangChain/Deep Agents callback propagation;
a subagent delegated through `task` is explicitly identified when possible.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

_AGENTS = frozenset({
    "orchestrator", "application-agent", "candidate-profile-agent",
    "technical-review-agent", "recruiting-ats-review-agent",
})
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_TASK_AGENT = re.compile(
    r"['\"]subagent_type['\"]\s*:\s*['\"]"
    r"(application-agent|candidate-profile-agent|technical-review-agent|recruiting-ats-review-agent)['\"]"
)


def _ident(value: Any) -> str:
    """Never print uncontrolled text that might contain user content."""
    return value if isinstance(value, str) and _IDENT.fullmatch(value) else "unknown"


def _run_id(value: Any) -> str | None:
    return str(value) if value is not None else None


def _task_agent(inputs: Any, input_str: Any) -> str | None:
    """Extract only a known subagent name, not the confidential task prompt."""
    if isinstance(inputs, dict):
        value = inputs.get("subagent_type")
        if isinstance(value, str) and value in _AGENTS - {"orchestrator"}:
            return value
    if isinstance(input_str, str) and len(input_str) <= 20000:
        match = _TASK_AGENT.search(input_str)
        if match:
            return match.group(1)
    return None


class LocalDebugSink:
    """Synchronous stderr + optionally append-only user-owned JSONL sink.

    The JSONL file is created with 0600 permissions. Existing files must be
    regular, singly linked and owned by this user; symlink destinations are
    rejected. Tool callback metadata is not copied verbatim.
    """

    def __init__(self, *, path: str | Path | None = None, stream=None):
        self.stream = sys.stderr if stream is None else stream
        self.fd: int | None = None
        self.path = Path(path).expanduser() if path is not None else None
        self.tool_starts: dict[str, tuple[str, float, str]] = {}
        self.agents: dict[str, str] = {}
        self.tool_calls = 0
        self.tool_errors = 0
        self.delegations = 0
        if self.path is not None:
            flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
            if not hasattr(os, "O_NOFOLLOW"):
                raise OSError("Secure debug logging needs O_NOFOLLOW support")
            flags |= os.O_NOFOLLOW
            flags |= getattr(os, "O_CLOEXEC", 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid():
                    raise OSError("Debug destination must be an owned regular file")
                os.fchmod(fd, 0o600)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def start_turn(self):
        self.tool_starts.clear()
        self.agents.clear()
        self.tool_calls = self.tool_errors = self.delegations = 0
        self.emit("turn.start", agent="orchestrator")

    def end_turn(self, status: str):
        self.emit(
            "turn.end", agent="orchestrator", status=status,
            tool_calls=self.tool_calls,
            tool_errors=self.tool_errors,
            delegations=self.delegations,
        )

    def emit(self, event: str, **fields: Any):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": _ident(event),
            **fields,
        }
        # Print ONLY these already-filtered/synthetic fields; never print objects.
        parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
        print(f"[DEBUG] {record['timestamp']} {record['event']} {' '.join(parts)}".rstrip(),
              file=self.stream, flush=True)
        if self.fd is not None:
            data = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
            while data:
                count = os.write(self.fd, data)
                if count <= 0:
                    raise OSError("Debug event log write failed")
                data = data[count:]

    def role_for(self, parent_id: Any) -> str:
        return self.agents.get(_run_id(parent_id) or "", "orchestrator")

    def chain_start(self, serialized: Any, run_id: Any, parent_run_id: Any, name: Any):
        rid = _run_id(run_id)
        chain_name = name if isinstance(name, str) and name in _AGENTS else None
        if chain_name is None and isinstance(serialized, dict):
            candidate = serialized.get("name")
            if isinstance(candidate, str) and candidate in _AGENTS:
                chain_name = candidate
        role = chain_name or self.role_for(parent_run_id)
        if rid is not None:
            self.agents[rid] = role
        if chain_name is not None:
            self.emit("agent.start", agent=role, run_id=rid, parent_id=_run_id(parent_run_id))

    def chain_end(self, run_id: Any):
        # Generic chain ends are too noisy: the task tool provides subagent
        # completion and graph.* events provide orchestrator boundaries.
        self.agents.pop(_run_id(run_id) or "", None)

    def tool_start(self, serialized: Any, input_str: Any, inputs: Any,
                   run_id: Any, parent_run_id: Any, name: Any):
        rid = _run_id(run_id)
        tool_name = name if isinstance(name, str) else None
        if tool_name is None and isinstance(serialized, dict):
            tool_name = serialized.get("name")
        tool_name = _ident(tool_name)
        role = self.role_for(parent_run_id)
        delegated = _task_agent(inputs, input_str) if tool_name == "task" else None
        if delegated:
            self.delegations += 1
            role = delegated
            self.emit("delegate.start", agent=delegated, tool="task", run_id=rid)
        if rid is not None:
            self.tool_starts[rid] = (tool_name, monotonic(), role)
            self.agents[rid] = role
        self.tool_calls += 1
        self.emit("tool.start", agent=role, tool=tool_name, run_id=rid,
                  parent_id=_run_id(parent_run_id))

    def tool_end(self, output: Any, run_id: Any):
        rid = _run_id(run_id)
        name, started, role = self.tool_starts.pop(rid or "", ("unknown", monotonic(), "orchestrator"))
        status = getattr(output, "status", None)
        # A returned string may describe a tool failure or rejected operation.
        # Never claim success from a callback alone.
        outcome = "error" if status == "error" else "returned_unverified"
        if outcome == "error":
            self.tool_errors += 1
        self.emit("tool.end", agent=role, tool=name, outcome=outcome,
                  tool_message_status=status if status in ("success", "error") else "unknown",
                  run_id=rid, elapsed_ms=max(0, int((monotonic() - started) * 1000)))
        if name == "task":
            self.emit("delegate.end", agent=role, outcome=outcome, run_id=rid)
        self.agents.pop(rid or "", None)

    def tool_error(self, error: BaseException, run_id: Any):
        rid = _run_id(run_id)
        name, started, role = self.tool_starts.pop(rid or "", ("unknown", monotonic(), "orchestrator"))
        self.tool_errors += 1
        self.emit("tool.error", agent=role, tool=name,
                  error_type=_ident(type(error).__name__), run_id=rid,
                  elapsed_ms=max(0, int((monotonic() - started) * 1000)))
        if name == "task":
            self.emit("delegate.end", agent=role, outcome="error", run_id=rid)
        self.agents.pop(rid or "", None)


def make_callback_handler(sink: LocalDebugSink):
    """Construct a LangChain handler only when debugging is enabled."""
    from langchain_core.callbacks import BaseCallbackHandler

    class _LocalHandler(BaseCallbackHandler):
        # Never turn diagnostics into a way around HITL or execution policy.
        raise_error = False

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
            sink.chain_start(serialized, run_id, parent_run_id, kwargs.get("name"))

        def on_chain_end(self, outputs, *, run_id, **kwargs):
            sink.chain_end(run_id)

        def on_chain_error(self, error, *, run_id, **kwargs):
            sink.emit("chain.error", agent=sink.role_for(run_id),
                      error_type=_ident(type(error).__name__), run_id=_run_id(run_id))
            sink.chain_end(run_id)

        def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
            sink.tool_start(serialized, input_str, kwargs.get("inputs"),
                            run_id, parent_run_id, kwargs.get("name"))

        def on_tool_end(self, output, *, run_id, **kwargs):
            sink.tool_end(output, run_id)

        def on_tool_error(self, error, *, run_id, **kwargs):
            sink.tool_error(error, run_id)

        def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
            sink.emit("model.start", agent=sink.role_for(parent_run_id), run_id=_run_id(run_id))

        def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
            sink.emit("model.start", agent=sink.role_for(parent_run_id), run_id=_run_id(run_id))

        def on_llm_end(self, response, *, run_id, **kwargs):
            sink.emit("model.end", run_id=_run_id(run_id))

        def on_llm_error(self, error, *, run_id, **kwargs):
            sink.emit("model.error", error_type=_ident(type(error).__name__),
                      run_id=_run_id(run_id))

    return _LocalHandler()
