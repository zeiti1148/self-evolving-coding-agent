"""Shared, one-call extraction after a program-owned final verification."""
from __future__ import annotations

import json
import re
import time

from backend.shared import ApplicationError, ErrorCode, ExperienceCreate, VerificationEvidence


def verification_passed(result, language: str) -> bool:
    counts = result.metadata
    runner = "maven" if language == "java" else "pytest"
    return (result.success and counts.get("runner", runner) == runner
            and type(counts.get("tests")) is int and counts["tests"] > 0
            and all(type(counts.get(key, 0)) is int and counts.get(key, 0) == 0
                    for key in ("failures", "errors", "skipped")))


def extract_and_save_experiences(llm, store, request, *, summary, changed_files, calls, results, deadline):
    from .core import normalize_error_type

    def remaining():
        if time.monotonic() >= deadline:
            raise ApplicationError(ErrorCode.AGENT_STOPPED, "Task time budget exhausted.")

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
    response = llm.complete([
        {"role": "system", "content": (
            'Extract independent reusable repair knowledge. Return only JSON {"experiences":[...]} with 1–5 records. '
            "Each record has task_summary, error_type, root_cause, solution, tags, applicability and ordered issues "
            "[{error_type,root_cause,solution,files}]. Each must have a distinct purpose and precise prerequisites. "
            "Group causally related defects in each record; do not copy records or split one rule merely to increase counts. "
            "For a complex repair, identify independent knowledge groups, each with its related problems and repair order. "
            "Only name files actually changed, explain each group's supporting facts. A simple repair may yield one record. "
            "Use only the supplied trace. Do not invent evidence, business defaults, identifiers, secrets or hidden reasoning. "
            "The program owns repository, language, state, provenance and final test evidence. No tool calls." )},
        {"role": "user", "content": json.dumps({"task": request.task_description, "summary": summary,
            "language": request.language, "changed_files": changed_files,
            "capabilities": request.capabilities, "trace": [r.model_dump(mode="json") for r in results]}, ensure_ascii=False)}])
    remaining()
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
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Every experience must be an object")
        data = dict(raw)
        data.update(schema_version=3, language=request.language, scope="project", repository_id=request.repository_id,
                    source_revision=request.source_revision, source_task_id=request.task_id,
                    reuse_status="active", batch_id=None, merged_from=[], derived_from=[], supersedes_id=None,
                    evidence=[evidence.model_dump()],
                    verification=f"Final {runner} verification passed: {evidence.tests} tests; call_id={calls[-1].id}.")
        if "error_type" in data:
            data["error_type"] = normalize_error_type(data["error_type"])
        if legacy and not data.get("issues") and data.get("error_type"):
            data["issues"] = [dict(error_type=data["error_type"], root_cause=data.get("root_cause"),
                                  solution=data.get("solution"), files=changed_files)]
        if legacy and not data.get("applicability"):
            data["applicability"] = ["Preserve the registered repository's original tested business contract."]
        for issue in data.get("issues", []):
            if isinstance(issue, dict):
                issue["error_type"] = normalize_error_type(issue.get("error_type"))
        if isinstance(data.get("tags", []), list):
            data["tags"] = [request.language, *[tag for tag in data.get("tags", []) if tag not in {"java", "python"}]]
        item = ExperienceCreate.model_validate(data)
        if not item.applicability or not item.issues or any(
                not issue.files or not set(issue.files).issubset(changed_files) for issue in item.issues):
            raise ValueError("Every rule requires conditions and issues supported by changed files")
        if not set(item.required_capabilities).issubset(request.capabilities):
            raise ValueError("Unregistered capability in extracted experience")
        items.append(item)
    remaining()
    return store.save_experiences(items, source_task_id=request.task_id)
