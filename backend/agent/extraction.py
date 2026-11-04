"""Shared, one-call extraction after a program-owned final verification."""
from __future__ import annotations

import json
import re
import time

from pydantic import ValidationError

from backend.shared import ApplicationError, ErrorCode, ExperienceCreate, VerificationEvidence


def verification_passed(result, language: str) -> bool:
    counts = result.metadata
    runner = "maven" if language == "java" else "pytest"
    return (result.success and counts.get("runner", runner) == runner
            and type(counts.get("tests")) is int and counts["tests"] > 0
            and all(type(counts.get(key, 0)) is int and counts.get(key, 0) == 0
                    for key in ("failures", "errors", "skipped")))


def extraction_output_schema():
    """Describe only model-authored fields; provenance remains program-owned."""
    record = ExperienceCreate.model_json_schema()
    authored = {"task_summary", "error_type", "root_cause", "solution", "tags", "applicability",
                "issues", "required_capabilities"}
    definitions = record.pop("$defs", {})
    record["properties"] = {key: value for key, value in record["properties"].items() if key in authored}
    record["required"] = ["task_summary", "root_cause", "solution", "applicability", "issues"]
    for field in ("applicability", "issues"):
        record["properties"][field]["minItems"] = 1
    return {"type": "object", "additionalProperties": False, "required": ["experiences"],
            "properties": {"experiences": {"type": "array", "minItems": 1, "maxItems": 5, "items": record}},
            "$defs": {"RepairIssue": definitions["RepairIssue"]}}


def extract_and_save_experiences(llm, store, request, *, summary, changed_files, calls, results, deadline,
                                 diagnostic=None):
    """Record safe failure location, never model text, input values or exception messages."""
    diagnostic = {} if diagnostic is None else diagnostic
    diagnostic.clear()
    diagnostic.update(status="running", stage="preconditions")
    try:
        records = _extract_and_save_experiences(llm, store, request, summary=summary,
            changed_files=changed_files, calls=calls, results=results, deadline=deadline, diagnostic=diagnostic)
    except Exception as exc:
        diagnostic.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, ApplicationError):
            diagnostic["error_code"] = exc.code.value
        if isinstance(exc, ValidationError):
            # Unknown object keys can themselves contain private model text.
            known_fields = set(ExperienceCreate.model_fields) | set(VerificationEvidence.model_fields) | {"files"}
            diagnostic["field_errors"] = [{
                "field": [part if type(part) is int or part in known_fields else "<unknown>"
                          for part in error["loc"]],
                "type": error["type"],
            } for error in exc.errors(include_url=False, include_context=False, include_input=False)[:20]]
        raise
    diagnostic.update(status="succeeded", stage="completed", saved_count=len(records))
    return records


def _extract_and_save_experiences(llm, store, request, *, summary, changed_files, calls, results, deadline,
                                  diagnostic):
    from .core import normalize_error_type

    def remaining():
        if time.monotonic() >= deadline:
            raise ApplicationError(ErrorCode.AGENT_STOPPED, "任务时限已用完。")

    remaining()
    if not request.repository_id:
        raise ValueError("Automatic extraction requires a registered repository identity")
    if (not calls or not results or calls[-1].name != "run_tests" or results[-1].call_id != calls[-1].id
            or not results[-1].metadata.get("final_verification") or not verification_passed(results[-1], request.language)):
        raise ValueError("Extraction requires independent passing final verification")
    if not changed_files:
        raise ValueError("No changed source files support a repair experience")
    runner = "maven" if request.language == "java" else "pytest"
    counts = results[-1].metadata
    evidence = VerificationEvidence(source_task_id=request.task_id, tool_call_id=calls[-1].id, runner=runner,
                                    tests=counts["tests"], failures=counts.get("failures", 0),
                                    errors=counts.get("errors", 0), skipped=counts.get("skipped", 0))
    diagnostic["stage"] = "model_response"
    response = llm.complete([
        {"role": "system", "content": (
            'Extract independent reusable repair knowledge. Return only JSON {"experiences":[...]} with 1–5 records, '
            "strictly conforming to the supplied output_schema. "
            "Each record has task_summary, error_type, root_cause, solution, tags, applicability and ordered issues "
            "[{error_type,root_cause,solution,files}]. Each must have a distinct purpose and precise prerequisites. "
            "tags, applicability, required_capabilities and each issue's files must be arrays of strings, never a string or object. "
            "applicability and issues must be nonempty. required_capabilities may only contain supplied capability identifiers. "
            "Do not add fields outside output_schema; omit verification, evidence, IDs, provenance and other program-owned fields. "
            "Group causally related defects in each record; do not copy records or split one rule merely to increase counts. "
            "For a complex repair, identify independent knowledge groups, each with its related problems and repair order. "
            "An experience is a reusable knowledge group; each issue describes one independently triggerable or testable "
            "failure condition and its corresponding repair rule. One source file may support multiple distinct issues. "
            "Keep distinct root causes, exception types or assertion failures as separate issues within that group, "
            "ordered by their prerequisite checks; do not compress all failures into one issue merely because they share a file. "
            "Use each issue's observed exception type or AssertionError when supported by the trace, rather than assigning "
            "the first observed exception to every issue. Do not split cases that differ only in input values but share "
            "the same root cause and repair rule. Preserve the number of genuinely distinct problems supported by evidence; "
            "a simple repair may have just one issue. "
            "Only name files actually changed, explain each group's supporting facts. A simple repair may yield one record. "
            "Use only the supplied trace. Do not invent evidence, business defaults, identifiers, secrets or hidden reasoning. "
            "The program owns repository, language, state, provenance and final test evidence. No tool calls. "
            "Write all human-facing summaries, root causes, solutions, applicability conditions and descriptive tags in Simplified Chinese. "
            "Preserve JSON keys, language tags, code literals, exception names, file paths and identifiers exactly." )},
        {"role": "user", "content": json.dumps({"task": request.task_description, "summary": summary,
            "language": request.language, "repository_id": request.repository_id, "changed_files": changed_files,
            "capabilities": request.capabilities, "output_schema": extraction_output_schema(),
            "trace": [r.model_dump(mode="json") for r in results]}, ensure_ascii=False)}])
    remaining()
    diagnostic["stage"] = "json_decode"
    if response.get("tool_calls"):
        raise ValueError("Extractor cannot request tools")
    content = response.get("content", "")
    if not isinstance(content, str):
        raise ValueError("Extractor must return JSON text")
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    payload = json.loads(content)
    legacy = isinstance(payload, dict) and "experiences" not in payload
    raw_items = [payload] if legacy else payload.get("experiences") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 5:
        raise ValueError("Extraction must contain 1–5 experiences")
    items = []
    for index, raw in enumerate(raw_items):
        diagnostic.update(stage="schema_validation", record_index=index)
        if not isinstance(raw, dict):
            raise ValueError("Every experience must be an object")
        data = dict(raw)
        data.update(schema_version=3, language=request.language, scope="project", repository_id=request.repository_id,
                    source_revision=request.source_revision, source_task_id=request.task_id,
                    reuse_status="active", batch_id=None, merged_from=[], derived_from=[], supersedes_id=None,
                    evidence=[evidence.model_dump()],
                    verification=f"程序独立运行 {runner} 最终验证，{evidence.tests} 项测试全部通过；工具调用编号：{calls[-1].id}。")
        if "error_type" in data:
            data["error_type"] = normalize_error_type(data["error_type"])
        if legacy and not data.get("issues") and data.get("error_type"):
            data["issues"] = [dict(error_type=data["error_type"], root_cause=data.get("root_cause"),
                                  solution=data.get("solution"), files=changed_files)]
        if legacy and not data.get("applicability"):
            data["applicability"] = ["必须保持已登记仓库原有测试验证的业务要求。"]
        for issue in data.get("issues", []):
            if isinstance(issue, dict):
                issue["error_type"] = normalize_error_type(issue.get("error_type"))
        if isinstance(data.get("tags", []), list):
            data["tags"] = [request.language, *[tag for tag in data.get("tags", []) if tag not in {"java", "python"}]]
        item = ExperienceCreate.model_validate(data)
        diagnostic["stage"] = "source_grounding"
        if not item.applicability or not item.issues or any(
                not issue.files or not set(issue.files).issubset(changed_files) for issue in item.issues):
            raise ValueError("Every rule requires conditions and issues supported by changed files")
        diagnostic["stage"] = "capability_validation"
        if not set(item.required_capabilities).issubset(request.capabilities):
            raise ValueError("Unregistered capability in extracted experience")
        items.append(item)
    remaining()
    diagnostic["stage"] = "storage"
    return store.save_experiences(items, source_task_id=request.task_id)
