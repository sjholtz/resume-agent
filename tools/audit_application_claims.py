"""Separately HITL-approved persistent claim-audit tool."""

from __future__ import annotations

import json
from pathlib import Path
from application_workspace import ApplicationWorkspaceError
from claim_audit import audit_application_claims


def make_audit_application_claims_tool(*, workspace_root: Path, candidate_profile: str):
    from langchain_core.tools import tool

    @tool("audit_application_claims")
    def audit_application_claims_tool(
        company_name: str, position_title: str, application_date: str, claims_json: str,
    ) -> str:
        """HITL REQUIRED: Record claim provenance after BOTH PDFs validate.

        claims_json is JSON: {"claims": [{"id":"CLM-0001", "document":"resume",
        "quote":"exact substring in compiled PDF", "evidence":[{"path":
        "/workspace/candidate/profile.md", "quote":"exact supporting source text"}]}],
        "fups":[{"id":"FUP-001", "note":"Unconfirmed claim excluded from PDF"}]}.
        Inventory every material resume/letter fact, metric, skill, credential,
        accomplishment and scope assertion. Cite exact source snippets.
        Historical-only claims need candidate confirmation; uncovered numerical
        claims and unsupported assertions block. An excluded FUP does not block.
        Never call this tool before final LaTeX validation or bypass approval.
        After EVERY edit, revalidate LaTeX then re-audit claims. This is not
        semantic fact verification: both independent reviewers and candidate
        factual sign-off are still required.
        """
        try:
            return json.dumps(audit_application_claims(
                workspace_root, company_name=company_name,
                position_title=position_title, application_date=application_date,
                claims_json=claims_json, candidate_profile=candidate_profile,
            ), ensure_ascii=False, sort_keys=True)
        except (ApplicationWorkspaceError, OSError, UnicodeError) as exc:
            return json.dumps({"status": "error", "message": str(exc)[:1000]})

    return audit_application_claims_tool
