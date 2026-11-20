"""Local terminal UI for Deep Agents with independent local debug and LangSmith controls.

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
from pathlib import Path
from typing import Any
from uuid import uuid4

from application_task_status import REGISTRY as APPLICATION_TASK_REGISTRY
from application_write_guard import ApplicationWriteError, is_application_path, require_initialized_document_path
from interactive_input import (
    configure_readline,
    edit_chat_message,
    remember_chat_message,
)
from permissions import configured_profile_prefix
from base_documents import (
    BaseDocumentSelectionError, list_base_documents, resolve_base_document,
)
from profile_review import ReviewError, review_profile_action, reject
from profile_cancel_guard import ProfileCancelGuard, RejectedWriteRetryLoop
from profile_receipts import REGISTRY, canonical_cli_report
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
        help="Stream live local agent/subagent and tool events, without LangSmith.",
    )
    parser.add_argument(
        "--debug-file", metavar="PATH",
        help="Append local structured debug events to a 0600 JSONL file; implies --debug.",
    )
    parser.add_argument(
        "--base-resume", metavar="FILENAME|?", default=None,
        help="Use a historical .tex resume in candidate/base-documents; '?' lists a terminal picker.",
    )
    parser.add_argument(
        "--base-cover", metavar="FILENAME|?", default=None,
        help="Use a historical .tex cover letter in candidate/base-documents; '?' lists a terminal picker.",
    )
    parser.add_argument(
        "--list-base-documents", action="store_true",
        help="List available historical .tex source filenames and exit before starting the model.",
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
    workspace_root: Path | None = None,
    local_debug=None,
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
    def add_decision(action, decision):
        decisions.append(decision)
        if local_debug is not None:
            from local_debug import _ident
            local_debug.emit(
                "approval.decision", tool=_ident(action.get("name")),
                decision={"approve": "approved", "edit": "edited", "reject": "rejected"}.get(
                    decision.get("type"), "rejected"),
            )

    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise TypeError("Unexpected HITL action format")
        if local_debug is not None:
            from local_debug import _ident
            local_debug.emit("approval.request", tool=_ident(action.get("name")))
        if _is_profile_write(action, profile) and guard is not None and guard.cancelled:
            # Do NOT present this as requiring a fresh candidate approval.
            print(
                "Blocked a repeated candidate-profile tool request after explicit cancellation."
            )
            add_decision(action, guard.block_retry())
            continue
        if (
            _is_profile_write(action, profile)
            and guard is not None
            and guard.already_authorized_path(action)
        ):
            print(
                "Blocked an additional profile write to content already approved this turn."
            )
            add_decision(action, guard.block_additional_write())
            continue
        # Fail closed before human approval for ordinary filesystem writes
        # aimed at an uninitialized or differently normalized job folder.
        if action.get("name") in {"write_file", "edit_file", "delete"}:
            args = action.get("args") or {}
            path = args.get("file_path") if isinstance(args, dict) else None
            if is_application_path(path):
                try:
                    require_initialized_document_path(
                        workspace_root if workspace_root is not None else Path.cwd(), path
                    )
                except (ApplicationWriteError, OSError) as exc:
                    reason = f"Application write rejected before approval: {exc}"
                    print(reason)
                    if write_audit is not None:
                        write_audit.record(action, authorized=False)
                    add_decision(action, reject(reason))
                    continue
        print("\nAction requiring human approval:")
        print(json.dumps(action, indent=2, ensure_ascii=False, default=str))
        if _is_profile_write(action, profile):
            cfg = review_configs[i]
            if cfg.get("action_name") != action.get("name"):
                add_decision(action, reject("HITL tool mismatch; no write authorized"))
                continue
            try:
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
            add_decision(action, decision)
            continue
        answer = input_fn("Approve this tool action? [y/N] ").strip().lower()
        authorized = answer in {"y", "yes"}
        if write_audit is not None:
            # Record the *human's* decision; do not trust the subagent report.
            write_audit.record(action, authorized=authorized)
        if authorized:
            add_decision(action, {"type": "approve"})
        else:
            add_decision(action, {
                "type": "reject",
                "message": (
                    "User rejected this tool operation. Do not "
                    "claim it succeeded or report the target file "
                    "as created/modified."
                ),
            })
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
    workspace_root: Path | None = None,
    local_debug=None,
):
    """Invoke and safely resume batched HITL interrupts on the same \
    thread."""
    # Attach callbacks to every invocation AND interrupt resume. The debug
    # handler is per user turn; unrelated LangSmith controls remain untouched.
    run_config = config
    if local_debug is not None:
        from local_debug import make_callback_handler
        run_config = dict(config)
        existing = run_config.get("callbacks") or []
        if not isinstance(existing, list):
            raise TypeError("Unsupported callbacks config for local debug")
        run_config["callbacks"] = [*existing, make_callback_handler(local_debug)]
        local_debug.emit("graph.invoke", agent="orchestrator")
    result = graph.invoke(payload, config=run_config)
    while True:
        pending = result.get("__interrupt__", ()) if isinstance(result, dict) else ()
        if not pending:
            if local_debug is not None:
                local_debug.emit("graph.returned", agent="orchestrator")
            return result
        if len(pending) != 1:
            raise RuntimeError(
                "Multiple simultaneous interrupts require a richer UI; "
                "nothing was automatically approved."
            )
        value = getattr(pending[0], "value", None)
        if local_debug is not None:
            local_debug.emit("graph.interrupted", agent="orchestrator")
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
            workspace_root=workspace_root,
            local_debug=local_debug,
        )
        from langgraph.types import Command

        if local_debug is not None:
            local_debug.emit("graph.resumed", agent="orchestrator")
        result = graph.invoke(Command(resume=response), config=run_config)


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

    # Resolve and show source selections before importing the model or
    # creating a graph. No new dependency (questionary) is required.
    try:
        if args.list_base_documents:
            names = list_base_documents(Path.cwd())
            print("Historical LaTeX documents in /workspace/candidate/base-documents:")
            for name in names:
                print("  " + name)
            if not names:
                print("  (none; add .tex files to candidate/base-documents on the host)")
            return 0
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        base_resume = resolve_base_document(
            Path.cwd(), args.base_resume, kind="resume", interactive=interactive,
        )
        base_cover = resolve_base_document(
            Path.cwd(), args.base_cover, kind="cover", interactive=interactive,
        )
    except (BaseDocumentSelectionError, EOFError, KeyboardInterrupt) as exc:
        print(f"Document selection failed: {exc}", file=sys.stderr)
        return 2

    # Defer expensive imports (and model creation) until after --help
    # parsing.
    from langsmith import tracing_context

    from agent import make_graph

    graph = (make_graph(base_resume=base_resume, base_cover=base_cover)
             if base_resume is not None or base_cover is not None
             else make_graph())
    print("Resume source:", (
        "/workspace/candidate/base-documents/" + base_resume if base_resume
        else "/workspace/templates/resume_template.tex"))
    print("Cover source:", (
        "/workspace/candidate/base-documents/" + base_cover if base_cover
        else "/workspace/templates/cover_letter_template.tex"))
    selection_notice = ""
    if base_resume is not None or base_cover is not None:
        selection_notice = (
            "[Selected document sources for this agent invocation. "
            "The approved prepare_latex_documents tool is bound to these selections; "
            "do not substitute other sources.]\n"
            "Resume: " + (base_resume or "default resume_template.tex") + "\n"
            "Cover: " + (base_cover or "default cover_letter_template.tex") + "\n\n"
        )
    first_turn = True
    profile = configured_profile_prefix()
    session_config = {
        "configurable": {
            "thread_id": str(uuid4()),
        },
    }

    local_debug = None
    if args.debug or args.debug_file:
        from local_debug import LocalDebugSink
        if args.debug_file is not None:
            # The CLI is outside the graph permission middleware: preserve
            # candidate originals and source template immutability here too.
            destination = Path(args.debug_file).expanduser().resolve(strict=False)
            for protected in (Path.cwd() / "candidate", Path.cwd() / "templates"):
                if destination.is_relative_to(protected.resolve(strict=False)):
                    print("Debug log must not be stored under candidate/ or templates/", file=sys.stderr)
                    return 2
        try:
            local_debug = LocalDebugSink(path=args.debug_file)
        except OSError as exc:
            print(f"Cannot create secure local debug log: {exc}", file=sys.stderr)
            return 2

    print("Career Agent — use /quit or /exit (or Ctrl+C/Ctrl+D) to exit")
    if args.trace:
        print("LangSmith tracing ON: candidate data may be sent to LangSmith")
    else:
        print("LangSmith tracing OFF")

    def run_once(message: str) -> bool:
        nonlocal session_config, first_turn
        # The guard is fresh for EACH user turn, including all interrupt resumes.
        guard = ProfileCancelGuard()
        write_audit = WriteDecisionAudit()
        thread_id = session_config["configurable"]["thread_id"]
        REGISTRY.begin_turn(thread_id)
        APPLICATION_TASK_REGISTRY.begin_turn(thread_id, write_audit)
        if local_debug is not None:
            local_debug.start_turn()
        try:
            with tracing_context(enabled=args.trace, project_name=args.trace_project):
                answer = _run_to_completion(
                    graph,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": selection_notice + message if first_turn else message,
                            },
                        ],
                    },
                    config=session_config,
                    profile=profile,
                    original_request=message,
                    guard=guard,
                    write_audit=write_audit,
                    workspace_root=Path.cwd(),
                    local_debug=local_debug,
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
            first_turn = True
            if local_debug is not None:
                local_debug.end_turn("cancelled")
            print("Started a fresh conversation thread; prior thread remains abandoned.")
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
            first_turn = True
            if local_debug is not None:
                from local_debug import _ident
                local_debug.emit("turn.error", error_type=_ident(type(exc).__name__))
                local_debug.end_turn("failed")
            print("Started a fresh conversation thread; the failed thread was abandoned.")
            return False
        first_turn = False

        # Exactly ONE outcome per user turn, outside the debug loop.
        if guard.cancelled:
            if local_debug is not None:
                local_debug.end_turn("cancelled")
            # Ignore misleading model output after explicit cancellation.
            print(
                "\nCandidate profile-change request CANCELLED. No profile edit authorized."
            )
            return True

        if local_debug is not None:
            # A graph return is not evidence that application artifacts exist.
            local_debug.end_turn("returned_unverified")
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
    finally:
        if local_debug is not None:
            local_debug.close()

if __name__ == "__main__":
    raise SystemExit(main())
