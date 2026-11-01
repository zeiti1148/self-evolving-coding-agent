"""Pydantic v2 models shared by the API, Agent, tools, and experience modules."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .errors import ErrorCode


MAX_TEXT_LENGTH = 50_000
MAX_OUTPUT_LENGTH = 200_000
MAX_STEPS = 100


def _canonical_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("must be a valid UUID string") from exc


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must include timezone information")
    return value


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            raise ValueError("tags must not contain blank values")
        key = cleaned.casefold()
        if key not in seen:
            normalized.append(cleaned)
            seen.add(key)
    return normalized


def _normalize_conditions(values: list[str]) -> list[str]:
    # Conditions can contain case-sensitive identifiers and significant spaces.
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("applicability must not contain blank values")
    return list(dict.fromkeys(cleaned))


def _validate_relative_file(path: str) -> str:
    if "\\" in path:
        raise ValueError("must use a workspace-relative POSIX path")
    parsed = PurePosixPath(path)
    if path in {"", "."} or parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError("must be a file path relative to the workspace")
    return path


class SharedModel(BaseModel):
    """Strict defaults for all public data contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ToolName(str, Enum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    RUN_TESTS = "run_tests"


class TestSourceInfo(SharedModel):
    path: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=1_000)]
    language: Literal["java", "python"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TestSourceIndex(SharedModel):
    task_id: str
    files: list[TestSourceInfo] = Field(max_length=1_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)


class TestSourceFile(TestSourceInfo):
    task_id: str
    content: Annotated[str, StringConstraints(strip_whitespace=False, max_length=200_000)]
    size_bytes: int = Field(strict=True, ge=0, le=200_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)


KNOWN_TOOL_NAMES: frozenset[str] = frozenset(tool.value for tool in ToolName)
ToolIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=1)
]


def _validate_tool_name(value: str) -> str:
    if not value.strip():
        raise ValueError("tool name must not be blank")
    return value


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class ToolCall(SharedModel):
    id: str
    name: ToolIdentifier
    arguments: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id")(_canonical_uuid)
    _validate_name = field_validator("name")(_validate_tool_name)


class ToolResult(SharedModel):
    call_id: str
    tool_name: ToolIdentifier
    success: bool
    output: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        default="", max_length=MAX_OUTPUT_LENGTH
    )
    error_code: ErrorCode | None = None
    error_message: str | None = Field(default=None, min_length=1, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_call_id = field_validator("call_id")(_canonical_uuid)
    _validate_name = field_validator("tool_name")(_validate_tool_name)

    @model_validator(mode="after")
    def validate_success_and_error(self) -> ToolResult:
        has_error = self.error_code is not None or self.error_message is not None
        if self.success and has_error:
            raise ValueError("successful tool results must not contain error fields")
        if not self.success and (
            self.error_code is None or self.error_message is None
        ):
            raise ValueError(
                "failed tool results require both error_code and error_message"
            )
        return self


class RepairIssue(SharedModel):
    """One repair rule; array order in an experience expresses repair order."""

    error_type: str = Field(min_length=1, max_length=200)
    root_cause: str = Field(min_length=1, max_length=5_000)
    solution: str = Field(min_length=1, max_length=10_000)
    files: list[Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=1_000)]] = Field(default_factory=list, max_length=50)

    @field_validator("files")
    @classmethod
    def validate_files(cls, values: list[str]) -> list[str]:
        normalized = []
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        for value in values:
            _validate_relative_file(value)
            path = PurePosixPath(value)
            if any(ord(character) < 32 or character in '<>:"|?*' for character in value):
                raise ValueError("files must be workspace-relative paths")
            if value.endswith("/") or value.split("/")[-1] == ".":
                raise ValueError("files must identify files, not directories")
            if any(part.rstrip(" .") != part or part.split(".")[0].upper() in reserved for part in path.parts):
                raise ValueError("files contain invalid path components")
            normalized.append(path.as_posix())
        return list(dict.fromkeys(normalized))


class VerificationEvidence(SharedModel):
    """Program-generated evidence for a completed independent final test run."""

    source_task_id: str
    tool_call_id: str
    runner: Literal["maven", "pytest"]
    tests: int = Field(strict=True, gt=0, le=10_000_000)
    failures: int = Field(default=0, strict=True, ge=0, le=0)
    errors: int = Field(default=0, strict=True, ge=0, le=0)
    skipped: int = Field(default=0, strict=True, ge=0, le=0)

    _validate_source_task_id = field_validator("source_task_id")(_canonical_uuid)
    _validate_tool_call_id = field_validator("tool_call_id")(_canonical_uuid)


class ExperienceCreate(SharedModel):
    task_summary: str = Field(min_length=1, max_length=1_000)
    error_type: str | None = Field(default=None, min_length=1, max_length=200)
    root_cause: str = Field(min_length=1, max_length=5_000)
    solution: str = Field(min_length=1, max_length=10_000)
    verification: str = Field(min_length=1, max_length=5_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_task_id: str | None = None
    schema_version: Literal[1, 2, 3] = 2
    language: Literal["java", "python"] = "java"
    scope: Literal["global", "project"] = "global"
    repository_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_revision: str | None = Field(default=None, min_length=1, max_length=200)
    applicability: list[Annotated[str, StringConstraints(min_length=1, max_length=1_000)]] = Field(default_factory=list, max_length=20)
    issues: list[RepairIssue] = Field(default_factory=list, max_length=50)
    evidence: list[VerificationEvidence] = Field(default_factory=list, max_length=100)
    merged_from: list[str] = Field(default_factory=list, max_length=10)
    batch_id: str | None = None
    reuse_status: Literal["active", "candidate", "legacy_unreviewed"] = "legacy_unreviewed"
    derived_from: list[str] = Field(default_factory=list, max_length=10)
    supersedes_id: str | None = None
    required_capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def default_reuse_status(cls, data):
        if isinstance(data, dict) and "reuse_status" not in data:
            data = dict(data)
            data["reuse_status"] = ("active" if data.get("scope") == "project" else
                                    "candidate" if data.get("schema_version") == 3 else "legacy_unreviewed")
        return data

    _validate_optional_ids = field_validator("batch_id", "supersedes_id")(
        lambda value: _canonical_uuid(value) if value is not None else value)
    _normalize_capabilities = field_validator("required_capabilities")(_normalize_conditions)

    _validate_source_task_id = field_validator("source_task_id")(
        lambda value: _canonical_uuid(value) if value is not None else value
    )
    _normalize_experience_tags = field_validator("tags")(_normalize_tags)
    _normalize_applicability = field_validator("applicability")(_normalize_conditions)

    @field_validator("merged_from", "derived_from")
    @classmethod
    def validate_merged_from(cls, values: list[str]) -> list[str]:
        canonical = [_canonical_uuid(value) for value in values]
        if len(set(canonical)) != len(canonical):
            raise ValueError("merged_from must contain distinct source IDs")
        return canonical

    @model_validator(mode="after")
    def validate_experience_scope(self) -> ExperienceCreate:
        if self.scope == "project" and self.repository_id is None:
            raise ValueError("project experience requires repository_id")
        if self.scope == "global" and self.repository_id is not None:
            raise ValueError("global experience must not carry repository_id")
        runner = "maven" if self.language == "java" else "pytest"
        if any(item.runner != runner for item in self.evidence):
            raise ValueError("verification runner must match experience language")
        if self.schema_version == 3 and self.reuse_status == "active" and not self.evidence:
            raise ValueError("active V3 experience requires verified evidence")
        return self


class Experience(ExperienceCreate):
    id: str
    created_at: datetime

    _validate_id = field_validator("id")(_canonical_uuid)
    _validate_created_at = field_validator("created_at")(_require_aware_datetime)


class AgentUsage(SharedModel):
    reported_tokens: int | None = Field(default=None, strict=True, ge=0)
    elapsed_ms: int = Field(default=0, strict=True, ge=0)


class AgentEvent(SharedModel):
    event_id: str
    task_id: str
    agent_id: str
    role: Literal["diagnosis", "repair", "review", "coordinator", "extractor"]
    phase: str = Field(min_length=1, max_length=100)
    status: Literal["started", "completed", "failed", "stopped"]
    sequence: int = Field(strict=True, ge=1)
    snapshot_revision: str | None = Field(default=None, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    usage: AgentUsage = Field(default_factory=AgentUsage)

    _validate_ids = field_validator("event_id", "task_id", "agent_id")(_canonical_uuid)
    _validate_date = field_validator("created_at")(
        lambda value: _require_aware_datetime(value).astimezone(timezone.utc))


class FileChange(SharedModel):
    path: str = Field(min_length=1, max_length=1000)
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(max_length=200000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value):
        return RepairIssue.validate_files([value])[0]


class PatchProposal(SharedModel):
    proposal_id: str
    agent_id: str
    base_revision: str = Field(min_length=1, max_length=200)
    changes: list[FileChange] = Field(min_length=1, max_length=20)

    _validate_ids = field_validator("proposal_id", "agent_id")(_canonical_uuid)

    @model_validator(mode="after")
    def validate_batch(self):
        if sum(len(change.content.encode("utf-8")) for change in self.changes) > 200000:
            raise ValueError("proposal exceeds 200000 UTF-8 bytes")
        paths = [change.path.casefold() for change in self.changes]
        if len(set(paths)) != len(paths):
            raise ValueError("proposal paths must be distinct, including case aliases")
        return self


class DiagnosisReport(SharedModel):
    issues: list[RepairIssue] = Field(default_factory=list, max_length=50)
    dependencies: list[tuple[Annotated[int, Field(strict=True)], Annotated[int, Field(strict=True)]]] = Field(default_factory=list, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_dependencies(self):
        remaining = set(range(len(self.issues)))
        if any(a not in remaining or b not in remaining for a, b in self.dependencies):
            raise ValueError("dependency index outside issues")
        while remaining:
            ready = {i for i in remaining if not any(b == i and a in remaining for a, b in self.dependencies)}
            if not ready:
                raise ValueError("cyclic issue dependencies")
            remaining -= ready
        return self


class ReviewDecision(SharedModel):
    accepted: bool = Field(strict=True)
    reasons: list[Annotated[str, StringConstraints(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def rejection_has_reason(self):
        if not self.accepted and not self.reasons:
            raise ValueError("rejected reviews require reasons")
        return self


class PromotionDraft(SharedModel):
    task_summary: str = Field(min_length=1, max_length=1000)
    error_type: str | None = Field(default=None, min_length=1, max_length=200)
    root_cause: str = Field(min_length=1, max_length=5000)
    solution: str = Field(min_length=1, max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    language: Literal["java", "python"]
    applicability: list[Annotated[str, StringConstraints(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=20)
    issues: list[RepairIssue] = Field(min_length=1, max_length=50)
    required_capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=30)

    _normalize_tags = field_validator("tags")(_normalize_tags)
    _normalize_conditions = field_validator("applicability", "required_capabilities")(_normalize_conditions)


class MergePreview(SharedModel):
    can_merge: bool
    conflicts: list[str]
    source_ids: list[str]
    preview: ExperienceCreate | None


class PromotionPreview(SharedModel):
    can_publish: bool
    conflicts: list[str]
    source_id: str
    current_global_id: str | None = None
    draft: PromotionDraft | None = None
    injection_preview: dict[str, Any] | None = None


class ProvenanceGraph(SharedModel):
    nodes: list[Experience]
    edges: list[dict[str, str]]
    truncated: bool = False


class AgentRequest(SharedModel):
    task_id: str
    task_description: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    workspace: Path
    max_steps: int = Field(default=20, ge=1, le=MAX_STEPS)
    language: Literal["java", "python"] = "java"
    repository_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_revision: str | None = Field(default=None, min_length=1, max_length=200)
    execution_mode: Literal["single", "multi"] = "single"
    capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=30)

    _normalize_capabilities = field_validator("capabilities")(_normalize_conditions)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("workspace must be an absolute managed path")
        return value


class AgentResult(SharedModel):
    task_id: str
    status: TaskStatus
    summary: str = Field(min_length=1, max_length=10_000)
    steps: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    repair_succeeded: bool
    tests_passed: bool | None = None
    changed_files: list[str] = Field(default_factory=list)
    experience_saved: bool | None = None
    experience_id: str | None = None
    experience_ids: list[str] = Field(default_factory=list, max_length=5)
    experience_save_error: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
    )
    error_message: str | None = Field(default=None, min_length=1, max_length=5_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_experience_id = field_validator("experience_id")(
        lambda value: _canonical_uuid(value) if value is not None else value
    )
    _validate_changed_files = field_validator("changed_files")(
        lambda paths: [_validate_relative_file(path) for path in paths]
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_experience_ids(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "experience_ids" not in data:
                data["experience_ids"] = [data["experience_id"]] if data.get("experience_id") else []
            elif "experience_id" not in data and isinstance(data["experience_ids"], list):
                data["experience_id"] = next(iter(data["experience_ids"]), None)
        return data

    @field_validator("experience_ids")
    @classmethod
    def validate_experience_ids(cls, values):
        values = [_canonical_uuid(value) for value in values]
        if len(set(values)) != len(values):
            raise ValueError("experience_ids must be distinct")
        return values

    @model_validator(mode="after")
    def validate_result_state(self) -> AgentResult:
        if self.experience_id != next(iter(self.experience_ids), None):
            raise ValueError("experience_id must match the first experience_ids item")
        if self.experience_saved is not True and self.experience_ids:
            raise ValueError("unsaved results cannot contain experience_ids")
        if self.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError("AgentResult status must be terminal")

        succeeded = self.status is TaskStatus.SUCCEEDED
        if succeeded and (
            not self.repair_succeeded or self.tests_passed is not True
        ):
            raise ValueError(
                "succeeded status requires repair_succeeded and tests_passed=True"
            )
        if self.repair_succeeded and self.tests_passed is not True:
            raise ValueError("successful repairs require tests_passed=True")

        if succeeded and self.experience_saved is None:
            raise ValueError(
                "successful repairs must report whether experience saving succeeded"
            )
        if not succeeded and self.experience_saved is not None:
            raise ValueError(
                "experience saving must not be attempted for unsuccessful repairs"
            )

        if self.experience_saved is True:
            if self.experience_id is None or self.experience_save_error is not None:
                raise ValueError(
                    "saved experience requires experience_id and no save error"
                )
        elif self.experience_saved is False:
            if self.experience_id is not None or self.experience_save_error is None:
                raise ValueError(
                    "failed experience saving requires an error and no experience_id"
                )
        elif self.experience_id is not None or self.experience_save_error is not None:
            raise ValueError(
                "experience fields must be empty when saving was not attempted"
            )

        call_ids = [call.id for call in self.steps]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call ids must be unique")
        result_ids = [result.call_id for result in self.tool_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("tool result call ids must be unique")
        if set(result_ids) != set(call_ids):
            raise ValueError("tool calls and results must have matching ids")
        call_names = {call.id: call.name for call in self.steps}
        if any(
            call_names[result.call_id] != result.tool_name
            for result in self.tool_results
        ):
            raise ValueError("tool calls and results must have matching names")
        if self.repair_succeeded:
            test_indexes = [
                index for index, call in enumerate(self.steps)
                if call.name == ToolName.RUN_TESTS.value
            ]
            final_index = test_indexes[-1] if test_indexes else None
            results_by_id = {result.call_id: result for result in self.tool_results}
            if (
                final_index is None
                or not results_by_id[self.steps[final_index].id].success
            ):
                raise ValueError(
                    "successful repairs require a successful final run_tests "
                    "call/result in the execution trace"
                )
            # Commands can mutate files, so they conservatively invalidate evidence.
            if any(
                call.name in {ToolName.WRITE_FILE.value, ToolName.RUN_COMMAND.value}
                for call in self.steps[final_index + 1:]
            ):
                raise ValueError(
                    "file writes or commands after final tests require re-verification"
                )
        return self


class TaskStep(SharedModel):
    sequence: int = Field(ge=1)
    call: ToolCall
    result: ToolResult
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    _validate_completed_at = field_validator("completed_at")(
        _require_aware_datetime
    )

    @model_validator(mode="after")
    def validate_call_result_pair(self) -> TaskStep:
        if self.call.id != self.result.call_id:
            raise ValueError("step call and result ids must match")
        if self.call.name != self.result.tool_name:
            raise ValueError("step call and result tool names must match")
        return self


class TaskCreateRequest(SharedModel):
    task_description: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    source_project: str = Field(min_length=1, max_length=1_000)
    max_steps: int = Field(default=20, ge=1, le=MAX_STEPS)
    execution_mode: Literal["single", "multi"] = "single"


class TaskCreateResponse(SharedModel):
    task_id: str
    status: Literal[TaskStatus.PENDING] = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_created_at = field_validator("created_at")(_require_aware_datetime)


class TaskStatusResponse(SharedModel):
    task_id: str
    status: TaskStatus
    steps_completed: int = Field(default=0, ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AgentResult | None = None
    error_message: str | None = Field(default=None, min_length=1, max_length=5_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_timestamps = field_validator(
        "created_at", "started_at", "finished_at"
    )(lambda value: _require_aware_datetime(value) if value is not None else value)

    @model_validator(mode="after")
    def validate_status_timeline(self) -> TaskStatusResponse:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.finished_at is not None:
            lower_bound = self.started_at or self.created_at
            if self.finished_at < lower_bound:
                raise ValueError("finished_at must not precede task start")

        if self.status is TaskStatus.PENDING:
            if any((self.started_at, self.finished_at, self.result)):
                raise ValueError("pending tasks cannot contain execution state")
        elif self.status is TaskStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None or self.result:
                raise ValueError("running tasks require only started_at")
        else:
            if self.finished_at is None:
                raise ValueError("terminal tasks require finished_at")
            if self.status is TaskStatus.SUCCEEDED and self.result is None:
                raise ValueError("succeeded tasks require an AgentResult")

        if self.result is not None:
            if self.result.task_id != self.task_id:
                raise ValueError("task response and result ids must match")
            if self.result.status is not self.status:
                raise ValueError("task response and result statuses must match")
        return self


class TaskStepsResponse(SharedModel):
    task_id: str
    status: TaskStatus
    steps: list[TaskStep] = Field(default_factory=list)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)

    @model_validator(mode="after")
    def validate_step_order(self) -> TaskStepsResponse:
        sequences = [step.sequence for step in self.steps]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("steps must have unique ascending sequence numbers")
        return self
