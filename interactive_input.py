"""Private, session-scoped terminal input helpers for the career-agent CLI.

Readline handles interactive line editing, but automatic history is explicitly
DISABLED: normal chat is added manually, while HITL answers are never added.
No history files are read or written by this module.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_CHAT_HISTORY = 100


def configure_readline() -> Any | None:
    """Enable interactive line editing and session-only chat history.

    Only run for a terminal session. In particular, never enable Readline's
    automatic history: that would capture answers to HITL approval prompts.
    """
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return None

    try:
        import readline
    except ImportError:
        # Plain input() still works on platforms without Python Readline.
        return None

    try:
        readline.set_auto_history(False)
        readline.clear_history()
    except (AttributeError, OSError) as exc:
        # Do not use a Readline configuration that could collect HITL inputs.
        print(f"Readline chat history unavailable: {exc}", file=sys.stderr)
        return None
    return readline


def remember_chat_message(line_editor: Any | None, message: str) -> None:
    """Store only a completed, ordinary one-line chat message in memory."""
    if line_editor is None or not message.strip():
        return
    line_editor.add_history(message)
    if line_editor.get_current_history_length() > MAX_CHAT_HISTORY:
        line_editor.remove_history_item(0)


def edit_chat_message() -> str | None:
    """Compose one multiline user message using $VISUAL, then $EDITOR.

    The temporary file is mode 0600 and always removed on return or error.
    This command never runs through a shell and cannot bypass HITL approval.
    Returns None on cancellation or error, leaving the chat loop intact.
    """
    specification = (
        os.environ.get("VISUAL", "").strip() or os.environ.get("EDITOR", "").strip()
    )
    if not specification:
        print(
            "No editor configured; set VISUAL or EDITOR (e.g. nano).", file=sys.stderr
        )
        return None
    try:
        command = shlex.split(specification)
    except ValueError as exc:
        print(f"Invalid VISUAL/EDITOR command: {exc}", file=sys.stderr)
        return None
    if not command:
        print("VISUAL/EDITOR did not specify a command.", file=sys.stderr)
        return None

    fd, filename = tempfile.mkstemp(prefix="career-agent-message-", suffix=".md")
    os.close(fd)
    path = Path(filename)
    try:
        completed = subprocess.run([*command, str(path)], check=False)
        if completed.returncode != 0:
            print("Editor exited without submitting a message.", file=sys.stderr)
            return None
        message = path.read_text(encoding="utf-8").rstrip("\n")
        return message if message.strip() else None
    except (OSError, UnicodeError) as exc:
        print(f"Editor could not provide a message: {exc}", file=sys.stderr)
        return None
    finally:
        path.unlink(missing_ok=True)
