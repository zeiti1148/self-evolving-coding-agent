"""Preflight every change, write through tools, restore attempted writes on failure."""
import hashlib
from pathlib import PurePosixPath
from uuid import uuid4

from backend.shared import ApplicationError, ErrorCode, PatchProposal, ToolCall
from backend.tools.workspace import ignored_path, protected


def apply_proposal(proposal, request, execute_tool, *, snapshot_revision):
    proposal = PatchProposal.model_validate(proposal.model_dump())
    trace, originals, attempted = [], {}, []
    def invoke(name, **arguments):
        call = ToolCall(id=str(uuid4()), name=name, arguments=arguments)
        result = execute_tool(request.workspace, call)
        if result.call_id != call.id or result.tool_name != call.name:
            raise ApplicationError(ErrorCode.INTERNAL_TOOL_ERROR, "Mismatched proposal tool result.")
        trace.append((call, result))
        return result
    def reject(message):
        raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, message, details={"trace": trace, "restoration_succeeded": True})
    if proposal.base_revision != snapshot_revision:
        reject("Stale proposal snapshot; read current source before proposing again.")
    suffix = ".py" if request.language == "python" else ".java"
    for change in proposal.changes:
        if protected({change.path: ""}) or ignored_path(change.path) or PurePosixPath(change.path).suffix != suffix:
            reject("Proposal may change application source only; tests and configuration are protected.")
        result = invoke("read_file", path=change.path, max_chars=200000)
        if not result.success or result.metadata.get("truncated"):
            reject("Proposal source is unavailable or exceeds the bounded snapshot.")
        if hashlib.sha256(result.output.encode("utf-8")).hexdigest() != change.before_sha256:
            reject("Stale proposal file hash; read current source before proposing again.")
        originals[change.path] = result.output
    try:
        for change in proposal.changes:
            attempted.append(change.path)
            result = invoke("write_file", path=change.path, content=change.content)
            if not result.success:
                raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Proposal write failed.")
    except Exception:
        restored = True
        for path in reversed(attempted):
            try:
                restored = invoke("write_file", path=path, content=originals[path]).success and restored
            except Exception:
                restored = False
        raise ApplicationError(ErrorCode.FILE_IO_ERROR,
            "Proposal application failed; originals restored." if restored else "Proposal application and restoration failed; task stopped.",
            details={"trace": trace, "restoration_succeeded": restored}) from None
    return trace
