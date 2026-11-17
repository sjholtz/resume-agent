"""Conservative CLI review of interrupted Deep Agents candidate-profile writes.

A review returns one LangChain HITL decision; it does NOT perform a write.
Compatible with the batched HITL response: Command(resume={"decisions": [...]})

Security boundaries:
  * same tool and file_path throughout review
  * insertion revision edits only newly inserted text; regular replacement
    revisions edit the proposed replacement text
  * only a literal end-of-file append may be mechanically re-anchored;
    the final exact operation still needs both approvals and a receipt
  * complete diff shown before candidate consent and tool approval
  * baseline must not change during review (best effort; not atomic)
  * caller's role-based filesystem policy remains authoritative

Semantic checks must be implemented separately, using original request and
candidate evidence; this module does not claim a valid edit is factually true.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from copy import deepcopy
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Any

from candidate_context_records import ContextRecordError, validate_context_profile_write
from profile_conflicts import newly_introduced_profile_conflicts

MAX_CONTENT_BYTES = 1_000_000
MAX_REVISIONS = 5

# A deliberate *request-level* cancellation, distinct from asking for a
# corrected proposal. HITL's native reject decision only blocks one tool call.
CANCEL_PROFILE_REQUEST_MESSAGE = (
    "CANDIDATE_CANCELLED_PROFILE_REQUEST: The candidate explicitly cancelled "
    "this entire profile-change request. The tool was not executed. "
    "Do not retry via edit_file, write_file, delegation, or any alternative. "
    "Do not request approval again in this user turn. Report CANCELLED, not PENDING_APPROVAL."
)


class ReviewError(ValueError):
    pass


def _checked_path(action: dict[str, Any], profile_prefix: str) -> Path:
    name = action.get("name")
    if name not in {"edit_file", "write_file"}:
        raise ReviewError(f"Unsupported profile revision tool: {name!r}")
    args = action.get("args")
    if not isinstance(args, dict):
        raise ReviewError("Missing tool arguments")
    raw = args.get("file_path")
    if not isinstance(raw, str) or not raw.startswith("/") or "\\" in raw:
        raise ReviewError("file_path must be an absolute POSIX path")
    if "//" in raw or any(p in {".", ".."} for p in raw.split("/")):
        raise ReviewError("Unsafe path segments")
    prefix = PurePosixPath(profile_prefix)
    path = PurePosixPath(raw)
    if path == prefix or prefix not in path.parents:
        raise ReviewError("File is outside the candidate-profile subtree")
    real_prefix = Path(profile_prefix).resolve(strict=False)
    resolved = Path(raw).resolve(strict=False)
    if not resolved.is_relative_to(real_prefix):
        raise ReviewError("Resolved file path escapes the candidate-profile subtree")
    return Path(raw)


def _read_snapshot(path: Path) -> bytes | None:
    # In the user's Guix CLI, /workspace is a mounted real directory.
    # Fail closed if a provider-backed virtual path cannot be verified locally.
    if path.is_symlink():
        raise ReviewError("Refusing to review a symlink target")
    if not path.exists():
        if not path.parent.is_dir():
            raise ReviewError("Parent directory is missing; re-propose this operation")
        return None
    if not path.is_file():
        raise ReviewError("Target is not a regular file")
    try:
        raw = path.read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewError("Cannot safely read a UTF-8 snapshot") from exc
    if len(raw) > MAX_CONTENT_BYTES:
        raise ReviewError("File is too large for interactive review")
    return raw


def _text(blob: bytes | None) -> str:
    return "" if blob is None else blob.decode("utf-8")


def _candidate_field(action: dict[str, Any]) -> str:
    return "content" if action["name"] == "write_file" else "new_string"


def _project(action: dict[str, Any], before: str | None) -> str:
    args = action["args"]
    if action["name"] == "write_file":
        proposed = args.get("content")
        if not isinstance(proposed, str):
            raise ReviewError("write_file.content must be text")
    elif action["name"] == "edit_file":
        if before is None:
            raise ReviewError("Cannot edit a missing file")
        old, new = args.get("old_string"), args.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ReviewError(
                "edit_file requires nonempty old_string and text new_string"
            )
        if args.get("replace_all", False) is not False:
            raise ReviewError("Bulk replacement requires a separate proposal")
        if before.count(old) != 1:
            raise ReviewError(
                "edit_file.old_string must occur exactly once in the original"
            )
        proposed = before.replace(old, new, 1)
    else:
        raise ReviewError("Unsupported tool")
    if not proposed or "\x00" in proposed:
        raise ReviewError("Empty or NUL-containing file contents are not allowed")
    if len(proposed.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ReviewError("Proposed content exceeds size limit")
    if proposed == (before or ""):
        raise ReviewError("Proposal makes no change")
    return proposed


def is_explicit_end_append_request(request: str) -> bool:
    """Conservatively recognize a literal request to append at end-of-file.

    Generic additions and in-place edits are NOT automatically moved.
    A request with a negated append instruction is left for human review.
    """
    if not isinstance(request, str):
        return False
    if re.search(
        r"\b(?:do not|don't|never|without)\s+append\b", request, re.IGNORECASE
    ):
        return False
    return bool(
        re.search(r"\bappend\b", request, re.IGNORECASE)
        and re.search(r"\b(?:end|end-of-file|bottom)\b", request, re.IGNORECASE)
    )


def _is_single_line_end_append_request(request: str) -> bool:
    """Recognize literal requests to append one line, not general text blocks."""
    return bool(
        is_explicit_end_append_request(request)
        and re.search(
            r"\bappend\s+(?:exactly\s+)?(?:one|a|single)\s+(?:new\s+)?line\b",
            request,
            re.IGNORECASE,
        )
    )


def is_explicit_begin_prepend_request(request: str) -> bool:
    """Recognize a literal start-of-file instruction, never a generic 'add'."""
    if not isinstance(request, str):
        return False
    if re.search(
        r"\b(?:do not|don't|never|without)\s+prepend\b", request, re.IGNORECASE
    ):
        return False
    if re.search(r"\bprepend\b", request, re.IGNORECASE):
        return True
    return bool(
        re.search(
            r"\b(?:insert|add)\b[^.!?\n]{0,80}"
            r"\bat (?:the )?(?:beginning|start|top) of (?:the |this )?"
            r"(?:file|document|profile)\b",
            request,
            re.IGNORECASE,
        )
    )


def _single_insertion(before: str, after: str) -> tuple[int, str] | None:
    """Return (offset, added text) only if *all* old bytes remain in order.

    A single contiguous insertion is required; deleting, replacing, or moving
    existing text is not acceptable for a mechanical append normalization.
    """
    added_length = len(after) - len(before)
    if added_length <= 0:
        return None
    offset = 0
    while offset < len(before) and before[offset] == after[offset]:
        offset += 1
    if after[offset + added_length :] != before[offset:]:
        return None
    return offset, after[offset : offset + added_length]


def _append_result(before: str, added: str) -> str:
    """Append text without inventing an extra blank line.

    Existing file bytes remain unchanged. The added text gets a single
    line separator only when the original file lacks its final newline.
    """
    added = added.strip("\n")
    if not added or "\x00" in added:
        raise ReviewError("No valid text to append")
    separator = "" if not before or before.endswith("\n") else "\n"
    return before + separator + added + "\n"


def _tail_anchored_edit(before: str, after: str, candidate: dict[str, Any]) -> None:
    """Re-anchor an edit_file at a unique *final* suffix, not in the middle.

    The name/path are unchanged. A modified old_string must receive a HITL
    'edit' decision and be revalidated by the receipt registry.
    """
    if not after.startswith(before) or not after[len(before) :].strip():
        raise ReviewError("Target must append content without changing original text")
    if candidate["name"] == "write_file":
        candidate["args"]["content"] = after
        return
    lines = before.splitlines(keepends=True)
    if not lines:
        raise ReviewError("Cannot derive a safe terminal edit anchor")
    anchor = ""
    for line in reversed(lines):
        anchor = line + anchor
        if before.count(anchor) == 1:
            break
    else:
        raise ReviewError("Cannot find a unique end-of-file edit anchor")
    candidate["args"]["old_string"] = anchor
    candidate["args"]["new_string"] = anchor + after[len(before) :]
    if _project(candidate, before) != after:
        raise ReviewError("Cannot express appended text using an end-of-file edit")


def _set_revised_result(candidate: dict[str, Any], before: str, after: str) -> None:
    """Change the proposed text, keeping the tool and its current anchor."""
    args = candidate["args"]
    if candidate["name"] == "write_file":
        args["content"] = after
    else:
        old = args["old_string"]
        offset = before.index(old)
        prefix, suffix = before[:offset], before[offset + len(old) :]
        if not after.startswith(prefix) or (suffix and not after.endswith(suffix)):
            raise ReviewError("Revised insertion does not fit the original edit anchor")
        end = len(after) - len(suffix) if suffix else len(after)
        args["new_string"] = after[len(prefix) : end]
    if _project(candidate, before) != after:
        raise ReviewError("Revision cannot be represented by the authorized tool")


def _print_diff(before: str | None, after: str, path: Path, output=print) -> None:
    lines = unified_diff(
        (before or "").splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=str(path) + " (current)" if before is not None else "/dev/null",
        tofile=str(path) + " (proposed)",
    )
    output("\nProposed exact file change:\n")
    for line in lines:
        output(line.rstrip("\n"))
    output("\nReview all changes above. No file has been modified.")


def _edit_in_editor(text: str, *, input_fn=input, output=print) -> str:
    """Run user's editor without a shell; fallback to multiline paste."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        argv = shlex.split(editor)
        if not argv:
            raise ReviewError("Empty editor command")
        fd, filename = tempfile.mkstemp(suffix=".md", prefix="profile-review-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            result = subprocess.run([*argv, filename], check=False)
            if result.returncode != 0:
                raise ReviewError("Editor exited unsuccessfully; no revision accepted")
            with open(filename, "r", encoding="utf-8", newline="") as handle:
                return handle.read(MAX_CONTENT_BYTES + 1)
        finally:
            os.unlink(filename)
    output("No $VISUAL or $EDITOR configured. Paste the COMPLETE replacement text.")
    output("End with a line containing only .end (not included in content).")
    lines = []
    while True:
        line = input_fn("content> ")
        if line == ".end":
            break
        lines.append(line)
    return "\n".join(lines)


def _revise_only_inserted_text(
    candidate: dict[str, Any],
    before: str,
    *,
    append_at_end: bool,
    input_fn=input,
    output=print,
) -> None:
    """Edit ONLY new text. Existing profile bytes are never editor-owned."""
    existing_after = _project(candidate, before)
    insertion = _single_insertion(before, existing_after)
    if insertion is None:
        raise ReviewError(
            "This is not an insertion-only proposal; request a new proposal"
        )
    offset, inserted = insertion
    if append_at_end and offset != len(before):
        raise ReviewError("Requested append is not at the end of the file")
    # Keep the original line delimiters outside the editor. This matters for
    # both prepends ("new\n" + old first line) and middle inserts
    # ("new\n" + old following line). Without the trailing delimiter, the
    # revised text would silently merge into an existing profile line.
    leading = inserted[: len(inserted) - len(inserted.lstrip("\n"))]
    trailing = inserted[len(inserted.rstrip("\n")) :]
    previous = inserted[
        len(leading) : len(inserted) - len(trailing) if trailing else len(inserted)
    ]
    if not previous:
        raise ReviewError("Missing inserted content")

    output("Only the NEW text is editable; existing profile text is preserved.")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        revised = _edit_in_editor(
            previous + "\n", input_fn=input_fn, output=output
        ).strip("\n")
    elif "\n" not in previous:
        output("Current new line: " + previous)
        revised = input_fn("Replacement new line (blank cancels): ")
    else:
        output("Paste only the NEW content, not the existing profile.")
        revised = _edit_in_editor(
            previous + "\n", input_fn=input_fn, output=output
        ).strip("\n")
    if not revised or "\x00" in revised:
        raise ReviewError("Empty or invalid inserted content; no revision accepted")

    if append_at_end:
        # Preserve the original append's line separators exactly. In
        # particular, do NOT insert a new blank line during candidate review.
        target_after = before + leading + revised + trailing
    else:
        # Keep the original insertion position and surrounding bytes intact.
        target_after = before[:offset] + leading + revised + trailing + before[offset:]
    _set_revised_result(candidate, before, target_after)


def _revise_only_appended_text(
    candidate: dict[str, Any], before: str, *, input_fn=input, output=print
) -> None:
    """Compatibility entry point: edit only the appended block."""
    _revise_only_inserted_text(
        candidate,
        before,
        append_at_end=True,
        input_fn=input_fn,
        output=output,
    )


def reject(reason: str) -> dict[str, Any]:
    return {"type": "reject", "message": reason}


def review_profile_action(
    action: dict[str, Any],
    *,
    profile_prefix: str,
    allowed_decisions: list[str] | tuple[str, ...],
    original_request: str = "",
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    revise_fn: Callable[[str], str] | None = None,
    scope_check: Callable[[str | None, str, str], list[str]] | None = None,
) -> dict[str, Any]:
    """Review once-per-interrupt without modifying a file.

    `scope_check(before, proposed, original_request)` is an optional deterministic
    or externally reviewed original-intent check. No automatic LLM calls are made.
    The CLI does NOT claim to verify professional claims or evidence.
    """
    path = _checked_path(action, profile_prefix)
    baseline = _read_snapshot(path)
    before = None if baseline is None else _text(baseline)
    candidate = deepcopy(action)
    field = _candidate_field(candidate)
    initial_after = _project(candidate, before)  # validate initial proposal
    try:
        validate_context_profile_write(
            str(path), initial_after, candidate_prefix=profile_prefix
        )
    except ContextRecordError as exc:
        raise ReviewError("Invalid candidate-context proposal: " + str(exc)) from exc
    contradictions = newly_introduced_profile_conflicts(before, initial_after)
    if contradictions:
        raise ReviewError(" ".join(contradictions))
    explicit_end_append = is_explicit_end_append_request(original_request)
    explicit_begin_prepend = is_explicit_begin_prepend_request(original_request)
    if explicit_end_append and explicit_begin_prepend:
        raise ReviewError("Conflicting append and prepend placement instructions")
    if explicit_begin_prepend:
        if before is None:
            raise ReviewError("Cannot prepend to a missing profile file")
        initial_prepend = _single_insertion(before, initial_after)
        if initial_prepend is None or initial_prepend[0] != 0:
            raise ReviewError(
                "Prepend-only request must add text at the beginning, without changing existing text"
            )
    if explicit_end_append:
        if before is None:
            raise ReviewError("Cannot append to a missing profile file")
        initial_insertion = _single_insertion(before, initial_after)
        if initial_insertion is None:
            raise ReviewError(
                "Append-only request would delete, replace or reorder existing profile text"
            )
        if initial_insertion[0] != len(before):
            # A mechanical relocation is safe only for one pure insertion.
            # No candidate-authored content is changed; the eventual corrected
            # tool operation will receive separate content and HITL approval.
            after_at_end = _append_result(before, initial_insertion[1])
            _tail_anchored_edit(before, after_at_end, candidate)
            output(
                "Agent inserted content in the middle of the profile. "
                "Corrected to an end-of-file append before review; no write occurred."
            )
    current_after = _project(candidate, before)
    originally_append_only = (
        before is not None
        and current_after.startswith(before)
        and bool(current_after[len(before) :].strip())
    )
    # Even for non-append tasks, a pure insertion must not turn into an
    # accidental deletion/replacement when the candidate chooses Revise.
    original_insertion = (
        None if before is None else _single_insertion(before, current_after)
    )
    originally_insertion_only = original_insertion is not None
    original_insertion_offset = (
        None if original_insertion is None else original_insertion[0]
    )
    revisions = 0
    safe_candidate = deepcopy(candidate)

    while True:
        try:
            after = _project(candidate, before)
        except ReviewError as exc:
            output(f"Invalid proposed edit: {exc}")
            if revisions >= MAX_REVISIONS:
                return reject("Invalid proposal after maximum revisions")
            choice = (
                input_fn("[V]ise content again or [N]ew agent proposal? [v/n] ")
                .strip()
                .lower()
            )
            if choice != "v":
                return reject("Invalid profile operation; create a corrected proposal")
            try:
                if revise_fn is None and originally_insertion_only:
                    # The invalid revision must never become the new baseline.
                    output("Restoring the last safe insertion for revision.")
                    candidate = deepcopy(safe_candidate)
                    _revise_only_inserted_text(
                        candidate,
                        before,
                        append_at_end=originally_append_only,
                        input_fn=input_fn,
                        output=output,
                    )
                else:
                    candidate["args"][field] = (
                        revise_fn
                        or (
                            lambda s: _edit_in_editor(
                                s, input_fn=input_fn, output=output
                            )
                        )
                    )(candidate["args"][field])
            except ReviewError as exc:
                output(f"Revision cancelled: {exc}")
            revisions += 1
            continue
        issues = newly_introduced_profile_conflicts(before, after)
        try:
            validate_context_profile_write(
                str(path), after, candidate_prefix=profile_prefix
            )
        except ContextRecordError as exc:
            issues.append("Invalid candidate-context proposal: " + str(exc))
        if explicit_end_append or originally_append_only:
            issues.extend(append_only_scope(before, after, original_request))
        if originally_insertion_only:
            revised_insertion = _single_insertion(before, after)
            if revised_insertion is None:
                issues.append(
                    "An insertion-only proposal cannot delete or rewrite existing text"
                )
            elif revised_insertion[0] != original_insertion_offset:
                issues.append("Revised insertion moved from its original position")
        if explicit_begin_prepend and (
            _single_insertion(before, after) is None
            or _single_insertion(before, after)[0] != 0
        ):
            issues.append("Prepend-only change must remain at the start of the file")
        if scope_check is not None:
            issues.extend(scope_check(before, after, original_request))
        if issues:
            output("Scope validation failed; nothing will be written:")
            for issue in issues:
                output(" - " + issue)
            choice = (
                input_fn("[V]ise again or [N]ew agent proposal? [v/n] ").strip().lower()
            )
            if choice == "v" and revisions < MAX_REVISIONS:
                try:
                    if revise_fn is None and originally_insertion_only:
                        candidate = deepcopy(safe_candidate)
                        _revise_only_inserted_text(
                            candidate,
                            before,
                            append_at_end=originally_append_only,
                            input_fn=input_fn,
                            output=output,
                        )
                    else:
                        candidate["args"][field] = (
                            revise_fn
                            or (
                                lambda s: _edit_in_editor(
                                    s, input_fn=input_fn, output=output
                                )
                            )
                        )(candidate["args"][field])
                except ReviewError as exc:
                    output(f"Revision cancelled: {exc}")
                revisions += 1
                continue
            return reject(
                "Proposal failed profile-change scope validation; draft a new proposal"
            )

        safe_candidate = deepcopy(candidate)
        if _read_snapshot(path) != baseline:
            output("Profile changed while under review. Aborting for a new proposal.")
            return reject("Profile changed since review began; re-read and re-propose")
        _print_diff(before, after, path, output=output)
        choice = (
            input_fn(
                "Candidate content: [A]pprove / [V]ise / [R]eject / [N]ew proposal? "
            )
            .strip()
            .lower()
        )
        if choice in {"r", "reject"}:
            return reject(CANCEL_PROFILE_REQUEST_MESSAGE)
        if choice in {"n", "new"}:
            reason = input_fn(
                "What should the candidate-profile-agent correct? "
            ).strip()
            return reject("Candidate requests new proposal: " + reason[:1500])
        if choice in {"v", "revise"}:
            if revisions >= MAX_REVISIONS:
                return reject("Revision limit reached; start a new proposal")
            try:
                if revise_fn is None and originally_insertion_only:
                    _revise_only_inserted_text(
                        candidate,
                        before,
                        append_at_end=originally_append_only,
                        input_fn=input_fn,
                        output=output,
                    )
                else:
                    candidate["args"][field] = (
                        revise_fn
                        or (
                            lambda s: _edit_in_editor(
                                s, input_fn=input_fn, output=output
                            )
                        )
                    )(candidate["args"][field])
            except ReviewError as exc:
                output(f"Revision cancelled: {exc}")
                continue
            revisions += 1
            continue
        if choice not in {"a", "approve"}:
            output("Unknown choice; nothing approved.")
            continue

        if _read_snapshot(path) != baseline:
            return reject("Profile changed since review began; re-read and re-propose")
        if candidate["args"] != action["args"] and "edit" not in allowed_decisions:
            return reject(
                "Runtime does not allow edited tool calls; require a new proposal"
            )
        if "approve" not in allowed_decisions and candidate["args"] == action["args"]:
            return reject("Runtime does not allow approval for this action")
        output("Candidate content accepted. Tool approval still required.")
        tool = input_fn("Authorize this EXACT file operation? [y/N] ").strip().lower()
        if tool not in {"y", "yes"}:
            return reject(CANCEL_PROFILE_REQUEST_MESSAGE)
        if _read_snapshot(path) != baseline:
            return reject(
                "Profile changed before tool approval; re-read and re-propose"
            )
        if candidate["args"] == action["args"]:
            return {"type": "approve"}
        return {
            "type": "edit",
            "edited_action": {
                "name": action["name"],  # fixed name: cannot switch tools
                "args": candidate["args"],  # fixed path & original edit anchor
            },
        }


def append_only_scope(before: str | None, after: str, _request: str) -> list[str]:
    """Example constraint for an explicit append-only task (NOT a general validator)."""
    if before is None:
        return ["Append-only operation requires an existing file"]
    if not after.startswith(before):
        return ["Requested append to end, but earlier file content was changed"]
    if not after[len(before) :].strip():
        return ["No appended text was provided"]
    if _is_single_line_end_append_request(_request):
        suffix = after[len(before) :]
        # When the existing file has no terminal newline, the first newline
        # separates the original final line from the appended line.
        if before and not before.endswith("\n"):
            if not suffix.startswith("\n"):
                return ["One-line append must start on a new line"]
            suffix = suffix[1:]
        if re.fullmatch(r"[^\n]+\n", suffix) is None:
            return ["Requested exactly one appended line; no extra blank lines"]
    return []
