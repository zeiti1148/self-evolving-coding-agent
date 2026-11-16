"""Run the unchanged pre-update Agent through the normal task factory hook."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

from backend.shared import AgentResult, ApplicationError, ErrorCode, TaskStep, ToolCall


class LegacyAgentBridge:
    """Old model/store/retrieval code, shared upload and physical test environment.

    The hosting before-arm must disable its new automatic knowledge workers and
    use a separate database. No production database is allowed by this bridge.
    Only the normal single-agent task factory is supported by this protocol.
    """
    def __init__(self, settings, tools, *, old_code: Path, project_root: Path, output_root: Path):
        self.settings, self.tools = settings, tools
        self.old_code, self.project_root, self.output_root = (Path(value).resolve() for value in (old_code, project_root, output_root))
        if not settings.database_path.resolve().is_relative_to(self.output_root):
            raise ValueError("Before-arm database must stay inside the isolated comparison output")
        if settings.database_path.resolve() == (self.project_root / "data/app.db").resolve():
            raise ValueError("Comparison cannot use the production database")
        if not settings.workspace_root.resolve().is_relative_to(self.output_root):
            raise ValueError("Before-arm workspaces must stay inside the isolated comparison output")
        self.last_run_audit = {}

    def run(self, request, *, on_step=None):
        if request.execution_mode != "single":
            raise ValueError("Comparison protocol only allows single-agent trials")
        worker = self.project_root / "scripts/compare_legacy_worker.py"
        folder = self.output_root / "legacy-worker-logs"
        folder.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        # Both arms perform the same real upload preflight. The old kernel does
        # not receive this result as a prompt: its original reasoning/retrieval
        # behavior, including any repeated test it chooses, remains observable.
        baseline_call = ToolCall(id=str(uuid4()), name="run_tests", arguments={"timeout_seconds": 180})
        baseline_started = time.monotonic()
        baseline = self.tools.execute_tool(request.workspace, baseline_call)
        baseline.metadata["retrieved_experience_ids"] = []
        baseline_ms = round((time.monotonic() - baseline_started) * 1000)
        if on_step:
            on_step(TaskStep(sequence=1, call=baseline_call, result=baseline))
        from backend.agent.orchestrator import repairable_test_failure
        if not baseline.success and not repairable_test_failure(baseline, request.language):
            raise ApplicationError(baseline.error_code or ErrorCode.COMMAND_FAILED,
                                   "原始项目的测试环境尚不可用，请查看首轮测试输出并调整依赖或测试配置。")
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        # No inherited credential identity can override this project's file.
        for name in ("LLM_API_KEY", "LLM_API_BASE_URL", "LLM_MODEL"):
            environment.pop(name, None)
        with (folder / f"{request.task_id}.stderr.log").open("w", encoding="utf-8") as errors:
            process = subprocess.Popen([sys.executable, "-X", "utf8", str(worker), "--old-code", str(self.old_code)],
                cwd=self.old_code, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                encoding="utf-8", bufsize=1, env=environment)
            messages = queue.Queue()

            def read():
                try:
                    for line in process.stdout:
                        messages.put(line)
                finally:
                    messages.put(None)

            reader = threading.Thread(target=read, daemon=True)
            reader.start()

            def send(value):
                process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                process.stdin.flush()

            send(dict(kind="start", project_root=str(self.project_root),
                      expected_base_url=self.settings.llm_base_url.rstrip("/"), expected_model=self.settings.llm_model,
                      database_path=str(self.settings.database_path), workspace_root=str(self.settings.workspace_root),
                      llm_timeout=self.settings.llm_timeout,
                      task_timeout=max(0.001, self.settings.task_timeout - (time.monotonic() - started)),
                      use_experience=self.settings.use_experience, request=request.model_dump(mode="json")))
            try:
                while True:
                    remaining = started + self.settings.task_timeout + self.settings.llm_timeout + 30 - time.monotonic()
                    if remaining <= 0:
                        raise ApplicationError(ErrorCode.AGENT_STOPPED, "更新前对照任务超过执行时限。")
                    try:
                        line = messages.get(timeout=remaining)
                    except queue.Empty:
                        raise ApplicationError(ErrorCode.AGENT_STOPPED, "更新前对照任务超过执行时限。") from None
                    if line is None:
                        raise ApplicationError(ErrorCode.LLM_ERROR, "更新前对照内核未返回结果，请查看隔离日志。")
                    value = json.loads(line)
                    if value["kind"] == "tool":
                        if Path(value["workspace"]).resolve() != request.workspace.resolve():
                            raise ValueError("Comparison worker changed workspace")
                        call = ToolCall.model_validate(value["call"])
                        result = self.tools.execute_tool(request.workspace, call)
                        send({"kind": "tool_result", "result": result.model_dump(mode="json")})
                    elif value["kind"] == "step":
                        if on_step:
                            step = TaskStep.model_validate(value["step"])
                            on_step(step.model_copy(update={"sequence": step.sequence + 1}))
                        send({"kind": "ack"})
                    elif value["kind"] == "result":
                        self.last_run_audit = value["audit"]
                        elapsed = round((time.monotonic() - started) * 1000)
                        self.last_run_audit.update(kernel_elapsed_ms=self.last_run_audit.get("elapsed_ms"),
                            elapsed_ms=elapsed, comparison_wall_elapsed_ms=elapsed,
                            tool_calls=self.last_run_audit.get("tool_calls", 0) + 1,
                            adapter_initial_verification={"call_id": baseline_call.id, "elapsed_ms": baseline_ms,
                                "model_context_injected": False, "sequence_offset": 1,
                                "metadata": baseline.metadata})
                        result = dict(value["result"])
                        result["steps"] = [baseline_call.model_dump(mode="json"), *result["steps"]]
                        result["tool_results"] = [baseline.model_dump(mode="json"), *result["tool_results"]]
                        return AgentResult.model_validate(result)
                    elif value["kind"] == "worker_error":
                        raise ApplicationError(ErrorCode.LLM_ERROR, "更新前对照内核初始化失败：" + value["error_type"])
                    else:
                        raise ValueError("Unexpected comparison worker message")
            finally:
                if process.poll() is None:
                    try:
                        process.stdin.close()
                        process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                        process.wait(timeout=5)
                process.stdout.close()
                reader.join(timeout=2)
