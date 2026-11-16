"""Isolated d724266 repair kernel. JSON pipes carry tools, never model credentials.

This is an internal compatibility worker, not an HTTP or demonstration entry.
The caller owns ordinary upload/task routes and equally configured real tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

OLD_REVISION = "d724266393588a1e93ec70f37b9351ef05288c12"
CORE_FILES = ("backend/agent/core.py", "backend/agent/extraction.py", "backend/agent/llm.py",
              "backend/experience/store.py", "backend/experience/policy.py", "backend/shared/models.py")


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def receive():
    value = sys.stdin.readline()
    if not value:
        raise RuntimeError("Comparison caller disconnected")
    return json.loads(value)


def kernel_identity(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                            encoding="utf-8", check=True, timeout=10)
    if result.stdout.strip() != OLD_REVISION:
        raise ValueError("Comparison baseline must be exactly d724266")
    dirty = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "HEAD", "--", "backend"],
                           capture_output=True, encoding="utf-8", check=True, timeout=10)
    if dirty.stdout.strip():
        raise ValueError("Comparison baseline backend was modified")
    return {"git_head": OLD_REVISION,
            "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CORE_FILES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-code", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    root = args.old_code.resolve(strict=True)
    identity = kernel_identity(root)
    sys.path.insert(0, str(root))
    from backend.agent.core import Agent
    from backend.agent.llm import ChatClient
    from backend.config import Settings
    from backend.experience import ExperienceStore
    from backend.shared import AgentRequest, ToolResult

    # Verify that every imported kernel really belongs to the detached baseline.
    imported = Path(sys.modules[Agent.__module__].__file__).resolve()
    if not imported.is_relative_to(root):
        raise ValueError("Wrong kernel imported")
    identity["agent_module"] = str(imported)
    if args.probe:
        emit({"kind": "probe", "identity": identity, "model_requests": 0,
              "request_fields": sorted(AgentRequest.model_fields)})
        return 0
    incoming = receive()
    if incoming.get("kind") != "start":
        raise ValueError("Expected comparison start")
    from dotenv import dotenv_values
    project_root = Path(incoming["project_root"]).resolve(strict=True)
    profile_file = project_root / ".env"
    profile = dotenv_values(profile_file)
    url = (profile.get("LLM_API_BASE_URL") or "").rstrip("/")
    model = profile.get("LLM_MODEL") or ""
    if not profile.get("LLM_API_KEY") or (url, model) != (incoming["expected_base_url"], incoming["expected_model"]):
        raise ValueError("Project model identity does not match comparison settings")
    settings = Settings(database_path=Path(incoming["database_path"]),
                        workspace_root=Path(incoming["workspace_root"]),
                        llm_base_url=url, llm_model=model, llm_api_key=profile["LLM_API_KEY"],
                        llm_trust_env=False, llm_timeout=float(incoming["llm_timeout"]),
                        task_timeout=float(incoming["task_timeout"]), use_experience=bool(incoming["use_experience"]))
    request_data = {key: value for key, value in incoming["request"].items() if key in AgentRequest.model_fields}
    request = AgentRequest.model_validate(request_data)
    if request.execution_mode != "single":
        raise ValueError("The registered comparison protocol uses single-agent trials")
    client = ChatClient(settings)

    def execute(workspace, call):
        emit({"kind": "tool", "workspace": str(workspace), "call": call.model_dump(mode="json")})
        value = receive()
        if value.get("kind") != "tool_result":
            raise RuntimeError("Missing tool response")
        result = ToolResult.model_validate(value["result"])
        if result.call_id != call.id or result.tool_name != call.name:
            raise RuntimeError("Mismatched tool response")
        return result

    def step(value):
        emit({"kind": "step", "step": value.model_dump(mode="json")})
        if receive().get("kind") != "ack":
            raise RuntimeError("Task recorder rejected step")

    agent = Agent(client, execute, ExperienceStore(settings.database_path),
                  use_experience=settings.use_experience, timeout_seconds=settings.task_timeout)
    result = agent.run(request, on_step=step)
    audit = dict(agent.last_run_audit)
    audit["comparison_kernel"] = identity | {
        "adapter": "ordinary-upload-and-identical-tools-json-rpc-v1",
        "model_config_file": str(profile_file), "base_url": url, "model": model,
        "kernel_unchanged": True, "automatic_knowledge_workers": False,
        "dropped_new_request_fields": sorted(set(incoming["request"]) - set(AgentRequest.model_fields))}
    emit({"kind": "result", "result": result.model_dump(mode="json"), "audit": audit})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Provider exceptions can contain paths or credentials; expose only type.
        emit({"kind": "worker_error", "error_type": type(exc).__name__})
        raise SystemExit(1)
