# bin/resume-agent/subagents.py
"""Declarative specialist subagents, each with independent write permissions.

All four subagents share the orchestrator's CompositeBackend and their own
role-specific memory, skills, prompt, and persistent write policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import MemoryMiddleware, SubAgent
from deepagents.backends.protocol import BackendProtocol

import utils
from models import model
from pdf_read_guard import PdfReadGuardMiddleware
from permissions import make_permissions
from tools.initialize_application import make_initialize_application_tool
from tools.read_web_page import read_web_page
from tools.read_web_pdf import read_web_pdf
from tools.search import web_search

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware, AgentState

# List of tools available to all of the subagents
RESEARCH_TOOLS = [
    web_search,
    read_web_page,
    read_web_pdf,
]


def _specialist(
    *,
    name: str,
    role: str,
    description: str,
    backend: BackendProtocol,
    candidate_profile: str,
    local_pdf_reader,
    initialize_application_tool=None,
) -> SubAgent:
    middleware: list[AgentMiddleware[AgentState[Any], None, Any]] = [
        PdfReadGuardMiddleware(),
        MemoryMiddleware(
            backend=backend,
            sources=utils.get_memory_sources(role),
        ),
    ]
    return SubAgent(
        name=name,
        description=description,
        system_prompt=utils.load_system_prompt(role),
        tools=[
            *RESEARCH_TOOLS,
            local_pdf_reader,
            *(
                [initialize_application_tool]
                if initialize_application_tool is not None
                else []
            ),
        ],
        interrupt_on=(
            {"initialize_application": {"allowed_decisions": ["approve", "reject"]}}
            if initialize_application_tool is not None
            else {}
        ),
        model=model,
        middleware=middleware,
        skills=utils.get_skill_sources(role),
        permissions=make_permissions(
            name,
            candidate_profile=candidate_profile,
        ),
    )
    # return {
    #     "name": name,
    #     "description": description,
    #     "system_prompt": utils.load_system_prompt(role),
    #     "tools": RESEARCH_TOOLS,
    #     "model": model,
    #     "middleware": [
    #         MemoryMiddleware(
    #             backend=backend,
    #             sources=utils.get_memory_sources(role),
    #         ),
    #     ],
    #     "skills": utils.get_skill_sources(role),
    #     "permissions": make_permissions(
    #         name,
    #         candidate_profile=candidate_profile,
    #     ),
    # }


def build_subagents(
    backend: BackendProtocol,
    *,
    candidate_profile: str,
    local_pdf_reader,
    workspace_root: Path,
) -> Sequence[SubAgent]:
    """Return the four role-specific subagents with no cross-role \
    write access."""

    return [
        _specialist(
            name="candidate-profile-agent",
            role="candidate",
            description=(
                "Interview candidates, manage FUPs and evidence, and "
                "propose profile updates. Commit only candidate-approved "
                "changes through write approval. Never modify application "
                "drafts."
            ),
            backend=backend,
            candidate_profile=candidate_profile,
            local_pdf_reader=local_pdf_reader,
        ),
        _specialist(
            name="application-agent",
            role="application",
            description=(
                "Develop evidence-supported, job-specific resumes and "
                "cover letters. Profile and FUP records are strictly "
                "read-only."
            ),
            backend=backend,
            candidate_profile=candidate_profile,
            local_pdf_reader=local_pdf_reader,
            initialize_application_tool=make_initialize_application_tool(
                workspace_root=workspace_root
            ),
        ),
        _specialist(
            name="recruiting-ats-review-agent",
            role="recruiting",
            description=(
                "Independently review applications for recruiting "
                "relevance, ATS considerations and clear professional "
                "positioning; do not modify persistent files."
            ),
            backend=backend,
            candidate_profile=candidate_profile,
            local_pdf_reader=local_pdf_reader,
        ),
        _specialist(
            name="technical-review-agent",
            role="technical",
            description=(
                "Independently audit technical accuracy, engineering "
                "depth and evidentiary support; do not modify persistent "
                "files."
            ),
            backend=backend,
            candidate_profile=candidate_profile,
            local_pdf_reader=local_pdf_reader,
        ),
    ]
