"""Minimal synchronous Chat Completions transport; all errors are safe to display."""
from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from backend.config import Settings
from backend.shared import ApplicationError, ErrorCode
from backend.tools.service import ARGUMENTS

TOOL_DESCRIPTIONS = {
    "list_files": "List files in the managed project; use recursive=true to locate source and tests.",
    "read_file": "Read a UTF-8 file using relative POSIX paths. Preserves whitespace.",
    "write_file": "Atomically replace a source file. Tests and build configuration are read-only.",
    "run_command": "Only java/javac -version, mvn -version, mvn compile, mvn test are permitted.",
    "run_tests": "Run the original Maven tests. Read error codes and output before deciding what to fix.",
}
TOOLS = [{"type": "function", "function": {"name": name, "description": TOOL_DESCRIPTIONS[name],
          "parameters": model.model_json_schema()}} for name, model in ARGUMENTS.items()]


class LLM(Protocol):
    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None) -> dict: ...


class ChatClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None) -> dict:
        config = self.settings
        if (not config.llm_api_key or config.llm_api_key == "replace_with_your_api_key" or
                not config.llm_model or config.llm_model == "example-model" or
                not config.llm_base_url or "api.example.com" in config.llm_base_url):
            raise ApplicationError(ErrorCode.LLM_ERROR, "Configure LLM_API_BASE_URL, LLM_API_KEY and LLM_MODEL in the local .env file.")
        payload = {"model": config.llm_model, "messages": messages, "stream": False}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        try:
            timeout = httpx.Timeout(config.llm_timeout, connect=min(10.0, config.llm_timeout))
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                response = client.post(config.llm_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + config.llm_api_key}, json=payload)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("response too large")
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ValueError("incomplete response")
            return validate_message(choice["message"])
        except httpx.HTTPStatusError as exc:
            raise ApplicationError(ErrorCode.LLM_ERROR, f"Model service returned HTTP {exc.response.status_code}.") from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ApplicationError(ErrorCode.LLM_ERROR, "Model request failed, timed out or returned an invalid response.") from None


def validate_message(message: dict) -> dict:
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ApplicationError(ErrorCode.LLM_ERROR, "Invalid model message.")
    content = message.get("content")
    calls = message.get("tool_calls") or []
    if content is not None and not isinstance(content, str):
        raise ApplicationError(ErrorCode.LLM_ERROR, "Invalid model content.")
    if not isinstance(calls, list) or len(calls) > 20 or (not calls and not content):
        raise ApplicationError(ErrorCode.LLM_ERROR, "Model response must contain text or bounded tool calls.")
    ids = set()
    clean = []
    for call in calls:
        try:
            raw_id, function = call["id"], call["function"]
            name, arguments = function["name"], function["arguments"]
            if (call.get("type") != "function" or not isinstance(raw_id, str) or not raw_id or raw_id in ids or
                    not isinstance(name, str) or not name.strip() or not isinstance(arguments, str)):
                raise ValueError("invalid call")
            ids.add(raw_id)
            clean.append({"id": raw_id, "type": "function", "function": {"name": name, "arguments": arguments}})
        except (KeyError, TypeError, ValueError):
            raise ApplicationError(ErrorCode.LLM_ERROR, "Model returned invalid or duplicate tool call IDs.") from None
    result = {"role": "assistant", "content": content}
    if clean:
        result["tool_calls"] = clean
    return result


def parse_arguments(value: str) -> dict:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, RecursionError):
        pass
    # Keep an invalid call in the trace and let the strict dispatcher return INVALID_TOOL_CALL.
    return {"__invalid_arguments": value[:2000]}
