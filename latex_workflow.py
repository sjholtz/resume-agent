"""Approved LaTeX historical-source staging and deterministic, bounded build verification.

Only call the mutating entrypoints through *separately HITL-interrupted* tools.
No filesystem tool permission is implicitly granted by this module. TeX is an
executable document format: use the existing Guix jail for untrusted inputs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any

from application_workspace import ApplicationWorkspaceError, _physical, canonical_paths
from base_documents import validate_filename, BaseDocumentSelectionError


class LatexWorkflowError(ApplicationWorkspaceError):
    """Missing template, unsafe path, invalid draft, or failed precondition."""


DEFAULT_TEMPLATES = {
    "resume": "resume_template.tex",
    "cover_letter": "cover_letter_template.tex",
}
MANIFEST_NAME = ".latex_source_manifest.json"
MAX_TEX_BYTES = 1024 * 1024
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_PAGES = 15
COMPILE_TIMEOUT_SECONDS = 90
UNRESOLVED_MARKERS = re.compile(
    r"\{\{\s*[a-zA-Z_][^{}\n]{0,100}\s*\}\}|"
    r"<<\s*[a-zA-Z_][^<>\n]{0,100}\s*>>|"
    r"\b(?:TODO|TBD|REPLACE_ME|PLACEHOLDER|YOUR_NAME|INSERT_HERE)\b",
    re.IGNORECASE,
)
DANGEROUS_TEX = re.compile(
    r"\\(?:write18|openout|readline|directlua|immediate\s*\\write18|ShellEscape)\b|"
    r"\\(?:input|include)\s*(?:\{\s*\||\s*\|)",
    re.IGNORECASE,
)
ANCHOR_PREFIXES = (
    r"\documentclass", r"\usepackage", r"\RequirePackage",
    r"\geometry", r"\pagestyle", r"\titleformat", r"\setlength",
    r"\definecolor", r"\renewenvironment", r"\newenvironment",
)
MACRO_DEFINITION = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\s*\{?\s*(\\[A-Za-z@]+)")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(workspace_root: Path) -> Path:
    root = Path(workspace_root)
    if root.is_symlink() or not root.is_dir():
        raise LatexWorkflowError("workspace_root must be an existing non-symlink directory")
    return root.resolve(strict=True)


def _file(root: Path, virtual: str) -> Path:
    try:
        return _physical(root, virtual)
    except ApplicationWorkspaceError as exc:
        raise LatexWorkflowError(str(exc)) from exc


def _application(root: Path, company_name: str, position_title: str, application_date: str):
    paths = canonical_paths(company_name, position_title, application_date)
    directory = _file(root, paths.position_directory)
    if not directory.is_dir():
        raise LatexWorkflowError("Application directory missing: run initialize_application first")
    job_description = _file(root, paths.job_description)
    if not job_description.is_file():
        raise LatexWorkflowError("job_description.md missing: initialize application first")
    captured = _read_text(job_description, 1_000_000)
    if "- Status: captured\n" not in captured or "## Source content\n\n" not in captured:
        raise LatexWorkflowError("job_description.md is not a captured source record")
    return paths, directory


def _regular(path: Path, *, limit: int) -> bytes:
    if path.is_symlink():
        raise LatexWorkflowError(f"Refusing symlink: {path}")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise LatexWorkflowError(f"Missing or unreadable file: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise LatexWorkflowError(f"Expected regular file: {path}")
    if metadata.st_size == 0 or metadata.st_size > limit:
        raise LatexWorkflowError(f"File must be 1..{limit} bytes: {path}")
    contents = path.read_bytes()
    if len(contents) > limit or len(contents) == 0:
        raise LatexWorkflowError(f"File size changed while reading: {path}")
    return contents


def _read_text(path: Path, size: int) -> str:
    try:
        return _regular(path, limit=size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LatexWorkflowError(f"File must be UTF-8: {path}") from exc


def _sources(root: Path, *, base_resume: str | None = None,
             base_cover: str | None = None) -> dict[str, dict[str, Any]]:
    """Pick the two immutable sources; historical choices are single filenames.

    Never silently fall back when an explicit historical document is missing.
    Directories and files cannot be symlinks; no arbitrary paths are allowed.
    """
    result: dict[str, dict[str, Any]] = {}
    selections = {"resume": base_resume, "cover_letter": base_cover}
    for kind, requested in selections.items():
        if requested is None:
            name = DEFAULT_TEMPLATES[kind]
            parent = root / "templates"
            virtual_path = "/workspace/templates/" + name
            source_type = "default_template"
        else:
            try:
                name = validate_filename(requested)
            except BaseDocumentSelectionError as exc:
                raise LatexWorkflowError(str(exc)) from exc
            parent = root / "candidate" / "base-documents"
            if (root / "candidate").is_symlink():
                raise LatexWorkflowError("Candidate source directory is a symlink")
            virtual_path = "/workspace/candidate/base-documents/" + name
            source_type = "historical_base"
        if parent.is_symlink() or not parent.is_dir():
            raise LatexWorkflowError(f"Source directory missing or symlink: {parent}")
        path = parent / name
        raw = _regular(path, limit=MAX_TEX_BYTES)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LatexWorkflowError(f"Source must be UTF-8: {virtual_path}") from exc
        _document_structure(text, name)
        result[kind] = {
            "path": path, "name": name, "raw": raw, "sha256": _sha(raw),
            "virtual_path": virtual_path, "source_type": source_type,
        }
    return result


def _sources_from_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reopen the exact sources recorded at approval time, not CLI defaults."""
    recorded = manifest["sources"]
    names: dict[str, str | None] = {}
    for kind in DEFAULT_TEMPLATES:
        entry = recorded.get(kind)
        if not isinstance(entry, dict):
            raise LatexWorkflowError(f"Missing source provenance for {kind}")
        if entry.get("source_type") == "historical_base":
            names[kind] = entry.get("filename")
            if not isinstance(names[kind], str):
                raise LatexWorkflowError(f"Invalid historical source name for {kind}")
        elif entry.get("source_type") == "default_template":
            names[kind] = None
        else:
            raise LatexWorkflowError(f"Unrecognized source type for {kind}")
    return _sources(root, base_resume=names["resume"], base_cover=names["cover_letter"])

def _document_structure(text: str, name: str) -> None:
    begins = list(re.finditer(r"\\begin\s*\{document\}", text))
    ends = list(re.finditer(r"\\end\s*\{document\}", text))
    if len(begins) != 1 or len(ends) != 1:
        raise LatexWorkflowError(f"{name}: expected exactly one document begin/end")
    if not re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{[^}]+\}", text):
        raise LatexWorkflowError(f"{name}: missing LaTeX documentclass")
    if begins[0].start() >= ends[0].start():
        raise LatexWorkflowError(f"{name}: document end precedes beginning")


def _scaffold(template: str, draft: str, name: str) -> None:
    """Require class and layout macros from the original template in the draft.

    Only checks structural anchors, not arbitrary prose or template-specific
    design choices. The human must still visually check the rendered pages.
    """
    _document_structure(draft, name)
    before_t = template[:re.search(r"\\begin\s*\{document\}", template).start()]
    before_d = draft[:re.search(r"\\begin\s*\{document\}", draft).start()]
    for line in before_t.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIXES) and stripped not in before_d:
            raise LatexWorkflowError(f"{name}: template layout/preamble changed: {stripped[:150]}")
    for macro in set(MACRO_DEFINITION.findall(before_t)):
        if macro not in MACRO_DEFINITION.findall(before_d):
            raise LatexWorkflowError(f"{name}: template macro definition removed: {macro}")
    if DANGEROUS_TEX.search(draft):
        raise LatexWorkflowError(f"{name}: dangerous TeX file/shell primitives are not allowed")
    marker = UNRESOLVED_MARKERS.search(draft)
    if marker:
        raise LatexWorkflowError(f"{name}: unresolved template placeholder: {marker.group()[:100]}")


def _manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(path, 40_000))
    except json.JSONDecodeError as exc:
        raise LatexWorkflowError("Source manifest is corrupt") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise LatexWorkflowError("Source manifest has unsupported schema")
    if not isinstance(data.get("sources"), dict):
        raise LatexWorkflowError("Source manifest missing provenance")
    return data


def _preflight(root: Path, paths, directory: Path, manifest: dict[str, Any],
               sources: dict[str, dict[str, Any]]) -> None:
    if manifest.get("position_directory") != paths.position_directory:
        raise LatexWorkflowError("Source manifest belongs to a different application")
    for kind, source in sources.items():
        saved = manifest["sources"].get(kind)
        if (not isinstance(saved, dict)
                or saved.get("filename") != source["name"]
                or saved.get("virtual_path") != source["virtual_path"]
                or saved.get("source_type") != source["source_type"]
                or saved.get("sha256") != source["sha256"]):
            raise LatexWorkflowError(
                f"{kind}: selected historical source/template does not match approved staging; "
                "do not overwrite existing drafts"
            )
        _file(root, getattr(paths, kind))


def prepare_latex_documents(
    workspace_root: Path, *, company_name: str, position_title: str,
    application_date: str, base_resume: str | None = None,
    base_cover: str | None = None,
) -> dict[str, Any]:
    """Stage exact historical documents or default templates without overwrites.

    Writes files persistently; expose ONLY through separately approved tool.
    """
    root = _root(workspace_root)
    paths, directory = _application(root, company_name, position_title, application_date)
    sources = _sources(root, base_resume=base_resume, base_cover=base_cover)
    manifest_path = _file(root, paths.position_directory + "/" + MANIFEST_NAME)
    drafts = {kind: _file(root, getattr(paths, kind)) for kind in DEFAULT_TEMPLATES}
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = _manifest(manifest_path)
        _preflight(root, paths, directory, manifest, sources)
        for path in drafts.values():
            _regular(path, limit=MAX_TEX_BYTES)
        return {
            "status": "ok", "action": "preserved", "position_directory": paths.position_directory,
            "drafts": {k: getattr(paths, k) for k in DEFAULT_TEMPLATES},
            "manifest": paths.position_directory + "/" + MANIFEST_NAME,
            "sources": manifest["sources"],
        }
    if any(p.exists() or p.is_symlink() for p in drafts.values()):
        raise LatexWorkflowError("Existing .tex draft without a manifest: manually reconcile it first; never overwrite")
    data = {
        "schema_version": 2,
        "staged_at_utc": _now(),
        "position_directory": paths.position_directory,
        "sources": {
            k: {
                "filename": v["name"], "virtual_path": v["virtual_path"],
                "source_type": v["source_type"], "sha256": v["sha256"],
            } for k, v in sources.items()
        },
    }
    written: list[Path] = []
    try:
        for kind, path in drafts.items():
            with path.open("xb") as handle:
                written.append(path)
                handle.write(sources[kind]["raw"])
        with manifest_path.open("x", encoding="utf-8") as handle:
            written.append(manifest_path)
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        for path in reversed(written):
            path.unlink(missing_ok=True)
        raise
    return {
        "status": "ok", "action": "created", "position_directory": paths.position_directory,
        "drafts": {k: getattr(paths, k) for k in DEFAULT_TEMPLATES},
        "manifest": paths.position_directory + "/" + MANIFEST_NAME,
        "sources": data["sources"],
        "note": "Preserve sound original wording; unchanged resumes are allowed. Source files are read-only.",
    }

def _read_tail(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        return b""
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - max_bytes), os.SEEK_SET)
        return source.read(max_bytes)


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace only fixed known build filenames; reject symlink destinations."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LatexWorkflowError(f"Unsafe existing build destination: {path}")
    fd, temp = tempfile.mkstemp(prefix=".latex-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _compile_one(kind: str, text: str, source_dirs: list[Path], application_dir: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Compile from a disposable directory, never run with cwd=/workspace."""
    report: dict[str, Any] = {"ok": False, "source_sha256": _sha(text.encode("utf-8")), "errors": [], "warnings": []}
    artifacts: dict[str, bytes] = {}
    latexmk = shutil.which("latexmk")
    if not latexmk:
        report["errors"].append("latexmk executable missing; install latexmk and a pdfLaTeX engine")
        return report, artifacts
    with tempfile.TemporaryDirectory(prefix="resume-agent-latex-") as workdir:
        work = Path(workdir)
        (work / f"{kind}.tex").write_text(text, encoding="utf-8")
        # TeX may read local style/asset files through TEXINPUTS, while its
        # temporary build directory and restricted openout prevent ordinary
        # output writes to the candidate's workspace.
        env = os.environ.copy()
        env.update({
            "HOME": str(work), "TEXMFOUTPUT": str(work),
            "TEXINPUTS": ":".join(str(folder) + "//" for folder in [*source_dirs, application_dir]) + ":",
            "openout_any": "p", "openin_any": "p", "shell_escape": "f",
        })
        command = [latexmk, "-norc", "-pdf", "-no-shell-escape", "-interaction=nonstopmode",
                   "-halt-on-error", "-file-line-error", "-synctex=0", f"{kind}.tex"]
        transcript_path = work / "latexmk-output.log"
        try:
            with transcript_path.open("wb") as transcript_file:
                run = subprocess.run(
                    command, cwd=work, env=env, stdout=transcript_file,
                    stderr=subprocess.STDOUT, timeout=COMPILE_TIMEOUT_SECONDS,
                    check=False, shell=False,
                )
            report["latexmk_exit_code"] = run.returncode
            artifacts[f"{kind}.build.log"] = _read_tail(transcript_path, max_bytes=MAX_LOG_BYTES)
            if run.returncode != 0:
                report["errors"].append("latexmk failed; inspect build/" + kind + ".log and build/" + kind + ".build.log")
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["errors"].append("latexmk execution failed or timed out: " + str(exc)[:600])
            artifacts[f"{kind}.build.log"] = _read_tail(transcript_path, max_bytes=MAX_LOG_BYTES) or str(exc).encode("utf-8")
        log_path = work / f"{kind}.log"
        if log_path.is_file() and not log_path.is_symlink():
            artifacts[f"{kind}.log"] = _read_tail(log_path, max_bytes=MAX_LOG_BYTES)
        pdf_path = work / f"{kind}.pdf"
        if report["errors"]:
            return report, artifacts
        if not pdf_path.is_file() or pdf_path.is_symlink():
            report["errors"].append("latexmk reported success but did not generate a regular PDF")
            return report, artifacts
        try:
            raw_pdf = _regular(pdf_path, limit=MAX_PDF_BYTES)
        except LatexWorkflowError as exc:
            report["errors"].append("Invalid generated PDF: " + str(exc)[:400])
            return report, artifacts
        if not raw_pdf.startswith(b"%PDF-"):
            report["errors"].append("Generated output is not a PDF")
            return report, artifacts
        try:
            import pymupdf
            with pymupdf.open(stream=raw_pdf, filetype="pdf") as pdf:
                pages = pdf.page_count
                report["page_count"] = pages
                if not 1 <= pages <= MAX_PAGES:
                    report["errors"].append(f"PDF has invalid/unreasonable page count: {pages}")
                extracted = "\n\n".join(page.get_text("text", sort=True) for page in pdf)
                report["extracted_chars"] = len(extracted.strip())
                if len(extracted.strip()) < 80:
                    report["errors"].append("PDF has insufficient selectable text (<80 characters)")
                if kind == "cover_letter" and pages > 1:
                    report["errors"].append(f"Cover letter must fit one page (found {pages})")
                if kind == "resume" and pages > 2:
                    report["warnings"].append(f"Resume spans {pages} pages; consider reducing to two")
                if not report["errors"]:
                    artifacts[f"{kind}.pdf"] = raw_pdf
                    artifacts[f"{kind}.txt"] = extracted.encode("utf-8")
                    artifacts[f"{kind}_page1.png"] = pdf[0].get_pixmap(matrix=pymupdf.Matrix(1.0, 1.0), alpha=False).tobytes("png")
                    report["pdf_sha256"] = _sha(raw_pdf)
        except (RuntimeError, ValueError, OSError) as exc:
            report["errors"].append("Failed to inspect generated PDF: " + str(exc)[:400])
    report["ok"] = not report["errors"]
    return report, artifacts


def validate_latex_documents(
    workspace_root: Path, *, company_name: str, position_title: str, application_date: str,
) -> dict[str, Any]:
    """Preflight, compile, inspect PDF and persist build diagnostics.

    This performs persistent writes under build/ and MUST be HITL interrupted.
    It never rewrites resume.tex or cover_letter.tex.
    """
    root = _root(workspace_root)
    paths, directory = _application(root, company_name, position_title, application_date)
    manifest_path = _file(root, paths.position_directory + "/" + MANIFEST_NAME)
    manifest = _manifest(manifest_path)
    sources = _sources_from_manifest(root, manifest)
    _preflight(root, paths, directory, manifest, sources)
    drafts: dict[str, str] = {}
    for kind, source in sources.items():
        source_path = _file(root, getattr(paths, kind))
        text = _read_text(source_path, MAX_TEX_BYTES)
        _scaffold(source["raw"].decode("utf-8"), text, f"{kind}.tex")
        drafts[kind] = text
    build_dir = _file(root, paths.position_directory + "/build")
    if build_dir.exists() and not build_dir.is_dir():
        raise LatexWorkflowError("build destination must be a directory")
    build_dir.mkdir(exist_ok=True)
    # Preflight all destinations before performing ANY compilation/output.
    artifact_names = ["validation.json"]
    for kind in DEFAULT_TEMPLATES:
        artifact_names += [f"{kind}.pdf", f"{kind}.txt", f"{kind}_page1.png", f"{kind}.log", f"{kind}.build.log"]
    for name in artifact_names:
        path = _file(root, paths.position_directory + "/build/" + name)
        if path.exists() and not path.is_file():
            raise LatexWorkflowError("Build destination is not a regular file: " + name)
    outputs: dict[str, bytes] = {}
    results: dict[str, Any] = {}
    source_dirs = sorted({entry["path"].parent for entry in sources.values()}, key=str)
    for kind, text in drafts.items():
        results[kind], built = _compile_one(kind, text, source_dirs, directory)
        results[kind]["unchanged_from_source"] = (
            results[kind]["source_sha256"] == manifest["sources"][kind]["sha256"]
        )
        outputs.update(built)
    report: dict[str, Any] = {
        "status": "ok" if all(item["ok"] for item in results.values()) else "validation_failed",
        "validated_at_utc": _now(),
        "position_directory": paths.position_directory,
        "manifest": paths.position_directory + "/" + MANIFEST_NAME,
        "sources": manifest["sources"],
        "documents": results,
        "visual_review_required": True,
    }
    output_report = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Invalidate any prior success report immediately. Publish the current
    # report LAST, after all outputs have been successfully written/removed.
    # This avoids a green status with missing or stale PDFs after a write error.
    report_path = _file(root, paths.position_directory + "/build/validation.json")
    report_path.unlink(missing_ok=True)
    for name in artifact_names:
        if name == "validation.json":
            continue
        target = _file(root, paths.position_directory + "/build/" + name)
        if name in outputs:
            _write_atomic(target, outputs[name])
        elif target.is_file():
            target.unlink()
    _write_atomic(report_path, output_report)
    return report
