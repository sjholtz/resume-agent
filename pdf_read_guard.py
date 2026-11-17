"""Prevent Deep Agents' multimodal ``read_file`` PDF outputs from reaching Ollama.

Deep Agents returns PDF ``file`` content blocks from its built-in ``read_file``
tool.  Some text-only Ollama adapters reject those blocks when building the next
model request, crashing the entire interactive session.  This middleware
short-circuits PDF ``read_file`` requests *before execution*.  It does not
rewrite or remove arbitrary conversation content, touch the filesystem, or
change permissions on the dedicated ``read_local_pdf`` tool.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

_PDF_READ_FILE_ERROR = (
    "Error: PDF files cannot be read with read_file in this Ollama session: "
    "the returned file content block is not supported by the model adapter. "
    "The PDF was NOT read. For candidate-owned PDF sources, use "
    "read_local_pdf with the virtual file_path under "
    "the configured candidate/evidence/sources/documents/ directory. "
    "If it is stored elsewhere, ask the candidate to copy it to that directory. "
    "Never invent extracted text or claim that read_file succeeded."
)


def _blocked_pdf_message(tool_call: dict[str, Any]) -> ToolMessage | None:
    """Return a model-visible error without executing an unsupported PDF read."""
    if tool_call.get("name") != "read_file":
        return None
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return None
    path = args.get("file_path")
    if not isinstance(path, str) or PurePosixPath(path).suffix.lower() != ".pdf":
        return None
    return ToolMessage(
        name="read_file",
        tool_call_id=tool_call.get("id") or "",
        content=_PDF_READ_FILE_ERROR,
        status="error",
    )


class PdfReadGuardMiddleware(AgentMiddleware):
    """Fail closed for PDF reads made through Deep Agents' built-in tool."""

    def wrap_tool_call(self, request, handler):
        blocked = _blocked_pdf_message(request.tool_call)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = _blocked_pdf_message(request.tool_call)
        return blocked if blocked is not None else await handler(request)
