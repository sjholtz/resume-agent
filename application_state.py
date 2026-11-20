"""Read-only application progress reconstructed from CURRENT on-disk artifacts.

No status is inferred from LLM messages, tool approvals, or file names alone.
The existing source records, source manifest, PDF validation and claim audit are
our durable evidence.  This module deliberately creates no files and cannot
certify independent human review or submission readiness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from application_workspace import canonical_paths, _physical, ApplicationWorkspaceError
from application_write_guard import JOB, SLUG
from latex_workflow import (
    MANIFEST_NAME, MAX_TEX_BYTES, LatexWorkflowError,
    _root, _file, _regular, _manifest, _sources_from_manifest, _preflight,
)
from claim_audit import ClaimAuditError, _validated_pdfs, check_claim_audit

# Stable keys consumed by local CLI and the read-only agent tool.
STAGE_NAMES = (
    "source_capture", "qualification_matrix", "documents_staged",
    "latex_validation", "claim_audit", "independent_reviews", "candidate_signoff",
)
MAX_APPLICATIONS = 200


def _stage(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def _valid_record(path: Path) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        return False
    text = path.read_text(encoding="utf-8")
    return (text.startswith("# Job Description\n") and
            "- Status: captured\n" in text.split("## Source content\n\n", 1)[0] and
            "## Source content\n\n" in text and
            bool(text.split("## Source content\n\n", 1)[1].strip()))


def application_status(
    workspace_root: Path, *, company_name: str, position_title: str,
    application_date: str, candidate_profile: str = "/workspace/candidate",
) -> dict[str, Any]:
    """Safely compute a per-role snapshot; never modify application artifacts.

    'complete' for a machine stage is distinct from human factual clearance.
    A missing review receipt is not a successful independent review.
    """
    paths = canonical_paths(company_name, position_title, application_date)
    root = _root(Path(workspace_root))
    position = _physical(root, paths.position_directory)
    stages = {key: _stage("missing", "Prerequisite not recorded") for key in STAGE_NAMES}
    stages["independent_reviews"] = _stage(
        "not_verified", "No machine-verifiable signed technical and recruiting reviews")
    stages["candidate_signoff"] = _stage(
        "not_verified", "No machine-verifiable candidate factual sign-off")
    result: dict[str, Any] = {
        "schema_version": 1,
        "position_directory": paths.position_directory,
        "stages": stages,
        "overall": "in_progress",
        "submission_ready": False,
    }
    if position.is_symlink() or not position.is_dir():
        stages["source_capture"] = _stage("missing", "Canonical application directory not initialized")
        result["overall"] = "not_initialized"
        return result

    record = _physical(root, paths.job_description)
    try:
        if not _valid_record(record):
            stages["source_capture"] = _stage("invalid", "Captured initializer job-description record is absent or invalid")
        else:
            stages["source_capture"] = _stage("complete", "Captured job description exists in canonical directory")
    except (OSError, UnicodeError, ValueError):
        stages["source_capture"] = _stage("invalid", "Captured job-description record is unreadable")

    matrix = _file(root, paths.position_directory + "/qualification_matrix.md")
    if matrix.exists() or matrix.is_symlink():
        try:
            _regular(matrix, limit=1_000_000).decode("utf-8")
            stages["qualification_matrix"] = _stage("complete", "Qualification matrix file exists (contents not independently reviewed)")
        except (LatexWorkflowError, UnicodeError, OSError):
            stages["qualification_matrix"] = _stage("invalid", "Qualification matrix is unreadable, empty or unsafe")

    manifest_path = _file(root, paths.position_directory + "/" + MANIFEST_NAME)
    drafts_present = any(
        _file(root, getattr(paths, kind)).exists() for kind in ("resume", "cover_letter")
    )
    if manifest_path.exists() or manifest_path.is_symlink():
        try:
            manifest = _manifest(manifest_path)
            sources = _sources_from_manifest(root, manifest)
            _preflight(root, paths, position, manifest, sources)
            for kind in ("resume", "cover_letter"):
                _regular(_file(root, getattr(paths, kind)), limit=MAX_TEX_BYTES).decode("utf-8")
            stages["documents_staged"] = _stage(
                "complete", "Both LaTeX drafts and source provenance verified (tailoring not established)")
        except (LatexWorkflowError, ValueError, KeyError, TypeError, UnicodeError, OSError) as exc:
            stages["documents_staged"] = _stage("invalid", "Document staging or source provenance invalid; inspect source manifest")
    elif drafts_present:
        stages["documents_staged"] = _stage("invalid", "LaTeX drafts exist without approved source manifest")

    report_path = _file(root, paths.position_directory + "/build/validation.json")
    if report_path.exists() or report_path.is_symlink():
        try:
            from claim_audit import _load_json
            report = _load_json(report_path, 100_000)
            if report.get("status") != "ok":
                stages["latex_validation"] = _stage("blocked", "Last compilation or PDF validation failed")
            else:
                _validated_pdfs(root, paths, position)
                stages["latex_validation"] = _stage("complete", "Both current PDFs match successful LaTeX validation")
        except (ClaimAuditError, LatexWorkflowError, OSError, ValueError, KeyError, TypeError):
            stages["latex_validation"] = _stage("stale", "LaTeX report, source hashes, or PDFs are missing or no longer current")

    audit_path = _file(root, paths.position_directory + "/build/claim_audit.json")
    if audit_path.exists() or audit_path.is_symlink():
        state = check_claim_audit(
            root, company_name=company_name, position_title=position_title,
            application_date=application_date, candidate_profile=candidate_profile,
        )
        audit_status = state.get("status")
        if audit_status == "review_required" and state.get("fresh") is True:
            stages["claim_audit"] = _stage("complete", "Current claim inventory exists; semantic and human review still required")
        elif audit_status == "blocked" and state.get("fresh") is True:
            stages["claim_audit"] = _stage("blocked", "Current claim audit has unsupported active claims")
        elif audit_status == "stale":
            stages["claim_audit"] = _stage("stale", "Claim audit does not match current PDF or candidate evidence")
        else:
            stages["claim_audit"] = _stage("invalid", "Claim audit missing, corrupt, or unverifiable")

    if any(stage["status"] in ("invalid", "blocked", "stale") for stage in stages.values()):
        result["overall"] = "needs_attention"
    elif (stages["latex_validation"]["status"] == "complete" and
          stages["claim_audit"]["status"] == "complete"):
        result["overall"] = "artifact_validated_review_pending"
    return result


def _selected_job(value: str) -> tuple[str, str, str]:
    """Accept only company-slug/YYYY-MM-DD-role-slug, not an arbitrary path."""
    if not isinstance(value, str) or "/" not in value or value.count("/") != 1:
        raise ApplicationWorkspaceError("Expected company_slug/YYYY-MM-DD-position_slug")
    company, job = value.split("/")
    match = JOB.fullmatch(job)
    if not SLUG.fullmatch(company) or not match:
        raise ApplicationWorkspaceError("Noncanonical application identifier")
    day, role = match.groups()
    paths = canonical_paths(company, role, day)
    if paths.position_directory != "/workspace/applications/" + value:
        raise ApplicationWorkspaceError("Noncanonical application identifier")
    return company, role, day


def application_status_for_path(
    root: Path, name: str, *, candidate_profile: str = "/workspace/candidate",
) -> dict[str, Any]:
    company, role, day = _selected_job(name)
    return application_status(root, company_name=company, position_title=role,
                              application_date=day, candidate_profile=candidate_profile)


def scan_application_workspace(
    workspace_root: Path, *, candidate_profile: str = "/workspace/candidate",
) -> dict[str, Any]:
    """Read-only listing with noncanonical directory warnings, no migrations."""
    root = _root(Path(workspace_root))
    base = root / "applications"
    if base.is_symlink():
        raise ApplicationWorkspaceError("applications/ is a symlink")
    if not base.exists():
        return {"applications": [], "anomalies": []}
    if not base.is_dir():
        raise ApplicationWorkspaceError("applications/ is not a directory")
    found: list[dict[str, Any]] = []
    anomalies: list[dict[str, str]] = []
    inspected = 0
    for company in sorted(base.iterdir()):
        inspected += 1
        if inspected > 1000:
            raise ApplicationWorkspaceError("Too many application directory entries to scan")
        if company.is_symlink() or not company.is_dir() or not SLUG.fullmatch(company.name):
            anomalies.append({"path": "applications/" + company.name, "issue": "Noncanonical or unsafe company entry"})
            continue
        for job in sorted(company.iterdir()):
            if job.name in {"company_inf.md", "additional_inf.md"} and job.is_file() and not job.is_symlink():
                continue
            inspected += 1
            if inspected > 1000:
                raise ApplicationWorkspaceError("Too many application directory entries to scan")
            if job.is_symlink() or not job.is_dir() or not JOB.fullmatch(job.name):
                anomalies.append({"path": "applications/" + company.name + "/" + job.name,
                                  "issue": "Noncanonical or unsafe position entry"})
                continue
            name = company.name + "/" + job.name
            try:
                found.append(application_status_for_path(
                    root, name, candidate_profile=candidate_profile))
            except (ApplicationWorkspaceError, LatexWorkflowError, OSError, ValueError):
                # An unsafe/unreadable job cannot count as a successful state.
                raise ApplicationWorkspaceError("Cannot safely inspect canonical application: " + name)
            if len(found) > MAX_APPLICATIONS:
                raise ApplicationWorkspaceError("Too many applications for a single status listing; select one")
    return {"applications": found, "anomalies": anomalies[:50],
            "additional_anomalies": max(0, len(anomalies) - 50)}


def list_application_status(
    workspace_root: Path, *, candidate_profile: str = "/workspace/candidate",
) -> list[dict[str, Any]]:
    """Compatibility helper returning only canonical application reports."""
    return scan_application_workspace(workspace_root, candidate_profile=candidate_profile)["applications"]


def format_application_status(report: dict[str, Any]) -> str:
    """Concise redaction-safe report; never include document or tool contents."""
    lines = ["Application: " + report["position_directory"],
             "Overall: " + report["overall"] + " (submission_ready=false)"]
    for name in STAGE_NAMES:
        stage = report["stages"][name]
        lines.append("  " + name + ": " + stage["status"] + " — " + stage["reason"])
    return "\n".join(lines)
