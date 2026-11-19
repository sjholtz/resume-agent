"""Approval-gated staging of user-selected historical LaTeX documents."""

from __future__ import annotations

import json
from pathlib import Path

from application_workspace import ApplicationWorkspaceError
from latex_workflow import prepare_latex_documents


def make_prepare_latex_documents_tool(*, workspace_root: Path,
                                      base_resume: str | None = None,
                                      base_cover: str | None = None):
    """Bind document selections into the tool, never trust model-supplied paths."""
    from langchain_core.tools import tool

    @tool("prepare_latex_documents")
    def prepare_latex_documents_tool(company_name: str, position_title: str,
                                     application_date: str) -> str:
        """HITL REQUIRED: Stage the CLI-selected resume and letter sources.

        The CLI chooses historical .tex files under candidate/base-documents
        using --base-resume and --base-cover, or uses the existing defaults
        under templates/ when flags are absent. This tool copies both sources
        into the already-initialized application directory, records immutable
        SHA256 provenance, and never overwrites an existing draft. The agent
        cannot change the selected source filenames through tool arguments.
        Reuse accurate source content, making only justified, approved edits.
        """
        try:
            return json.dumps(prepare_latex_documents(
                workspace_root, company_name=company_name,
                position_title=position_title, application_date=application_date,
                base_resume=base_resume, base_cover=base_cover,
            ), sort_keys=True)
        except (ApplicationWorkspaceError, OSError, UnicodeError) as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    return prepare_latex_documents_tool
