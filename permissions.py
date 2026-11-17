"""Role-specific permissions for Deep Agents' built-in filesystem tools.

The candidate profile path is a *virtual* prefix, not a host path. The Guix
launcher owns physical workspace selection; this module never inspects or
changes the launcher or the environment's filesystem mounts.

FilesystemPermission only controls built-in filesystem tools. Do not expose
custom arbitrary file-write or shell/execute tools without equivalent policy.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Literal

Role = Literal[
    "orchestrator",
    "candidate-profile-agent",
    "application-agent",
    "recruiting-ats-review-agent",
    "technical-review-agent",
]

PROFILE_ENV = "RESUME_AGENT_PROFILE_PREFIX"
DEFAULT_PROFILE_PREFIX = "/workspace/candidate"

# FilesystemPermission uses wcmatch glob rules: * matches exactly one path
# component and ** matches subdirectories. Date/title shape is enforced at
# tool level by application_workspace.canonical_paths() too.
_DATE_COMPONENT = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*"
APPLICATION_MUTATION_PATHS = [
    "/workspace/applications/*/company_inf.md",
    "/workspace/applications/*/additional_inf.md",
    f"/workspace/applications/*/{_DATE_COMPONENT}/**",
]


def profile_prefix(value: str | None = None) -> str:
    """Resolve the authoritative profile's virtual path, fail closed.

    A single dedicated subtree must hold the authoritative candidate
    profile and FUP register. Set RESUME_AGENT_PROFILE_PREFIX in the
    *career agent's* environment if this is not
    /workspace/candidate. No Guix-specific variable or modification to
    the jail is needed.
    """
    raw = DEFAULT_PROFILE_PREFIX if value is None else value
    if not isinstance(raw, str) or not raw.startswith("/") or "\\" in raw:
        raise ValueError("Profile prefix must be an absolute virtual POSIX path")
    if "//" in raw or any(part in {".", ".."} for part in raw.split("/")):
        raise ValueError("Profile prefix cannot contain '.', '..', or empty components")
    normalized = str(PurePosixPath(raw))
    if normalized in {"/workspace", "/workspace/applications", "/workspace/research"}:
        raise ValueError("Profile prefix must be a dedicated subtree")
    if not normalized.startswith("/workspace/"):
        raise ValueError("Profile prefix must be under /workspace/")
    if normalized.startswith(("/workspace/applications/", "/workspace/research/")):
        raise ValueError("Profile prefix must not overlap applications or research")
    return normalized


def configured_profile_prefix() -> str:
    """Resolve configuration in the application process, not in Guix."""
    return profile_prefix(os.environ.get(PROFILE_ENV) or None)


def _subtree(prefix: str) -> list[str]:
    return [prefix, prefix.rstrip("/") + "/**"]


def make_permissions(role: Role, *, candidate_profile: str | None = None):
    """Return ordered, first-match-wins FilesystemPermission rules.

    Reads never interrupt. Persistent writes require an explicit
    approved tool call, and candidate-profile writes are restricted to
    the profile agent.  Writes to the default StateBackend scratch
    area remain available; they do not persist to the candidate
    workspace or application install.
    """
    from deepagents import FilesystemPermission

    profile = profile_prefix(candidate_profile)
    # Source documents may be copied by the candidate on the host, but
    # never altered by an agent. Precede the candidate owner's broader
    # /workspace/candidate/** interrupt rule (first match wins).
    deny_source_documents = FilesystemPermission(
        operations=["write"],
        paths=_subtree(profile + "/evidence/sources/documents"),
        mode="deny",
    )
    deny_system = FilesystemPermission(
        operations=[
            "write",
        ],
        paths=_subtree(
            "/system",
        ),
        mode="deny",
    )
    if role in ("orchestrator", "application-agent"):
        return [
            deny_system,
            deny_source_documents,
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    profile,
                ),
                mode="deny",
            ),
            # The authoritative templates are immutable even with approval.
            FilesystemPermission(
                operations=["write"],
                paths=_subtree("/workspace/templates"),
                mode="deny",
            ),
            # Only canonical company records or position-scoped artifacts may
            # be written, and every such write still interrupts for approval.
            FilesystemPermission(
                operations=["write"],
                paths=APPLICATION_MUTATION_PATHS,
                mode="interrupt",
            ),
            # No /workspace/resume.tex, /workspace/applications/resume.tex,
            # arbitrary workspace notes, or other persistent file mutations.
            FilesystemPermission(
                operations=["write"],
                paths=_subtree("/workspace"),
                mode="deny",
            ),
        ]
    if role == "candidate-profile-agent":
        return [
            deny_system,
            deny_source_documents,
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    "/workspace/applications",
                ),
                mode="deny",
            ),
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    profile,
                ),
                mode="interrupt",
            ),
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    "/workspace/research",
                ),
                mode="interrupt",
            ),
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    "/workspace",
                ),
                mode="deny",
            ),
        ]
    if role in ("technical-review-agent", "recruiting-ats-review-agent"):
        return [
            deny_system,
            deny_source_documents,
            FilesystemPermission(
                operations=[
                    "write",
                ],
                paths=_subtree(
                    "/workspace",
                ),
                mode="deny",
            ),
        ]
    raise ValueError(f"Unknown agent role: {role!r}")
