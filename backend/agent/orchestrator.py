"""Bounded diagnosis → proposal → independent review → program apply/verify workflow."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import math
import re
import time
from threading import RLock
from uuid import uuid4

from backend.experience.policy import to_model_reference
from backend.shared import AgentEvent, AgentResult, ApplicationError, ErrorCode, TaskStep, ToolCall, ToolResult
from .budget import RunBudget
from .context import capture_context
from .core import error_types_in
from .extraction import extract_and_save_experiences, verification_passed
from .proposals import apply_proposal
from .roles import ROLE_LABELS, RoleRunner


def repairable_test_failure(result, language):
    if result.error_code == ErrorCode.TEST_FAILED:
        return True
    if result.error_code != ErrorCode.COMMAND_FAILED or result.metadata.get("runner") != ("maven" if language == "java" else "pytest"):
        return False
    # A compiler/collector rejecting application code is repair feedback, not a successful test.
    # Missing executables, dependency downloads, timeouts, empty/skipped tests stay terminal.
    if language == "java":
        return "COMPILATION ERROR" in result.output or "Compilation failure" in result.output
    return result.metadata.get("exit_code") == 2 and bool(re.search(
        r"\b(?:SyntaxError|IndentationError|TabError|ImportError)\b", result.output))


def client_usage(client, started):
    values = [event.get("usage", {}).get("total_tokens") for event in getattr(client, "audit", [])]
    return {"reported_tokens": sum(values) if values and all(type(value) is int for value in values) else None,
            "elapsed_ms": round((time.monotonic()-started)*1000)}


class MultiAgentCoordinator:
    def __init__(self, llm_factory, execute_tool, experience_store, *, use_experience=True, timeout_seconds=600):
        self.llm_factory, self.execute_tool, self.experiences = llm_factory, execute_tool, experience_store
        self.use_experience, self.timeout_seconds = use_experience, timeout_seconds
        self.last_run_audit = {}

    def run(self, request, *, on_step=None, on_event=None):
        started = time.monotonic()
        budget = RunBudget(request.max_steps, started+self.timeout_seconds)
        roles = RoleRunner(self.llm_factory, budget)
        calls, results, events, changed, retrieval_events = [], [], [], [], []
        facts, diagnoses, feedback, observed = [], [], [], error_types_in(request.task_description, request.language)
        lock = RLock()
        coordinator_id = str(uuid4())
        snapshot = None

        def emit(role, agent_id, phase, status, summary, revision=None, usage=None):
            with lock:
                event = AgentEvent(event_id=str(uuid4()), task_id=request.task_id, agent_id=agent_id, role=role,
                    phase=phase, status=status, sequence=len(events)+1, snapshot_revision=revision,
                    summary=summary[:2000], usage=usage or {})
                events.append(event)
                if on_event:
                    try:
                        on_event(event.model_copy(deep=True))
                    except Exception:
                        raise ApplicationError(ErrorCode.INTERNAL_ERROR, "无法保存角色过程记录。") from None

        def execute(workspace, call, *, final=False):
            nonlocal changed
            remaining = budget.remaining_seconds()
            if call.name in {"run_tests", "run_command"}:
                call = call.model_copy(update={"arguments": {**call.arguments,
                    "timeout_seconds": min(180, max(1, math.floor(remaining)))}})
            try:
                result = self.execute_tool(workspace, call)
                if result.call_id != call.id or result.tool_name != call.name:
                    raise ValueError("mismatched tool response")
            except Exception:
                result = ToolResult(call_id=call.id, tool_name=call.name, success=False,
                                    error_code=ErrorCode.INTERNAL_TOOL_ERROR, error_message="工具执行失败。")
            result = result.model_copy(deep=True)
            if final:
                if result.success and not verification_passed(result, request.language):
                    result = ToolResult(call_id=call.id, tool_name=call.name, success=False,
                        output=result.output, metadata=result.metadata, error_code=ErrorCode.TEST_FAILED,
                        error_message="最终验证必须包含实际执行且通过的测试证据。")
                result.metadata["final_verification"] = True
            if isinstance(result.metadata.get("changed_files"), list):
                changed = list(result.metadata["changed_files"])
            calls.append(call.model_copy(deep=True)); results.append(result)
            if on_step:
                try:
                    on_step(TaskStep(sequence=len(calls), call=call, result=result.model_copy(deep=True)))
                except Exception:
                    raise ApplicationError(ErrorCode.INTERNAL_ERROR, "无法保存已完成的工具步骤。") from None
            budget.remaining_seconds()
            return result

        def retrieve():
            if not self.use_experience:
                return []
            try:
                entries = self.experiences.retrieve_experiences(language=request.language, repository_id=request.repository_id,
                    error_types=observed, capabilities=request.capabilities, limit=5)
                with lock:
                    retrieval_events.append({"experience_ids": [entry.id for entry in entries], "error_types": list(observed)})
                return [to_model_reference(entry) for entry in entries]
            except Exception:
                with lock:
                    retrieval_events.append({"experience_ids": [], "error": "经验检索暂时不可用"})
                return []

        def capture():
            return capture_context(request, execute, failure_facts=list(facts), diagnoses=list(diagnoses),
                                   feedback=list(feedback[-10:]), reference_experiences=retrieve())

        def run_role(role, context, proposal=None):
            agent_id = str(uuid4())
            context = replace(context, reference_experiences=retrieve())
            emit(role, agent_id, role, "started", f"{ROLE_LABELS[role]}角色已启动，使用独立上下文。", context.snapshot_revision)
            try:
                if role == "diagnosis": value = roles.diagnose(context, agent_id)
                elif role == "repair": value = roles.propose(context, agent_id)
                else: value = roles.review(context, proposal, agent_id)
                audit = next(item for item in roles.audit if item["agent_id"] == agent_id)
                summary = (value.summary if role == "diagnosis" else
                           f"已提交修改提案 {value.proposal_id}，涉及 {len(value.changes)} 个源文件。" if role == "repair" else
                           ("复核通过：" if value.accepted else "复核未通过：") + "；".join(value.reasons))
                emit(role, agent_id, role, "completed", summary, context.snapshot_revision,
                     {"reported_tokens": audit["reported_tokens"], "elapsed_ms": audit["elapsed_ms"]})
                return value
            except Exception as exc:
                stopped = isinstance(exc, ApplicationError) and exc.code == ErrorCode.AGENT_STOPPED
                audit = next((item for item in roles.audit if item["agent_id"] == agent_id), {})
                emit(role, agent_id, role, "stopped" if stopped else "failed",
                     exc.message if isinstance(exc, ApplicationError) else f"{ROLE_LABELS[role]}失败。", context.snapshot_revision,
                     {"reported_tokens": audit.get("reported_tokens"), "elapsed_ms": audit.get("elapsed_ms", 0)})
                raise

        status, summary, error = "failed", "协作任务尚未完成。", None
        repaired, passed, saved, ids, save_error = False, None, None, [], None
        try:
            baseline = execute(request.workspace, ToolCall(id=str(uuid4()), name="run_tests"))
            if not baseline.success and not repairable_test_failure(baseline, request.language):
                raise ApplicationError(baseline.error_code, baseline.error_message)
            facts.append(baseline.model_dump(mode="json"))
            observed = list(dict.fromkeys([*observed, *baseline.metadata.get("error_types", [])]))[:20]
            snapshot = capture()
            # The coordinator does not write while these two independent diagnostics run.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnosis") as pool:
                futures = [pool.submit(run_role, "diagnosis", snapshot) for _ in range(2)]
                diagnoses = [future.result().model_dump(mode="json") for future in futures]
            while True:
                if budget.decisions >= request.max_steps:
                    raise ApplicationError(ErrorCode.AGENT_STOPPED, "所有角色共用的决策次数已用完。")
                snapshot = capture()
                proposal = run_role("repair", snapshot)
                review = run_role("review", snapshot, proposal)
                if not review.accepted:
                    feedback.append("独立复核未通过：" + "; ".join(review.reasons))
                    continue
                # A fresh complete snapshot checks changes during model calls, before any writes.
                current = capture()
                emit("coordinator", coordinator_id, "apply", "started", f"正在检查修改提案 {proposal.proposal_id}。", current.snapshot_revision)
                try:
                    apply_proposal(proposal, request, execute, snapshot_revision=current.snapshot_revision)
                except ApplicationError as exc:
                    emit("coordinator", coordinator_id, "apply", "failed", exc.message, current.snapshot_revision)
                    if exc.code == ErrorCode.INVALID_TOOL_CALL and exc.details.get("stale_proposal"):
                        feedback.append(exc.message)
                        continue
                    raise
                emit("coordinator", coordinator_id, "apply", "completed", f"已应用通过复核的修改提案 {proposal.proposal_id}。", current.snapshot_revision)
                emit("coordinator", coordinator_id, "verify", "started", "程序正在独立运行最终测试。")
                final = execute(request.workspace, ToolCall(id=str(uuid4()), name="run_tests"), final=True)
                passed = True if final.success else False if final.error_code == ErrorCode.TEST_FAILED else None
                emit("coordinator", coordinator_id, "verify", "completed" if passed else "failed",
                     f"最终测试：共 {final.metadata.get('tests')} 项，失败 {final.metadata.get('failures')} 项，错误 {final.metadata.get('errors')} 项，跳过 {final.metadata.get('skipped')} 项。")
                if passed:
                    status, repaired, summary = "succeeded", True, "经过复核的修改已通过程序独立运行的最终测试。"
                    break
                if not repairable_test_failure(final, request.language):
                    raise ApplicationError(final.error_code, final.error_message)
                facts = [final.model_dump(mode="json")]
                observed = list(dict.fromkeys([*observed, *final.metadata.get("error_types", [])]))[:20]
                feedback.append("独立运行的最终测试仍有失败，请根据新的失败信息继续修复。")
        except ApplicationError as exc:
            status = "stopped" if exc.code == ErrorCode.AGENT_STOPPED else "failed"
            error, summary = exc.message, "协作任务已停止。" if status == "stopped" else "协作任务失败。"
        except Exception:
            status, error, summary = "failed", "角色调用或程序协调操作失败。", "协作任务失败。"

        if status == "succeeded":
            extractor, extractor_id = None, str(uuid4())
            extraction_started = time.monotonic()
            try:
                budget.remaining_seconds()
                emit("extractor", extractor_id, "extract", "started", "正在依据最终测试结果提取经验，整批保存。")
                extractor = self.llm_factory()
                if any(extractor is client for client in roles.clients):
                    raise ApplicationError(ErrorCode.LLM_ERROR, "经验提取必须使用独立的模型客户端。")
                if hasattr(extractor, "set_deadline"): extractor.set_deadline(budget.deadline)
                records = extract_and_save_experiences(extractor, self.experiences, request, summary=summary,
                    changed_files=changed, calls=calls, results=results, deadline=budget.deadline)
                ids, saved = [entry.id for entry in records], True
                emit("extractor", extractor_id, "extract", "completed", "已保存经验：" + ", ".join(ids),
                     usage=client_usage(extractor, extraction_started))
            except Exception:
                saved, save_error = False, "经验提取、校验或保存失败。"
                ids = []
                emit("extractor", extractor_id, "extract", "failed", save_error, usage=client_usage(extractor, extraction_started))
            finally:
                if extractor and hasattr(extractor, "close"):
                    try: extractor.close()
                    except Exception: pass
                reports = list(getattr(extractor, "audit", []))
                values = [event.get("usage", {}).get("total_tokens") for event in reports]
                roles.audit.append({"agent_id": extractor_id, "role": "extractor", "success": saved is True,
                    "reported_tokens": sum(values) if values and all(type(value) is int for value in values) else None,
                    "elapsed_ms": round((time.monotonic()-extraction_started)*1000), "requests": reports,
                    "request_experience_ids": []})
        self.last_run_audit = {"task_id": request.task_id, "execution_mode": "multi", "decision_rounds": budget.decisions,
            "tool_calls": len(calls), "elapsed_ms": round((time.monotonic()-started)*1000),
            "retrieval_events": retrieval_events,
            "retrieved_experience_ids": list(dict.fromkeys(identifier for event in retrieval_events for identifier in event["experience_ids"])),
            "model_requests": sorted(roles.audit, key=lambda event: event.get("decision", request.max_steps+1)),
            "experience_ids": ids, "final_verification_attempts": sum(bool(result.metadata.get("final_verification")) for result in results)}
        return AgentResult(task_id=request.task_id, status=status, summary=summary, steps=calls, tool_results=results,
            repair_succeeded=repaired, tests_passed=passed, changed_files=changed, experience_saved=saved,
            experience_ids=ids, experience_save_error=save_error, error_message=error)
