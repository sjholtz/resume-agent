"""Construct Deep Agents filesystem backends for one candidate session.

Virtual routes:
  /system/...    -> <app_root>/...       (agents/AGENTS.md, skills/)
  /workspace/... -> <workspace_root>/... (candidate data and applications)
  all other paths -> StateBackend (thread-scoped scratch)

Tool-level filesystem permissions and HITL approval must be configured
separately on the orchestrator and each subagent. Backend routing alone
does not make the system files read-only or prevent external custom tools
from accessing the host filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from config import AgentConfig

if TYPE_CHECKING:
    from deepagents.backends import CompositeBackend


SYSTEM_PREFIX = "/system/"
WORKSPACE_PREFIX = "/workspace/"
SHARED_MEMORY = f"{SYSTEM_PREFIX}agents/shared/AGENTS.md"


class BackendPaths:
    """Helper for constructing *virtual* agent paths."""

    system_prefix: str = SYSTEM_PREFIX
    workspace_prefix: str = WORKSPACE_PREFIX
    shared_memory: str = SHARED_MEMORY

    @staticmethod
    def agent_memory(name: str) -> str:
        if not name or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("Agent name must contain only letters, digits, '-' or '_'")
        return f"/system/agents/{name}/AGENTS.md"

    @staticmethod
    def system_prompt(name: str) -> str:
        if not name or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError(
                "Prompt name must contain only letters, digits, '-' or '_'"
            )
        return f"/system/prompts/{name}.md"


def _are_overlapping(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def build_backend(config: AgentConfig) -> CompositeBackend:
    """Build two virtual filesystem roots plus an ephemeral scratch \
    backend.

    The roots are explicitly disjoint, to prevent /workspace/... from being
    used as an alternative path to /system/... or vice versa.

    The session's graph/checkpointer remains responsible for StateBackend
    state persistence. This is a local CLI design, not an OS sandbox.
    """

    app_root = config.app_root.resolve(strict=True)
    workspace_root = config.workspace_root.resolve(strict=True)

    if not app_root.is_dir() or not workspace_root.is_dir():
        raise ValueError("Application root and workspace root must be directories")

    if _are_overlapping(app_root, workspace_root):
        raise ValueError(
            "Application and candidate-workspace roots must not overlap. "
            "Choose a candidate directory outside the application installation."
        )

    # Deferred import allows config.py to run without the Deep Agents package.
    from deepagents.backends import (
        CompositeBackend,
        FilesystemBackend,
        StateBackend,
    )

    return CompositeBackend(
        default=StateBackend(),
        routes={
            SYSTEM_PREFIX: FilesystemBackend(
                root_dir=app_root,
                virtual_mode=True,
            ),
            WORKSPACE_PREFIX: FilesystemBackend(
                root_dir=workspace_root,
                virtual_mode=True,
            ),
        },
    )
