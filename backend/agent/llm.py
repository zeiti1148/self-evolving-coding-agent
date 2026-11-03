"""Minimal synchronous Chat Completions transport; all errors are safe to display."""
from __future__ import annotations

import json
import hashlib
import time
from typing import Any, Protocol

import httpx

from backend.config import Settings
from backend.shared import ApplicationError, ErrorCode
from backend.tools.service import ARGUMENTS
from .transport import TransportTrace, exception_chain

TOOL_DESCRIPTIONS = {
    "list_files": "List files in the managed project; use recursive=true to locate source and tests.",
    "read_file": "Read a UTF-8 file using relative POSIX paths. Preserves whitespace.",
    "write_file": "Atomically replace a source file. Tests and build configuration are read-only.",
    "run_command": "Only java/javac -version, mvn -version, mvn compile, mvn test are permitted.",
    "run_tests": "Run original acceptance tests with the registered Java/Maven or Python/pytest runner. Read test_failures, error_types and output, and repair every failure. Tests and build configuration are read-only.",
}
TOOLS = [{"type": "function", "function": {"name": name, "description": TOOL_DESCRIPTIONS[name],
          "parameters": model.model_json_schema()}} for name, model in ARGUMENTS.items()]


class LLM(Protocol):
    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None) -> dict: ...


class ChatClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.audit: list[dict] = []
        self.deadline: float | None = None
        self._client: httpx.Client | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def _remaining(self, request_deadline: float | None = None) -> float:
        now = time.monotonic()
        remaining = self.settings.llm_timeout
        if self.deadline is not None:
            if self.deadline <= now:
                raise ApplicationError(ErrorCode.AGENT_STOPPED, "任务时限已用完。")
            remaining = min(remaining, self.deadline - now)
        if request_deadline is not None:
            if request_deadline <= now:
                raise ApplicationError(ErrorCode.LLM_ERROR, "模型请求超过单次调用时限。")
            remaining = min(remaining, request_deadline - now)
        return remaining

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None) -> dict:
        config = self.settings
        # One fixed call budget includes connection retries; body keepalives cannot reset it.
        request_deadline = time.monotonic() + config.llm_timeout
        if self.deadline is not None:
            request_deadline = min(request_deadline, self.deadline)
        if (not config.llm_api_key or config.llm_api_key == "replace_with_your_api_key" or
                not config.llm_model or config.llm_model == "example-model" or
                not config.llm_base_url or "api.example.com" in config.llm_base_url):
            raise ApplicationError(ErrorCode.LLM_ERROR, "请在本地 .env 文件中配置 LLM_API_BASE_URL、LLM_API_KEY 和 LLM_MODEL。")
        payload = {"model": config.llm_model, "messages": messages, "stream": False}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        references = []
        role = None
        if len(messages) > 1:
            try:
                facts = json.loads(messages[1]["content"])
                references = facts.get("reference_experiences", [])
                role = facts.get("agent_role")
            except (ValueError, KeyError, TypeError):
                pass
        if self._client is None:
            self._client = httpx.Client(transport=self.transport, trust_env=config.llm_trust_env)
        # Retry connection establishment only: a read timeout may already incur model cost.
        for attempt in range(3):
            started = time.monotonic()
            trace = TransportTrace(started)
            event = {"phase": role or ("decision" if tools else "extraction"), "attempt": attempt + 1,
                     "transport_policy": "environment" if config.llm_trust_env else "direct",
                     "transport_events": trace.events,
                     "requested_model": config.llm_model, "success": False,
                     "request_experience_ids": [x["id"] for x in references],
                     "request_experience_hashes": [hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for x in references]}
            try:
                try:
                    remaining = self._remaining(request_deadline)
                    timeout = httpx.Timeout(remaining, connect=min(10.0, remaining))
                    with self._client.stream("POST", config.llm_base_url.rstrip("/") + "/chat/completions",
                                             headers={"Authorization": "Bearer " + config.llm_api_key,
                                                      "Accept-Encoding": "identity"}, json=payload, timeout=timeout,
                                             extensions={"trace": trace}) as response:
                        event["http_status"] = response.status_code
                        self._remaining(request_deadline)
                        response.raise_for_status()
                        # Observe raw bytes so compressed empty blocks cannot keep a read alive
                        # indefinitely without yielding a chunk to our deadline check.
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ValueError("unsupported response encoding")
                        body = bytearray()
                        chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
                        for chunk in chunks:
                            self._remaining(request_deadline)
                            body.extend(chunk)
                            if len(body) > 2_000_000:
                                raise ValueError("response too large")
                    self._remaining(request_deadline)
                    data = json.loads(body)
                    self._remaining(request_deadline)
                    choice = data["choices"][0]
                    event["finish_reason"] = choice.get("finish_reason")
                    if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                        raise ValueError("incomplete response")
                    message = validate_message(choice["message"])
                    usage = data.get("usage")
                    if isinstance(usage, dict):
                        event["usage"] = {key: value for key, value in usage.items()
                                          if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int and value >= 0}
                    self._remaining(request_deadline)
                    event.update(success=True, returned_model=data.get("model"), response_id=data.get("id"))
                    return message
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    event["error_type"] = type(exc).__name__
                    event.update(error_chain=exception_chain(exc), failure_stage=trace.failed_stage)
                    self._remaining(request_deadline)
                    if attempt == 2:
                        raise ApplicationError(ErrorCode.LLM_ERROR, "已尝试连接模型服务三次，仍无法连接。") from None
                    time.sleep(min(0.2 * (attempt + 1), self._remaining(request_deadline)))
                except httpx.HTTPStatusError as exc:
                    event["error_type"] = "HTTPStatusError"
                    self._remaining(request_deadline)
                    raise ApplicationError(ErrorCode.LLM_ERROR, f"模型服务返回 HTTP {exc.response.status_code}。") from None
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                    event["error_type"] = type(exc).__name__
                    event.update(error_chain=exception_chain(exc), failure_stage=trace.failed_stage)
                    self._remaining(request_deadline)
                    raise ApplicationError(ErrorCode.LLM_ERROR, "模型请求失败、超时或返回了无效响应。") from None
            except ApplicationError as exc:
                failure = exc
                try:
                    self._remaining(request_deadline)
                except ApplicationError as expired:
                    failure = expired
                # Preserve transport diagnostics; never serialize exception text or details.
                event.setdefault("error_type", type(failure).__name__)
                event["error_code"] = failure.code.value
                if failure is not exc:
                    raise failure from None
                raise
            except BaseException as exc:
                # Record interruptions without swallowing them or retrying a sent request.
                event.setdefault("error_type", type(exc).__name__[:64])
                raise
            finally:
                event["duration_ms"] = round((time.monotonic() - started) * 1000)
                self.audit.append(event)


def validate_message(message: dict) -> dict:
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ApplicationError(ErrorCode.LLM_ERROR, "模型返回的消息无效。")
    content = message.get("content")
    calls = message.get("tool_calls") or []
    if content is not None and not isinstance(content, str):
        raise ApplicationError(ErrorCode.LLM_ERROR, "模型返回的内容无效。")
    if not isinstance(calls, list) or len(calls) > 20 or (not calls and not content):
        raise ApplicationError(ErrorCode.LLM_ERROR, "模型响应必须包含文本或符合数量限制的工具调用。")
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
            raise ApplicationError(ErrorCode.LLM_ERROR, "模型返回的工具调用编号无效或重复。") from None
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
