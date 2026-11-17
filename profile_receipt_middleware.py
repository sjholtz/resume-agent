"""Attach verified CLI profile-change receipts to the subagent `task` result.

Installed on the ORCHESTRATOR graph (not as a candidate-profile filesystem tool).
The result is consumed by the orchestrator in the regular task ToolMessage.
"""

from __future__ import annotations

from langchain.agents.middleware import wrap_tool_call

from application_task_status import (
    REGISTRY as APP_REGISTRY,
)
from application_task_status import (
    rejected_write_status,
    replace_task_narrative,
)
from profile_receipts import (
    REGISTRY,
    add_receipts_to_tool_result,
    thread_from_runtime,
)


@wrap_tool_call
def profile_task_receipt_middleware(request, handler):
    call = request.tool_call
    is_profile_task = (
        call.get("name") == "task"
        and (call.get("args") or {}).get("subagent_type") == "candidate-profile-agent"
    )
    is_app_task = (
        call.get("name") == "task"
        and (call.get("args") or {}).get("subagent_type") == "application-agent"
    )
    thread_id = (
        thread_from_runtime(request.runtime) if (is_profile_task or is_app_task) else ""
    )
    if (is_profile_task or is_app_task) and not thread_id:
        raise RuntimeError("Delegated task audit requires a thread_id")
    call_id = call.get("id")
    if is_app_task:
        APP_REGISTRY.start_task(thread_id, call_id)
    result = handler(request)
    if is_app_task:
        decisions = APP_REGISTRY.finish_task(thread_id, call_id)
        status_text = rejected_write_status(decisions)
        if status_text is not None:
            return replace_task_narrative(result, status_text, tool_call_id=call_id)
        return result
    if not is_profile_task:
        return result
    task_success = getattr(result, "status", "success") != "error"
    receipts = REGISTRY.finish_profile_task(thread_id, task_success=task_success)
    return add_receipts_to_tool_result(result, receipts, tool_call_id=call.get("id"))
