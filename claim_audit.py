"""Patch C: deterministic, approval-gated claim-to-source traceability.

This module does NOT determine whether candidate assertions are true. It proves
only that quoted text occurs in the compiled artifacts and specified candidate
records. Semantic review and candidate factual sign-off remain mandatory.

Persistent writes are permitted ONLY via an approval-interrupted tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from application_workspace import ApplicationWorkspaceError, _no_symlink_components
from latex_workflow import (
    MAX_PDF_BYTES, MAX_TEX_BYTES, MANIFEST_NAME, _application, _file, _root,
    _manifest, _sources_from_manifest, _preflight,
    _regular, _sha, _write_atomic,
)
from permissions import profile_prefix


class ClaimAuditError(ApplicationWorkspaceError):
    """Missing prerequisite, invalid provenance, or unsafe audit request."""


CLAIM_ID = re.compile(r"CLM-[0-9]{4,6}\Z")
FUP_ID = re.compile(r"FUP-[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
# Deliberately focused on *consequential* numerical assertions. Dates, phone
# numbers and street addresses are not classified as quantified achievements.
QUANTIFIED = re.compile(
    r"(?<!\w)(?:[$€£]\s*\d[\d,.]*|\d[\d,.]*\s*(?:%|percent(?:age)?s?\b|"
    r"[x×](?!\w)|(?:million|billion|thousand|users|customers|clients|students|"
    r"requests|transactions|hours|days|weeks|milliseconds|ms|seconds|servers|"
    r"deployments|systems|models|teams|projects|employees|dollars)\b))",
    re.IGNORECASE,
)
MAX_CLAIMS = 150
MAX_AUDIT_INPUT = 120_000
MAX_EVIDENCE_BYTES = 1_000_000
AUDIT_REPORT_NAME = "claim_audit.md"
AUDIT_STATE_NAME = "claim_audit.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain(value: str) -> str:
    """Match whitespace-wrapped PDF text without rewriting the factual words."""
    return " ".join(value.replace("\u00ad", "").split())


def _text_field(value: Any, field: str, *, limit: int = 2500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ClaimAuditError(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


def _load_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        obj = json.loads(_regular(path, limit=limit).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ClaimAuditError(f"Invalid JSON: {path.name}") from exc
    if not isinstance(obj, dict):
        raise ClaimAuditError(f"Expected JSON object in {path.name}")
    return obj


def _evidence_source(root: Path, virtual: str, *, candidate_profile: str) -> tuple[Path, str]:
    """Only the candidate's protected records or historical-document library.

    Job postings, company research, and arbitrary URLs NEVER establish a
    candidate accomplishment. Every intermediate path must be symlink-free.
    """
    value = _text_field(virtual, "evidence.path", limit=800)
    path = PurePosixPath(value)
    if str(path) != value or not path.is_absolute() or not path.is_relative_to("/workspace"):
        raise ClaimAuditError("Evidence paths must be normalized /workspace/... paths")
    if any(part in (".", "..") for part in value.split("/")):
        raise ClaimAuditError("Relative path components are not allowed in evidence")
    base = PurePosixPath("/workspace/candidate/base-documents")
    profile = PurePosixPath(candidate_profile)
    if path.is_relative_to(base):
        if path == base:
            raise ClaimAuditError("Evidence path must name a file")
        kind = "historical_document"
    elif path.is_relative_to(profile) and path != profile:
        # If profile is /workspace/candidate, the historical library is still
        # separately classified rather than promoted to profile evidence.
        kind = "candidate_record"
    else:
        raise ClaimAuditError("Evidence must be in the configured candidate profile or historical-document library")
    actual = root.joinpath(*path.parts[2:])
    try:
        _no_symlink_components(root, actual)
    except ApplicationWorkspaceError as exc:
        raise ClaimAuditError(str(exc)) from exc
    if actual.suffix.lower() not in {".md", ".txt", ".tex"}:
        raise ClaimAuditError("Only text-based candidate source files (.md, .txt, .tex) are accepted")
    return actual, kind


def _validated_pdfs(root: Path, paths, position: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Ensure PDF artifacts correspond to the *current* LaTeX and validation.

    Extract text from actual PDFs, not the potentially stale build/*.txt files.
    """
    # A passing validation must also have its original approved source manifest
    # and source templates intact, not merely matching draft/PDF hashes.
    manifest_path = _file(root, paths.position_directory + "/" + MANIFEST_NAME)
    manifest = _manifest(manifest_path)
    sources = _sources_from_manifest(root, manifest)
    _preflight(root, paths, position, manifest, sources)
    report_path = _file(root, paths.position_directory + "/build/validation.json")
    report = _load_json(report_path, 100_000)
    if report.get("status") != "ok" or report.get("position_directory") != paths.position_directory:
        raise ClaimAuditError("Both LaTeX documents must have a current successful validation")
    docs = report.get("documents")
    if not isinstance(docs, dict):
        raise ClaimAuditError("Missing compiled-document validation details")
    import pymupdf
    rendered: dict[str, str] = {}
    for kind in ("resume", "cover_letter"):
        entry = docs.get(kind)
        if not isinstance(entry, dict) or entry.get("ok") is not True:
            raise ClaimAuditError(f"{kind}: validated document is missing or failed")
        source = _file(root, getattr(paths, kind))
        raw_source = _regular(source, limit=MAX_TEX_BYTES)
        if entry.get("source_sha256") != _sha(raw_source):
            raise ClaimAuditError(f"{kind}: draft changed after LaTeX validation; recompile before audit")
        pdf_path = _file(root, paths.position_directory + f"/build/{kind}.pdf")
        raw_pdf = _regular(pdf_path, limit=MAX_PDF_BYTES)
        if entry.get("pdf_sha256") != _sha(raw_pdf):
            raise ClaimAuditError(f"{kind}: PDF changed after LaTeX validation")
        try:
            with pymupdf.open(stream=raw_pdf, filetype="pdf") as pdf:
                if pdf.page_count != entry.get("page_count"):
                    raise ClaimAuditError(f"{kind}: compiled PDF page count changed")
                rendered[kind] = _plain(" ".join(page.get_text("text", sort=True) for page in pdf))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ClaimAuditError(f"{kind}: could not extract validated PDF text") from exc
        if len(rendered[kind]) < 80:
            raise ClaimAuditError(f"{kind}: compiled document has insufficient text")
    return rendered, report


def _parse_request(claims_json: str) -> dict[str, Any]:
    if not isinstance(claims_json, str) or not 2 <= len(claims_json.encode("utf-8")) <= MAX_AUDIT_INPUT:
        raise ClaimAuditError("claims_json must be a JSON object no larger than 120 KB")
    try:
        req = json.loads(claims_json)
    except (ValueError, TypeError) as exc:
        raise ClaimAuditError("claims_json is not valid JSON") from exc
    if not isinstance(req, dict) or set(req) - {"claims", "fups"}:
        raise ClaimAuditError("Expected object with claims and optional fups only")
    claims = req.get("claims")
    if not isinstance(claims, list) or not 2 <= len(claims) <= MAX_CLAIMS:
        raise ClaimAuditError("Provide 2..150 substantive claims covering BOTH documents")
    fups = req.get("fups", [])
    if not isinstance(fups, list) or len(fups) > MAX_CLAIMS:
        raise ClaimAuditError("fups must be a list of no more than 150 excluded follow-ups")
    return {"claims": claims, "fups": fups}


def _scan_numbers(document: str, quotes: list[str]) -> list[str]:
    uncovered = []
    for match in QUANTIFIED.finditer(document):
        if not any(match.start() >= start and match.end() <= end for quote in quotes
                   for start in _positions(document, quote) for end in (start + len(quote),)):
            uncovered.append(match.group())
    return list(dict.fromkeys(uncovered))


def _positions(document: str, quote: str):
    start = 0
    while True:
        i = document.find(quote, start)
        if i < 0:
            return
        yield i
        start = i + 1


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Application Claim Audit", "",
        f"- Status: **{report['status']}**",
        f"- Audited at (UTC): {report['audited_at_utc']}",
        f"- Position directory: `{report['position_directory']}`", "",
        "This is **exact-match provenance checking**, not a proof that a person's claims are true.",
        "Both independent reviewers must check the completeness and semantic relevance of citations.",
        "The candidate must confirm any candidate-reported claims before submission.", "",
        "## Document claims", "",
    ]
    for c in report["claims"]:
        lines += [
            f"### {c['id']} — {c['document']} — {c['status']}", "",
            f"- PDF quote: {json.dumps(c['quote'], ensure_ascii=False)}",
        ]
        for s in c["evidence"]:
            lines += [f"- Evidence: `{s['path']}` ({s['source_type']}) — {json.dumps(s['quote'], ensure_ascii=False)}"]
        for issue in c["issues"]:
            lines += [f"- **Finding:** {issue}"]
        lines.append("")
    lines += ["## Quantitative-claim coverage", ""]
    for kind, value in report["uncovered_quantitative"].items():
        lines += [f"- {kind}: " + (", ".join(json.dumps(x) for x in value) if value else "No uncovered quantitative patterns detected")]
    lines += ["", "## Excluded follow-ups (not blocking)", ""]
    for item in report["fups"]:
        lines += [f"- `{item['id']}`: {item['note']}"]
    if not report["fups"]:
        lines += ["- None recorded"]
    lines += ["", "## Remaining quality gates", "",
              "- [ ] Technical reviewer checks every material statement and source context",
              "- [ ] Recruiting/ATS reviewer checks cross-document assertions and omitted claims",
              "- [ ] Candidate confirms candidate-reported wording and final facts",
              "- [ ] Re-run LaTeX validation and this audit after every substantive edit", ""]
    return "\n".join(lines)


def audit_application_claims(
    workspace_root: Path, *, company_name: str, position_title: str,
    application_date: str, claims_json: str, candidate_profile: str = "/workspace/candidate",
) -> dict[str, Any]:
    """Record a claim ledger with mandatory current-PDF and exact-source anchors.

    The agent proposes an inventory, so omitted *non-quantitative* claims are
    still a human-review risk. The tool never marks an application submission-ready.
    """
    root = _root(workspace_root)
    profile = profile_prefix(candidate_profile)
    paths, position = _application(root, company_name, position_title, application_date)
    rendered, latex_report = _validated_pdfs(root, paths, position)
    request = _parse_request(claims_json)
    done: set[str] = set()
    processed: list[dict[str, Any]] = []
    quoted: dict[str, list[str]] = {"resume": [], "cover_letter": []}
    for claim in request["claims"]:
        if not isinstance(claim, dict) or set(claim) != {"id", "document", "quote", "evidence"}:
            raise ClaimAuditError("Each claim needs exactly id, document, quote, evidence")
        identifier = _text_field(claim["id"], "claim.id", limit=16)
        if not CLAIM_ID.fullmatch(identifier) or identifier in done:
            raise ClaimAuditError("Claim IDs must be unique CLM-0001-style identifiers")
        done.add(identifier)
        kind = claim["document"]
        if kind not in rendered:
            raise ClaimAuditError(f"{identifier}: document must be resume or cover_letter")
        excerpt = _plain(_text_field(claim["quote"], "claim.quote"))
        if len(excerpt) < 12 or excerpt not in rendered[kind]:
            raise ClaimAuditError(f"{identifier}: exact claim quote not found in the current compiled {kind} PDF")
        quoted[kind].append(excerpt)
        evidence = claim["evidence"]
        if not isinstance(evidence, list) or len(evidence) > 6:
            raise ClaimAuditError(f"{identifier}: evidence must be a list of at most 6 sources")
        found = []
        for source in evidence:
            if not isinstance(source, dict) or set(source) != {"path", "quote"}:
                raise ClaimAuditError(f"{identifier}: evidence requires path and exact quote")
            actual, source_type = _evidence_source(root, source["path"], candidate_profile=profile)
            raw = _regular(actual, limit=MAX_EVIDENCE_BYTES)
            try:
                contents = raw.decode("utf-8")
            except UnicodeError as exc:
                raise ClaimAuditError("Evidence file must be UTF-8") from exc
            citation = _plain(_text_field(source["quote"], "evidence.quote", limit=1800))
            if len(citation) < 8 or citation not in _plain(contents):
                raise ClaimAuditError(f"{identifier}: quoted evidence was not found in {source['path']}")
            found.append({"path": source["path"], "quote": citation,
                          "source_type": source_type, "sha256": hashlib.sha256(raw).hexdigest()})
        quant = [x.group() for x in QUANTIFIED.finditer(excerpt)]
        record_support = [e for e in found if e["source_type"] == "candidate_record"]
        record_supports_metrics = any(all(q.casefold() in e["quote"].casefold() for q in quant)
                                      for e in record_support)
        issues = []
        if not found:
            status = "unsupported"
            issues.append("No cited candidate source; remove or obtain factual support before submission")
        elif record_support:
            status = "record_backed"
        else:
            status = "candidate_reported"
            issues.append("Historical documents are candidate-authored, not independently verified; candidate confirmation required")
        if quant and not record_supports_metrics:
            issues.append("Quantified result lacks matching numerical support in the candidate profile; remove it or verify and update profile through approved channels")
        processed.append({"id": identifier, "document": kind, "quote": excerpt,
                          "status": status, "evidence": found, "quantitative": quant, "issues": issues})
    if any(not quoted[kind] for kind in rendered):
        raise ClaimAuditError("At least one anchored substantive claim is required for EACH compiled document")
    uncovered = {k: _scan_numbers(text, quoted[k]) for k, text in rendered.items()}
    excluded = []
    seen_fups: set[str] = set()
    for item in request["fups"]:
        if not isinstance(item, dict) or set(item) != {"id", "note"}:
            raise ClaimAuditError("Each excluded FUP needs exactly id and note")
        ref = _text_field(item["id"], "fup.id", limit=70)
        if not FUP_ID.fullmatch(ref) or ref in seen_fups:
            raise ClaimAuditError("FUP IDs must be unique FUP- identifiers")
        seen_fups.add(ref)
        excluded.append({"id": ref, "note": _text_field(item["note"], "fup.note", limit=500)})
    blocked = any(c["status"] == "unsupported" or any("Quantified result" in x for x in c["issues"])
                  for c in processed) or any(uncovered.values())
    report = {
        "schema_version": 1,
        "status": "blocked" if blocked else "review_required",
        "audited_at_utc": _now(),
        "position_directory": paths.position_directory,
        "latex_report_sha256": _sha(json.dumps(latex_report, sort_keys=True).encode("utf-8")),
        "document_hashes": {k: {
            "source_sha256": latex_report["documents"][k]["source_sha256"],
            "pdf_sha256": latex_report["documents"][k]["pdf_sha256"],
        } for k in rendered},
        "claims": processed,
        "uncovered_quantitative": uncovered,
        "fups": excluded,
        "remaining_gates": ["technical_reviewer", "recruiting_ats_reviewer", "candidate_factual_signoff"],
        "limits": "Exact-match source traceability is not semantic fact verification; non-numerical omissions require independent reviewer detection.",
    }
    # Tool-owned files, always preflighted, never writable through edit_file.
    report_file = _file(root, paths.position_directory + "/" + AUDIT_REPORT_NAME)
    state_file = _file(root, paths.position_directory + "/build/" + AUDIT_STATE_NAME)
    if not state_file.parent.is_dir():
        raise ClaimAuditError("Validated build directory has disappeared")
    for dest in (report_file, state_file):
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise ClaimAuditError(f"Unsafe audit output destination: {dest}")
    # Invalidate stale state first; on partial write failure a previous success
    # may NOT survive as if it were a fresh, completed audit.
    state_file.unlink(missing_ok=True)
    _write_atomic(report_file, _markdown(report).encode("utf-8"))
    _write_atomic(state_file, (json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return report


def check_claim_audit(
    workspace_root: Path, *, company_name: str, position_title: str,
    application_date: str, candidate_profile: str = "/workspace/candidate",
) -> dict[str, Any]:
    """Read-only freshness gate for the orchestrator and both reviewers."""
    try:
        root = _root(workspace_root)
        paths, position = _application(root, company_name, position_title, application_date)
        state_file = _file(root, paths.position_directory + "/build/" + AUDIT_STATE_NAME)
        saved = _load_json(state_file, 2_000_000)
        rendered, latex_report = _validated_pdfs(root, paths, position)
        current = {k: {"source_sha256": latex_report["documents"][k]["source_sha256"],
                       "pdf_sha256": latex_report["documents"][k]["pdf_sha256"]}
                   for k in rendered}
        if (saved.get("schema_version") != 1 or saved.get("position_directory") != paths.position_directory
                or saved.get("document_hashes") != current
                or saved.get("latex_report_sha256") != _sha(json.dumps(latex_report, sort_keys=True).encode("utf-8"))):
            return {"status": "stale", "reason": "Draft, PDF or LaTeX validation changed; re-run claim audit"}
        for claim in saved.get("claims", []):
            if claim.get("quote") not in rendered.get(claim.get("document"), ""):
                return {"status": "stale", "reason": "Audited quote no longer occurs in current PDF"}
            for source in claim.get("evidence", []):
                actual, _ = _evidence_source(root, source["path"], candidate_profile=profile_prefix(candidate_profile))
                if _sha(_regular(actual, limit=MAX_EVIDENCE_BYTES)) != source["sha256"]:
                    return {"status": "stale", "reason": "Underlying candidate evidence changed; re-run audit"}
        return {"status": saved.get("status"), "fresh": True,
                "claims": len(saved.get("claims", [])),
                "remaining_gates": saved.get("remaining_gates", [])}
    except (ClaimAuditError, ApplicationWorkspaceError, OSError, KeyError, TypeError, ValueError) as exc:
        return {"status": "missing_or_invalid", "fresh": False, "reason": str(exc)[:300]}
