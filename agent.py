"""Career-agent graph factory.

Importing this module never starts an agent, resolves a workspace or prompts.
The Guix launcher chooses a workspace and runs the process from /workspace.
The factory explicitly selects that current working directory, without any
Guix-specific environment variable or dependency.

Direct CLI usage:
    uv run python agent.py [--trace]
    uv run python main.py [--trace]
For a LangGraph server, import agent:make_graph as a factory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import utils
from backend import build_backend
from config import load_config
from permissions import configured_profile_prefix, make_permissions
from subagents import build_subagents

if TYPE_CHECKING:
    from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig

# Explicit approvals for *external* tools. File writes are handled by the
# path-specific permissions on each agent, and file reads require no
# approval.
RESEARCH_INTERRUPT_ON: dict[str, bool | InterruptOnConfig] = {
    "web_search": {
        "allowed_decisions": [
            "approve",
            "reject",
        ],
    },
    "read_web_page": {
        "allowed_decisions": [
            "approve",
            "reject",
        ],
    },
    "read_web_pdf": {
        "allowed_decisions": [
            "approve",
            "reject",
        ],
    },
}


def make_graph(*, checkpointer=None, base_resume: str | None = None, base_cover: str | None = None):
    """Build a new graph for the current workspace (default: /workspace in \
    jail).

    Supply a durable checkpointer for server/persistent use. InMemorySaver
    is appropriate only for a prototype CLI that stays alive during
    approvals.
    """
    import warnings

    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from langchain_core._api.beta_decorator import LangChainBetaWarning

    # from langchain_quickjs import CodeInterpreterMiddleware
    from langgraph.checkpoint.memory import InMemorySaver

    from models import strong_model
    from pdf_read_guard import PdfReadGuardMiddleware
    from profile_receipt_middleware import profile_task_receipt_middleware
    from tools.read_local_pdf import make_read_local_pdf_tool
    from tools.read_web_page import read_web_page
    from tools.read_web_pdf import read_web_pdf
    from tools.search import web_search

    warnings.filterwarnings("ignore", category=LangChainBetaWarning)

    # Deep Agents otherwise inserts an unwanted general-purpose subagent.
    # Register the policy for the provider used in models.py. If the model
    # provider changes, update this key as well.
    register_harness_profile(
        "ollama",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )

    config = load_config(
        workspace=Path.cwd(),
        interactive=False,
    )
    backend = build_backend(config)
    profile = configured_profile_prefix()
    local_pdf_reader = make_read_local_pdf_tool(
        workspace_root=config.workspace_root,
        candidate_profile=profile,
    )

    # The included tools are the only *custom* tools. Deep Agents provides
    # its built-in filesystem tools; the role-specific permissions govern
    # writes.
    graph = create_deep_agent(
        model=strong_model,
        tools=[
            web_search,
            read_web_page,
            read_web_pdf,
            local_pdf_reader,
        ],
        interrupt_on=RESEARCH_INTERRUPT_ON,
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        system_prompt=utils.load_system_prompt("orchestrator"),
        subagents=build_subagents(
            backend,
            candidate_profile=profile,
            local_pdf_reader=local_pdf_reader,
            workspace_root=config.workspace_root,
            base_resume=base_resume,
            base_cover=base_cover,
        ),
        skills=utils.get_skill_sources("orchestrator"),
        memory=utils.get_memory_sources("orchestrator"),
        permissions=make_permissions(
            "orchestrator",
            candidate_profile=profile,
        ),
        backend=backend,
        middleware=[
            #     CodeInterpreterMiddleware(),
            profile_task_receipt_middleware,
            PdfReadGuardMiddleware(),
        ],
        name="orchestrator",
    )

    return graph.with_config(
        {
            "recursion_limit": 50,
        },
    )


if __name__ == "__main__":
    from cli import main

    raise SystemExit(main())
