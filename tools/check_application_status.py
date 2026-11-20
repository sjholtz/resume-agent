"""Current application progress; read-only and never HITL interrupted."""

from __future__ import annotations

import json
from pathlib import Path
from application_state import application_status
from application_workspace import ApplicationWorkspaceError
from latex_workflow import LatexWorkflowError


def make_check_application_status_tool(*, workspace_root: Path, candidate_profile: str):
    from langchain_core.tools import tool

    @tool("check_application_status")
    def check_application_status_tool(company_name: str, position_title: str, application_date: str) -> str:
        """Read current application stages from actual source, LaTeX and audit files.

        Does not write or confer submission approval. 'complete' is a mechanical
        artifact milestone; separate reviewer and candidate consent are needed.
        """
        try:
            result = application_status(
                workspace_root, company_name=company_name, position_title=position_title,
                application_date=application_date, candidate_profile=candidate_profile,
            )
        except (ApplicationWorkspaceError, LatexWorkflowError, OSError, ValueError) as exc:
            result = {"status": "error", "error_type": type(exc).__name__,
                      "message": str(exc)[:240]}
        return json.dumps(result, sort_keys=True)

    return check_application_status_tool
