"""Explicitly approved LaTeX build/inspection, with no arbitrary shell tool."""

from __future__ import annotations

import json
from pathlib import Path

from application_workspace import ApplicationWorkspaceError
from latex_workflow import validate_latex_documents


def make_validate_latex_documents_tool(*, workspace_root: Path):
    from langchain_core.tools import tool

    @tool("validate_latex_documents")
    def validate_latex_documents_tool(company_name: str, position_title: str, application_date: str) -> str:
        """HITL REQUIRED: Compile both staged LaTeX files using bounded latexmk.

        Verifies recorded source hashes, preserved layout anchors and absence
        of unresolved placeholders; runs latexmk without shell escape
        inside temporary directories; inspects resulting PDFs using PyMuPDF.
        Persists validation.json, compiler logs and successful PDF/text/page-1
        previews only under the canonical application's build/ directory.
        Never modifies .tex drafts or candidate-profile files. A cover letter
        longer than ONE PAGE fails validation. Re-run after EVERY approved edit.
        Compilation does not check factual truth or visual layout; independent
        reviews and candidate verification remain mandatory.
        """
        try:
            return json.dumps(validate_latex_documents(
                workspace_root, company_name=company_name,
                position_title=position_title, application_date=application_date,
            ), sort_keys=True)
        except (ApplicationWorkspaceError, OSError, UnicodeError) as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    return validate_latex_documents_tool
