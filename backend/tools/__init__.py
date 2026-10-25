"""Public tool contracts and an injectable service used by API/tests."""
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from backend.config import Settings
from backend.shared import ToolCall, ToolResult
from .service import ToolService
from .workspace import WorkspaceManager


@lru_cache(maxsize=1)
def default_service() -> ToolService:
    settings = Settings.from_env()
    return ToolService(WorkspaceManager(settings.workspace_root, settings.projects), settings.maven_home)


def create_workspace(source_project: Path, task_id: str) -> Path:
    return default_service().manager.create_workspace(source_project, task_id)


def cleanup_workspace(workspace: Path) -> None:
    default_service().manager.cleanup_workspace(workspace)


def execute_tool(workspace: Path, call: ToolCall) -> ToolResult:
    return default_service().execute_tool(workspace, call)


def _call(workspace: Path, name: str, arguments: dict) -> ToolResult:
    return execute_tool(workspace, ToolCall(id=str(uuid4()), name=name, arguments=arguments))


def list_files(workspace: Path, path: str = ".", recursive: bool = False, max_entries: int = 200) -> ToolResult:
    return _call(workspace, "list_files", dict(path=path, recursive=recursive, max_entries=max_entries))


def read_file(workspace: Path, path: str, start_line: int = 1, end_line: int | None = None, max_chars: int = 50_000) -> ToolResult:
    return _call(workspace, "read_file", dict(path=path, start_line=start_line, end_line=end_line, max_chars=max_chars))


def write_file(workspace: Path, path: str, content: str, create_parent: bool = False) -> ToolResult:
    return _call(workspace, "write_file", dict(path=path, content=content, create_parent=create_parent))


def run_command(workspace: Path, command: list[str], cwd: str = ".", timeout_seconds: int = 60) -> ToolResult:
    return _call(workspace, "run_command", dict(command=command, cwd=cwd, timeout_seconds=timeout_seconds))


def run_tests(workspace: Path, project_path: str = ".", timeout_seconds: int = 180) -> ToolResult:
    return _call(workspace, "run_tests", dict(project_path=project_path, timeout_seconds=timeout_seconds))
