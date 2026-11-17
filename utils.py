"""Filesystem-safe, central references to system prompts and AGENTS.md \
memory."""

from __future__ import annotations

from pathlib import Path

from backend import BackendPaths

AGENT_HOME = Path(__file__).resolve().parent

# The keys are the roles used internally; values match the *existing* archive.
AGENT_DIRECTORY: dict[str, str] = {
    "shared": "shared",
    "orchestrator": "orchestrator",
    "candidate": "candidate-profile-agent",
    "application": "application-agent",
    "recruiting": "recruiting-ats-review-agent",
    "technical": "technical-review-agent",
}

PROMPT_FILE: dict[str, str] = {
    "orchestrator": "orchestrator_system_prompt.md",
    "candidate": "candidate_system_prompt.md",
    "application": "application_system_prompt.md",
    "recruiting": "recruiting_system_prompt.md",
    "technical": "technical_system_prompt.md",
}


def load_system_prompt(role: str) -> str:
    """Read the actual prompt on disk; never return missing/empty prompts."""
    if role not in PROMPT_FILE:
        raise ValueError(f"Unknown prompt role: {role!r}")
    prompt_path = AGENT_HOME / "prompts" / PROMPT_FILE[role]
    content = prompt_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Empty system prompt: {prompt_path}")
    return content


def get_memory_sources(role: str) -> list[str]:
    """Get shared memory followed by the agent-specific virtual memory \
    file."""
    if role not in AGENT_DIRECTORY:
        raise ValueError(f"Unknown agent role: {role!r}")
    paths = [BackendPaths.shared_memory]
    if role != "shared":
        paths.append(BackendPaths.agent_memory(AGENT_DIRECTORY[role]))
    return paths


def get_skill_sources(role: str) -> list[str]:
    """Each role sees its own skills directory through /system/."""
    if role not in AGENT_DIRECTORY or role == "shared":
        raise ValueError(f"Unknown skill role: {role!r}")
    return [f"/system/skills/{AGENT_DIRECTORY[role]}/"]
