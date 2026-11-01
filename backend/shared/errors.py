"""Stable error codes and a small cross-module service exception."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Error codes that may cross backend module boundaries."""

    INVALID_TOOL_CALL = "INVALID_TOOL_CALL"
    INVALID_TASK = "INVALID_TASK"
    INVALID_WORKSPACE = "INVALID_WORKSPACE"
    PATH_OUTSIDE_WORKSPACE = "PATH_OUTSIDE_WORKSPACE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_IO_ERROR = "FILE_IO_ERROR"
    COMMAND_NOT_ALLOWED = "COMMAND_NOT_ALLOWED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    TEST_FAILED = "TEST_FAILED"
    WORKSPACE_CREATE_FAILED = "WORKSPACE_CREATE_FAILED"
    WORKSPACE_CLEANUP_FAILED = "WORKSPACE_CLEANUP_FAILED"
    EXPERIENCE_VALIDATION_ERROR = "EXPERIENCE_VALIDATION_ERROR"
    EXPERIENCE_STORAGE_ERROR = "EXPERIENCE_STORAGE_ERROR"
    EXPERIENCE_CONFLICT = "EXPERIENCE_CONFLICT"
    EXPERIENCE_NOT_FOUND = "EXPERIENCE_NOT_FOUND"
    LLM_ERROR = "LLM_ERROR"
    AGENT_STOPPED = "AGENT_STOPPED"
    INTERNAL_TOOL_ERROR = "INTERNAL_TOOL_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApplicationError(Exception):
    """Expected service error carrying a stable code and safe details."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"
