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
            raise ApplicationError(ErrorCode.INTERNAL_TOOL_ERROR, "修改提案的工具返回结果与请求不一致。")
        trace.append((call, result))
        return result
    def reject(message, *, stale=False):
        raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, message,
                               details={"trace": trace, "restoration_succeeded": True, "stale_proposal": stale})
    if proposal.base_revision != snapshot_revision:
        reject("修改提案的代码快照已过期，请读取最新源文件后重新提交。", stale=True)
    suffix = ".py" if request.language == "python" else ".java"
    for change in proposal.changes:
        if protected({change.path: ""}) or ignored_path(change.path) or PurePosixPath(change.path).suffix != suffix:
            reject("修改提案只能更改业务源文件，测试和配置受保护。")
        result = invoke("read_file", path=change.path, max_chars=200000)
        if not result.success or result.metadata.get("truncated"):
            reject("提案引用的源文件不可读或超出快照范围。")
        if hashlib.sha256(result.output.encode("utf-8")).hexdigest() != change.before_sha256:
            reject("修改提案的文件校验值已过期，请读取最新源文件后重新提交。", stale=True)
        originals[change.path] = result.output
    try:
        for change in proposal.changes:
            attempted.append(change.path)
            result = invoke("write_file", path=change.path, content=change.content)
            if not result.success:
                raise ApplicationError(ErrorCode.FILE_IO_ERROR, "写入修改提案失败。")
    except Exception:
        restored = True
        for path in reversed(attempted):
            try:
                restored = invoke("write_file", path=path, content=originals[path]).success and restored
            except Exception:
                restored = False
        raise ApplicationError(ErrorCode.FILE_IO_ERROR,
            "应用修改提案失败，已恢复原文件。" if restored else "应用修改提案及恢复原文件均失败，任务已停止。",
            details={"trace": trace, "restoration_succeeded": restored}) from None
    return trace
