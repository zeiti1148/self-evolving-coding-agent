"""Read-only links to original test files retained in an owned task workspace."""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import stat
from typing import Annotated, Callable
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response

from backend.shared import ApplicationError
from backend.shared.models import TestSourceFile, TestSourceIndex, TestSourceInfo
from backend.tools.workspace import Workspace, WorkspaceManager, ignored_path
from .store import TaskStore
from .task_files import task_workspace


MAX_SOURCE_BYTES = 200_000


def source_language(path: str) -> str | None:
    """Only test code, never arbitrary project files or configuration."""
    if ignored_path(path):
        return None
    parts = PurePosixPath(path.casefold()).parts
    if not parts:
        return None
    name = parts[-1]
    if name.endswith(".java") and any(parts[i:i + 2] == ("src", "test") for i in range(len(parts) - 2)):
        return "java"
    if name.endswith(".py") and (any(p in {"test", "tests"} for p in parts[:-1])
                               or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"):
        return "python"
    return None


def register_test_source_routes(app: FastAPI, tasks: TaskStore, manager: WorkspaceManager,
                                ensure_recorded: Callable) -> None:
    def workspace(task_id: UUID) -> Workspace:
        return task_workspace(task_id, tasks, manager, ensure_recorded)

    @app.get("/api/tasks/{task_id}/test-sources", response_model=TestSourceIndex)
    def list_sources(task_id: UUID, response: Response):
        record = workspace(task_id)
        with record.lock:
            files = [TestSourceInfo(path=path, language=language, sha256=digest)
                     for path, digest in sorted(record.baseline.items())
                     if (language := source_language(path)) is not None]
        if len(files) > 1_000:
            raise HTTPException(413, "测试文件超过 1000 个，暂时无法展示。")
        response.headers["Cache-Control"] = "no-store"
        return TestSourceIndex(task_id=str(task_id), files=files)

    @app.get("/api/tasks/{task_id}/test-source", response_model=TestSourceFile)
    def read_source(task_id: UUID, response: Response,
                    path: Annotated[str, Query(min_length=1, max_length=1_000)]):
        record = workspace(task_id)
        with record.lock:
            language = source_language(path)
            if path not in record.baseline or language is None:
                raise HTTPException(404, "该文件不在任务原有的测试源码中。")
            try:
                target = manager.resolve(record, path)
                if not stat.S_ISREG(target.stat().st_mode):
                    raise HTTPException(404, "测试文件已不存在或不是普通文件。")
                with target.open("rb") as stream:
                    raw = stream.read(MAX_SOURCE_BYTES + 1)
            except ApplicationError:
                raise HTTPException(403, "不允许读取此测试文件路径。") from None
            except OSError:
                raise HTTPException(404, "测试文件已不存在或无法读取。") from None
            if len(raw) > MAX_SOURCE_BYTES:
                raise HTTPException(413, "测试源码超过 200 KB，暂时无法展示。")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != record.baseline[path]:
                raise HTTPException(409, "测试文件已被改动，不能作为该任务的原始测试源码展示。")
            try:
                content = raw.decode("utf-8")
                if "\x00" in content:
                    raise ValueError("binary content")
            except (UnicodeError, ValueError):
                raise HTTPException(415, "暂时只能查看 UTF-8 编码的文本源码。") from None
        response.headers["Cache-Control"] = "no-store"
        return TestSourceFile(task_id=str(task_id), path=path, language=language,
                              sha256=digest, content=content, size_bytes=len(raw))
