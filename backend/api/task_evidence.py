"""Small, read-only evidence summaries; never serialize raw model request audits."""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
import re
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.shared import ApplicationError
from .task_files import task_workspace
from .test_source_routes import source_language


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestRunEvidence(EvidenceModel):
    tool_call_id: str
    sequence: int = Field(ge=1)
    runner: Literal["maven", "pytest"]
    tests: int = Field(gt=0)
    passed: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class TestComparison(EvidenceModel):
    before: TestRunEvidence | None = None
    after: TestRunEvidence | None = None
    final_verified: bool = False
    original_tests_unchanged: bool | None = None
    comparable: bool = False
    unavailable_reason: str | None = None


class FailureCase(EvidenceModel):
    test: str
    error_type: str
    message: str
    source_file: str | None = None
    source_line: int | None = Field(default=None, ge=1)


class ReferenceRole(EvidenceModel):
    role: Literal["diagnosis", "repair", "review", "single"]
    request_count: int = Field(ge=1)


class ReferenceEvidence(EvidenceModel):
    experience_id: str
    title: str
    source_task_id: str | None = None
    scope: Literal["global", "project"] | None = None
    content_sha256: str
    roles: list[ReferenceRole]
    request_count: int = Field(ge=1)
    detail_available: bool


class TaskEvidence(EvidenceModel):
    task_id: str
    status: str
    source_project: str | None = None
    project_label: str | None = None
    tests: TestComparison
    failure_cases: list[FailureCase] = Field(default_factory=list)
    references: list[ReferenceEvidence] = Field(default_factory=list)
    reference_recording: Literal["recorded", "unavailable"]
    reference_notice: str


def _safe_text(value, limit=300):
    if not isinstance(value, str):
        return ""
    # Public evidence never needs host paths, credentials or line breaks.
    value = re.sub(r"[A-Za-z]:[\\/][^\s，。；]*|(?<!\w)/(?:[^\s/]+/)+[^\s，。；]*", "[路径已省略]", value)
    value = re.sub(r"(?i)(?:bearer\s+|(?:api[_-]?key|token|password)\s*[:=]\s*)\S+", "[敏感值已省略]", value)
    return " ".join(value.split())[:limit]


def _test_run(step):
    if step.call.name != "run_tests" or step.result.tool_name != "run_tests":
        return None
    data = step.result.metadata
    if data.get("runner") not in {"maven", "pytest"}:
        return None
    counts = [data.get(name) for name in ("tests", "failures", "errors", "skipped")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    tests, failures, errors, skipped = counts
    if not tests or failures + errors + skipped > tests:
        return None
    return TestRunEvidence(tool_call_id=step.call.id, sequence=step.sequence, runner=data["runner"],
        tests=tests, passed=tests-failures-errors-skipped, failures=failures, errors=errors, skipped=skipped)


def _baseline(steps):
    for index, step in enumerate(steps):
        if step.call.name != "run_tests":
            continue
        if step.result.metadata.get("final_verification") is True:
            return None
        # The first test alone is insufficient: old records may omit changed-files evidence.
        if step.result.metadata.get("changed_files") != []:
            return None
        if any(previous.call.name in {"write_file", "run_command"} for previous in steps[:index]):
            return None
        return step if _test_run(step) else None
    return None


ERROR_MESSAGES = {
    "NullPointerException": "访问了缺失的对象或输入。",
    "NumberFormatException": "输入文本不能解析为数字。",
    "IndexOutOfBoundsException": "访问的下标超出了列表范围。",
    "ArrayIndexOutOfBoundsException": "访问的下标超出了数组范围。",
    "AssertionError": "实际结果与测试预期不一致。",
    "AssertionFailedError": "实际结果与测试预期不一致。",
    "IllegalArgumentException": "输入参数不符合约束。",
    "TypeError": "输入类型或空值处理不符合要求。",
    "ValueError": "输入值不能按预期处理。",
    "IndexError": "访问的下标超出了序列范围。",
    "KeyError": "读取了不存在的键。",
}


def _failure_cases(baseline, record, manager):
    if baseline is None:
        return []
    raw = baseline.result.metadata.get("test_failures", [])
    if not isinstance(raw, list):
        return []
    files = [name for name in record.baseline if source_language(name)] if record else []
    result = []
    for item in raw[:100]:
        if not isinstance(item, dict) or not isinstance(item.get("test"), str):
            continue
        test = _safe_text(item["test"], 300)
        kind = item.get("error_type", "")
        kind = kind if isinstance(kind, str) and re.fullmatch(r"[\w.$]{1,200}", kind) else "TestFailure"
        source, line = None, None
        if "::" in test:
            candidate, method = test.split("::", 1)
            candidates = [name for name in files if name == candidate]
            method = method.split("::")[-1].split("[")[0]
        else:
            parts = test.rsplit(".", 2)
            method = parts[-1]
            basename = parts[-2].split("$")[0] + ".java" if len(parts) > 1 else ""
            candidates = [name for name in files if PurePosixPath(name).name == basename]
        if len(candidates) == 1:
            source = candidates[0]
            try:
                # Only read a verified original test; a later edit must not invent a source location.
                content = manager.resolve(record, source).read_text(encoding="utf-8")
                pattern = re.compile(r"\b(?:def\s+)?" + re.escape(method) + r"\s*\(")
                matching = [i for i, text in enumerate(content.splitlines(), 1) if pattern.search(text)]
                line = matching[0] if len(matching) == 1 else None
            except (OSError, UnicodeError, ApplicationError):
                source = None
        result.append(FailureCase(test=test, error_type=kind, message=ERROR_MESSAGES.get(kind.split(".")[-1],
            "原始测试记录了该错误，可查看对应测试与执行过程。"), source_file=source, source_line=line))
    return result


def _reference_evidence(audit, experiences):
    """Count only successful transport audit rows, never a planned retrieval or role context."""
    groups = audit.get("model_requests", [])
    counts, recorded = {}, False
    if not isinstance(groups, list):
        return [], False
    for group in groups:
        if not isinstance(group, dict):
            continue
        nested = isinstance(group.get("requests"), list)
        role = group.get("role") if nested else "single"
        if role not in {"diagnosis", "repair", "review", "single"}:
            continue
        for row in group["requests"] if nested else [group]:
            if not isinstance(row, dict) or row.get("phase") == "extraction":
                continue
            ids, hashes = row.get("request_experience_ids"), row.get("request_experience_hashes")
            if not isinstance(ids, list) or not isinstance(hashes, list) or len(ids) != len(hashes):
                continue
            if row.get("success") is not True or type(row.get("http_status")) is not int or not 200 <= row["http_status"] < 300:
                continue
            seen = set()
            valid_pairs = []
            for identifier, digest in zip(ids, hashes):
                try:
                    key = (str(UUID(identifier)), digest)
                except (ValueError, TypeError, AttributeError):
                    break
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    break
                valid_pairs.append(key)
            if len(valid_pairs) != len(ids):
                continue
            recorded = True
            for key in valid_pairs:
                if key in seen:
                    continue
                seen.add(key)
                counts.setdefault(key, Counter())[role] += 1
    references = []
    for (identifier, digest), roles in counts.items():
        entry = experiences.get_experience(identifier)
        references.append(ReferenceEvidence(experience_id=identifier,
            title=_safe_text(entry.task_summary) if entry else "历史引用经验（详情暂不可用）",
            source_task_id=entry.source_task_id if entry else None, scope=entry.scope if entry else None,
            content_sha256=digest, roles=[ReferenceRole(role=role, request_count=count) for role, count in roles.items()],
            request_count=sum(roles.values()), detail_available=entry is not None))
    return references, recorded


def register_task_evidence_routes(app: FastAPI, tasks, manager, experiences, settings, ensure_recorded):
    @app.get("/api/tasks/{task_id}/evidence", response_model=TaskEvidence)
    def get_evidence(task_id: UUID, response: Response):
        key = str(task_id)
        ensure_recorded(key)
        try:
            status = tasks.get(key)
            request = tasks.get_request(key)
            steps = tasks.get_steps(key).steps
        except KeyError:
            raise HTTPException(404, "未找到该任务。") from None
        baseline = _baseline(steps)
        last = steps[-1] if steps else None
        final = last if last and last.result.metadata.get("final_verification") is True else None
        comparison = TestComparison(before=_test_run(baseline) if baseline else None,
                                    after=_test_run(final) if final else None)
        comparison.final_verified = bool(final and comparison.after and final.result.success
            and not (comparison.after.failures + comparison.after.errors + comparison.after.skipped)
            and status.result and status.result.tests_passed is True)
        record = None
        try:
            record = task_workspace(task_id, tasks, manager, ensure_recorded)
            with record.lock:
                manager.verify_tests(record)
                comparison.original_tests_unchanged = True
                cases = _failure_cases(baseline, record, manager)
        except HTTPException:
            cases = _failure_cases(baseline, None, manager)
        except (ApplicationError, OSError, ValueError):
            comparison.original_tests_unchanged = False
            cases = _failure_cases(baseline, None, manager)
        if comparison.before is None:
            comparison.unavailable_reason = "没有足够记录确认修复前的原始测试，暂不比较。"
        elif comparison.after is None:
            comparison.unavailable_reason = "尚无完整的最终测试记录。"
        elif comparison.before.sequence >= comparison.after.sequence or comparison.before.tool_call_id == comparison.after.tool_call_id:
            comparison.unavailable_reason = "修复前检查与最终测试缺少独立且有序的执行记录，暂不比较。"
        elif comparison.original_tests_unchanged is not True:
            comparison.unavailable_reason = "原始测试与构建配置尚未核实一致，暂不比较。"
        elif comparison.before.runner != comparison.after.runner or comparison.before.tests != comparison.after.tests:
            comparison.unavailable_reason = "前后测试执行器或测试总数不同，暂不比较。"
        else:
            comparison.comparable = True
        references, recorded = _reference_evidence(tasks.get_audit(key), experiences)
        label = None
        if request and request.source_project in settings.projects:
            label = _safe_text(settings.project(request.source_project).label)
        response.headers["Cache-Control"] = "no-store"
        return TaskEvidence(task_id=key, status=status.status.value,
            source_project=request.source_project if request else None, project_label=label, tests=comparison,
            failure_cases=cases, references=references, reference_recording="recorded" if recorded else "unavailable",
            reference_notice="仅列出成功模型请求中实际附带的经验；不据此推断模型采用程度或效率提升。" if recorded
                else "该任务尚无可核对的实际模型请求记录，不能确认经验引用。")
