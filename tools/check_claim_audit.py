"""Read-only current-state claim audit freshness check, no HITL required."""

from __future__ import annotations

import json
from pathlib import Path
from claim_audit import check_claim_audit


def make_check_claim_audit_tool(*, workspace_root: Path, candidate_profile: str):
    from langchain_core.tools import tool

    @tool("check_claim_audit")
    def check_claim_audit_tool(company_name: str, position_title: str, application_date: str) -> str:
        """Check whether current PDFs and candidate evidence match saved audit.

        Read-only. Reports blocked/review_required/stale/missing_or_invalid.
        A fresh review_required audit still needs independent reviewers and
        candidate factual sign-off; it is never a submission approval.
        """
        return json.dumps(check_claim_audit(
            workspace_root, company_name=company_name,
            position_title=position_title, application_date=application_date, candidate_profile=candidate_profile,
        ), sort_keys=True)

    return check_claim_audit_tool
