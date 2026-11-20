"""Human-approved tool for application directory creation and source capture."""

from __future__ import annotations

import json
from pathlib import Path

from application_workspace import ApplicationWorkspaceError, initialize_application


def make_initialize_application_tool(*, workspace_root: Path):
    """Bind the workspace physically; agent never chooses the host directory.

    The caller MUST configure an explicit human-in-the-loop interrupt for the
    returned tool name `initialize_application`. Do not use this tool without
    approval: its filesystem operations do not pass through built-in write_file.
    """
    from langchain_core.tools import tool

    @tool("initialize_application")
    def initialize_application_tool(
        company_name: str,
        position_title: str,
        application_date: str,
        job_description_text: str = "",
        job_description_source: str = "",
        job_description_url: str = "",
        company_information_text: str = "",
        company_information_source: str = "",
        company_information_url: str = "",
        company_additional_text: str = "",
        company_additional_source: str = "",
        position_additional_text: str = "",
        position_additional_source: str = "",
    ) -> str:
        """After explicit human approval, initialize job folder and source records.

        Accept either a job_description_url ALONE (fetch public HTML and save
        its complete extracted text) OR genuine job_description_text with a
        truthful source label. URL retrieval happens only after THIS tool's
        explicit human approval; its HTTPS/public-site checks are inherited
        from web_fetch. A failed fetch never creates files. No JavaScript,
        logins or PDFs. Company/extra texts are optional, and existing
        approved source snapshots are preserved rather than overwritten.
        """
        try:
            outcome = initialize_application(
                workspace_root,
                company_name=company_name,
                position_title=position_title,
                application_date=application_date,
                job_description_text=job_description_text,
                job_description_source=job_description_source,
                job_description_url=job_description_url,
                company_information_text=company_information_text,
                company_information_source=company_information_source,
                company_information_url=company_information_url,
                company_additional_text=company_additional_text,
                company_additional_source=company_additional_source,
                position_additional_text=position_additional_text,
                position_additional_source=position_additional_source,
            )
            return json.dumps({"status": "ok", **outcome}, sort_keys=True)
        except (ApplicationWorkspaceError, FileExistsError, OSError, UnicodeError) as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    return initialize_application_tool
