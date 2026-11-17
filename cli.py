"""Small local terminal UI for Deep Agents with scoped LangSmith tracing.

CLI defaults to tracing OFF, regardless of environment. `--trace` enables it
only for this process's graph invocations and resumes. No global env mutation.

This is a prototype for single-user interactive Guix sessions, not an HTTP
server. It uses InMemorySaver and cannot survive process restarts. LangGraph
server mode needs its own per-run tracing controls and durable checkpointer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from application_task_status import REGISTRY as APPLICATION_TASK_REGISTRY
from interactive_input import (
    configure_readline,
    edit_chat_message,
    remember_chat_message,
)
from permissions import configured_profile_prefix
from profile_cancel_guard import ProfileCancelGuard, RejectedWriteRetryLoop
from profile_receipts import REGISTRY, canonical_cli_report
from profile_review import ReviewError, reject, review_profile_action
from write_decision_audit import WriteDecisionAudit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive Career Agent")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Enable LangSmith tracing for this CLI invocation only (off by default).",
    )
    parser.add_argument(
        "--trace-project",
        default=None,
        metavar="NAME",
        help="LangSmith project for --trace; otherwise use existing settings.",
    )
    parser.add_argument(
        "--message",
        metavar="PROMPT",
        help="Submit a single prompt instead of entering an interactive chat loop.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print newly generated message/tool diagnostics (off by default).",
    )
    return parser


def _is_profile_write(action: dict[str, Any], prefix: str) -> bool:
    if action.get("name") not in {"write_file", "edit_file", "delete"}:
        return False
    arguments = action.get("args") or {}
    if not isinstance(arguments, dict):
        return False
    target = arguments.get("file_path")
    return isinstance(target, str) and (
        target == prefix or target.startswith(prefix.rstrip("/") + "/")
    )


def _prompt_approvals(
    request: dict[str, Any],
    *,
    profile: str,
    original_request: str = "",
    guard: ProfileCancelGuard | None = None,
    thread_id: str = "",
    write_audit: WriteDecisionAudit | None = None,
    input_fn=input,
) -> dict[str, list[dict[str, str]]]:
    """Conservative review of a batched HumanInTheLoopMiddleware interrupt.

    Every action must be authorized separately. For candidate-profile
    writes, the terminal requests a candidate content decision and a
    tool-execution decision. Future browser UIs must implement equivalent
    safeguards.
    """
    actions = request.get("action_requests")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Unexpected HITL request (missing action_requests)")
    review_configs = request.get("review_configs")
    if not isinstance(review_configs, list) or len(review_configs) != len(actions):
        raise ValueError("Missing or mismatched HITL review_configs")
    decisions = []
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise TypeError("Unexpected HITL action format")
        if _is_profile_write(action, profile) and guard is not None and guard.cancelled:
            # Do NOT present this as requiring a fresh candidate approval.
            print(
                "Blocked a repeated candidate-profile tool request after explicit cancellation."
            )
            decisions.append(guard.block_retry())
            continue
        if (
            _is_profile_write(action, profile)
            and guard is not None
            and guard.already_authorized_path(action)
        ):
            print(
                "Blocked an additional profile write to content already approved this turn."
            )
            decisions.append(guard.block_additional_write())
            continue
        print("\nAction requiring human approval:")
        print(json.dumps(action, indent=2, ensure_ascii=False, default=str))
        if _is_profile_write(action, profile):
            cfg = review_configs[i]
            if cfg.get("action_name") != action.get("name"):
                decisions.append(reject("HITL tool mismatch; no write authorized"))
                continue
            try:
                import os
                import shutil

                print(
                    "[EDITOR DEBUG]",
                    "VISUAL =",
                    repr(os.environ.get("VISUAL")),
                    "EDITOR =",
                    repr(os.environ.get("EDITOR")),
                    "nano =",
                    shutil.which("nano"),
                )
                decision = review_profile_action(
                    action,
                    profile_prefix=profile,
                    allowed_decisions=cfg.get("allowed_decisions", []),
                    original_request=original_request,
                    input_fn=input_fn,
                    # For known append-only tasks, use
                    # scope_check=append_only_scope.
                )
            except ReviewError as exc:
                print(f"Unsafe profile proposal rejected: {exc}")
                decision = reject(
                    "Unsafe profile operation; resubmit corrected proposal"
                )
            if decision.get("type") in {"approve", "edit"}:
                # Record the candidate-approved final operation while still
                # paused. The subagent task wrapper will verify the file AFTER
                # the actual tool returns, before forwarding a receipt.
                try:
                    REGISTRY.record_authorized(
                        thread_id,
                        action,
                        decision,
                        profile_prefix=profile,
                        original_request=original_request,
                    )
                except (ValueError, OSError) as exc:
                    print(f"Profile receipt preparation failed: {exc}")
                    decision = reject(
                        "Cannot track an auditable profile write; no authorization"
                    )
            if guard is not None:
                guard.record(decision, action)
            decisions.append(decision)
            continue
        answer = input_fn("Approve this tool action? [y/N] ").strip().lower()
        authorized = answer in {"y", "yes"}
        if write_audit is not None:
            # Record the *human's* decision; do not trust the subagent report.
            write_audit.record(action, authorized=authorized)
        if authorized:
            decisions.append(
                {
                    "type": "approve",
                },
            )
        else:
            decisions.append(
                {
                    "type": "reject",
                    "message": (
                        "User rejected this tool operation. Do not "
                        "claim it succeeded or report the target file "
                        "as created/modified."
                    ),
                },
            )
    return {
        "decisions": decisions,
    }


def _run_to_completion(
    graph,
    payload,
    *,
    config,
    profile: str,
    original_request: str = "",
    guard: ProfileCancelGuard | None = None,
    write_audit: WriteDecisionAudit | None = None,
    input_fn=input,
):
    """Invoke and safely resume batched HITL interrupts on the same \
    thread."""
    result = graph.invoke(payload, config=config)
    while True:
        pending = result.get("__interrupt__", ()) if isinstance(result, dict) else ()
        if not pending:
            return result
        if len(pending) != 1:
            raise RuntimeError(
                "Multiple simultaneous interrupts require a richer UI; "
                "nothing was automatically approved."
            )
        value = getattr(pending[0], "value", None)
        if not isinstance(value, dict):
            raise TypeError("Unsupported HITL interrupt type; no action approved")
        response = _prompt_approvals(
            value,
            profile=profile,
            original_request=original_request,
            guard=guard,
            thread_id=config["configurable"]["thread_id"],
            write_audit=write_audit,
            input_fn=input_fn,
        )
        from langgraph.types import Command

        result = graph.invoke(Command(resume=response), config=config)


def _show_response(result: Any) -> None:
    if not isinstance(result, dict):
        print(result)
        return
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            content = getattr(msg, "content", "")
            tool_calls = getattr(msg, "tool_calls", [])

            if isinstance(content, str) and content.strip():
                print(f"\nAssistant: {content}")
                return
            if tool_calls:
                continue

            print(
                "\n[WARNING] The model returned an empty "
                "final response after tool execution.",
                file=sys.stderr,
            )
            print(
                "The requested task may have been executed, "
                "but its final result was not reported."
            )
            return
    print("\nNo final assistant message was returned.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.trace_project and not args.trace:
        print("--trace-project requires --trace", file=sys.stderr)
        return 2

    # Defer expensive imports (and model creation) until after --help
    # parsing.
    from langsmith import tracing_context

    from agent import make_graph

    graph = make_graph()
    profile = configured_profile_prefix()
    session_config = {
        "configurable": {
            "thread_id": str(uuid4()),
        },
    }

    # LangGraph normally returns the full thread history on each
    # invocation.  Track the already-seen prefix so --debug does not
    # replay old messages.
    debug_message_count = 0

    print("Career Agent — use /quit or /exit (or Ctrl+C/Ctrl+D) to exit")
    if args.trace:
        print("LangSmith tracing ON: candidate data may be sent to LangSmith")
    else:
        print("LangSmith tracing OFF")

    def run_once(message: str) -> None:
        nonlocal session_config, debug_message_count
        # The guard is fresh for EACH user turn, including all interrupt resumes.
        guard = ProfileCancelGuard()
        write_audit = WriteDecisionAudit()
        thread_id = session_config["configurable"]["thread_id"]
        REGISTRY.begin_turn(thread_id)
        APPLICATION_TASK_REGISTRY.begin_turn(thread_id, write_audit)
        try:
            with tracing_context(enabled=args.trace, project_name=args.trace_project):
                answer = _run_to_completion(
                    graph,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": message,
                            },
                        ],
                    },
                    config=session_config,
                    profile=profile,
                    original_request=message,
                    guard=guard,
                    write_audit=write_audit,
                )
        except RejectedWriteRetryLoop as exc:
            # The graph may still be suspended on the old thread. Fail
            # closed: abandon that thread. This loses conversation
            # context, so say so.
            print(f"\nProfile change CANCELLED. {exc}")
            session_config = {
                "configurable": {
                    "thread_id": str(uuid4()),
                },
            }
            debug_message_count = 0
            print(
                "Started a fresh conversation thread; prior thread remains abandoned."
            )
            return True
        except Exception as exc:
            # A model/provider error can leave partial state in the graph
            # checkpoint (e.g. an unsupported PDF file content block).
            # Never resume this thread or claim that prior tools did not run.
            print(
                f"\nAgent turn failed ({type(exc).__name__}): {exc}",
                file=sys.stderr,
            )
            print(
                "Turn outcome is unknown: any tools approved earlier in the "
                "turn may already have executed. Verify files before retrying."
            )
            session_config = {
                "configurable": {"thread_id": str(uuid4())},
            }
            debug_message_count = 0
            print(
                "Started a fresh conversation thread; the failed thread was abandoned."
            )
            return False
        # --debug is opt-in. Avoid replaying LangGraph's accumulated
        # --history.
        messages = answer.get("messages", []) if isinstance(answer, dict) else []
        if not isinstance(messages, list):
            messages = []
        if args.debug:
            from langchain_core.messages import AIMessage, ToolMessage

            start_at = (
                debug_message_count if len(messages) >= debug_message_count else 0
            )
            for msg in messages[start_at:]:
                content = getattr(msg, "content", "")
                metadata = getattr(msg, "response_metadata", {})
                kwargs = getattr(msg, "additional_kwargs", {})

                print(
                    "[DEBUG]",
                    "type=",
                    getattr(msg, "type", None),
                    "content_length=",
                    len(str(content)),
                    "tool_calls=",
                    len(getattr(msg, "tool_calls", []) or []),
                    "reasoning_present=",
                    bool(kwargs.get("reasoning_content")),
                    "response_metadata=",
                    metadata,
                    file=sys.stderr,
                )

                if isinstance(msg, AIMessage):
                    for call in msg.tool_calls:
                        print(
                            "[TOOL CALL]",
                            call["name"],
                            call["args"],
                            file=sys.stderr,
                        )
                elif isinstance(msg, ToolMessage):
                    print(
                        "[TOOL RESULT]",
                        msg.name,
                        getattr(msg, "status", "unknown"),
                        str(msg.content)[:200],
                        file=sys.stderr,
                    )
        debug_message_count = len(messages)

        # Exactly ONE outcome per user turn, outside the debug loop.
        if guard.cancelled:
            # Ignore misleading model output after explicit cancellation.
            print(
                "\nCandidate profile-change request CANCELLED. No profile edit authorized."
            )
            return True

        receipts = REGISTRY.completed(thread_id)
        if receipts:
            # Runtime-verified profile outcome, not an agent's narrative.
            canonical_cli_report(receipts)
        else:
            rejection_report = write_audit.rejection_report()
            if rejection_report is not None:
                print("\n" + rejection_report)
            else:
                _show_response(answer)
        if REGISTRY.unreported(thread_id):
            print(
                "WARNING: Profile write authorization lacks a completed task receipt."
            )
        return True

    try:
        if args.message is not None:
            if not run_once(args.message):
                return 1
        else:
            # Readline edits only the interactive chat input. Automatic history
            # is disabled so HITL approvals are never collected as chat history.
            line_editor = configure_readline()
            while True:
                message = input("\nYou> ").strip()
                if message.lower() == "/edit":
                    # Multiline prompts are composed in $VISUAL/$EDITOR.
                    # An empty or cancelled editor session submits nothing.
                    message = edit_chat_message()
                    if message is None:
                        continue
                else:
                    if message.lower() in {"quit", "exit", "/quit", "/exit"}:
                        break
                    if message:
                        remember_chat_message(line_editor, message)
                if not message.strip():
                    continue
                run_once(message)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting; no pending tool actions were automatically approved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
