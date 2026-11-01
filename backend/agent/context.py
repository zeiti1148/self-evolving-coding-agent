"""Versioned facts only; each role receives a new JSON serialization."""
from dataclasses import dataclass, field
import hashlib
import json
from uuid import uuid4

from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolCall
from backend.tools.workspace import ignored_path


@dataclass(frozen=True)
class TaskContext:
    request: AgentRequest
    snapshot_revision: str
    files: dict[str, str]
    failure_facts: list[dict] = field(default_factory=list)
    diagnoses: list[dict] = field(default_factory=list)
    reference_experiences: list[dict] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)

    def payload(self):
        return {"task_id": self.request.task_id, "task": self.request.task_description,
                "language": self.request.language, "repository_id": self.request.repository_id,
                "capabilities": self.request.capabilities, "snapshot_revision": self.snapshot_revision,
                "files": self.files, "failure_facts": self.failure_facts, "diagnoses": self.diagnoses,
                "reference_experiences": self.reference_experiences, "feedback": self.feedback}


def capture_context(request, execute_tool, **facts):
    def invoke(name, **arguments):
        call = ToolCall(id=str(uuid4()), name=name, arguments=arguments)
        result = execute_tool(request.workspace, call)
        if (not result.success or result.call_id != call.id or result.tool_name != name
                or result.metadata.get("truncated")):
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Cannot capture a complete bounded source snapshot.")
        return result.output
    listing = invoke("list_files", recursive=True, max_entries=2000)
    paths = [path for path in listing.splitlines() if path and not path.endswith("/") and not ignored_path(path)]
    files, size = {}, 0
    for path in paths:
        content = invoke("read_file", path=path, max_chars=200000)
        size += len(content.encode("utf-8"))
        if size > 400000:
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Role source snapshot exceeds the 400000-byte limit.")
        files[path] = content
    hashes = {path: hashlib.sha256(content.encode("utf-8")).hexdigest() for path, content in files.items()}
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return TaskContext(request.model_copy(deep=True), revision, files, **facts)
