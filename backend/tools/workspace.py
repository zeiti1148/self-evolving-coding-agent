"""Managed copies of trusted fixtures, including persistent ownership records.

Path validation is defense in depth for local trusted projects, not an OS sandbox.
All links/reparse points are rejected, including links whose targets are in bounds.
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from threading import RLock
from uuid import UUID

from backend.shared import ApplicationError, ErrorCode

IGNORED = {".git", "target", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
SECRET_PATTERNS = {".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", ".ssh"}


def sensitive_path(path: str | Path) -> bool:
    return any(fnmatch.fnmatchcase(part.casefold(), pattern) for part in Path(path).parts for pattern in SECRET_PATTERNS)


def ignored_path(path: str | Path) -> bool:
    return any(part.casefold() in IGNORED for part in Path(path).parts) or sensitive_path(path)


def copy_ignored(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if ignored_path(name)}


def reject_links(path: Path) -> None:
    for part in [*reversed(path.absolute().parents), path.absolute()]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "不允许使用链接或重解析点。")


def inventory(root: Path) -> dict[str, str]:
    """Hash source files; inspect even excluded entries before pruning the walk."""
    result = {}
    reject_links(root)
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in dirs + files:
            reject_links(parent / name)
        for name in files:
            path = parent / name
            if ignored_path(path.relative_to(root)):
                continue
            if path.stat().st_size > 10_000_000:
                raise ApplicationError(ErrorCode.FILE_IO_ERROR, "项目文件超过 10 MB 限制。")
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if len(result) > 10_000:
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "项目文件数量超过限制。")
        dirs[:] = [name for name in dirs if not ignored_path(name)]
    return result


def protected(files: dict[str, str]) -> dict[str, str]:
    # Windows aliases are case-insensitive, and Maven still reads .MVN/maven.config.
    # Apply the same conservative policy on every host so a project cannot become
    # writable merely by moving between platforms.
    def is_protected(name: str) -> bool:
        parts = [part.casefold() for part in Path(name).parts]
        base = parts[-1]
        return (sensitive_path(name) or base == "pom.xml" or "src/test/" in name.casefold() or
                ".mvn" in parts or base.startswith("mvnw") or
                any(part in {"test", "tests"} for part in parts) or
                base in {"pyproject.toml", "pytest.ini", "setup.cfg", "setup.py", "tox.ini",
                         "conftest.py", "sitecustomize.py", "usercustomize.py", "uv.lock",
                         "poetry.lock", "pipfile", "pipfile.lock", ".python-version", ".agent-project.json"} or
                base.startswith("requirements") or
                base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py")))
    return {name: digest for name, digest in files.items() if is_protected(name)}


@dataclass
class Workspace:
    path: Path
    source: Path
    baseline: dict[str, str]
    lock: RLock = field(default_factory=RLock)
    before_code: dict[str, str | None] | None = None


class WorkspaceManager:
    def __init__(self, root: Path, projects: dict[str, Path]):
        reject_links(root)
        self.root = root.resolve()
        self.projects = {name: path.absolute() for name, path in projects.items()}
        self.records: dict[Path, Workspace] = {}
        self.lock = RLock()

    def validate_source(self, source: Path) -> Path:
        reject_links(source)
        path = source.resolve()
        allowed = {p.resolve() for p in self.projects.values()}
        if path not in allowed or not path.is_dir() or path.is_relative_to(self.root) or self.root.is_relative_to(path):
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "源项目尚未登记或当前不可用。")
        return path

    def _record_path(self, task_id: str) -> Path:
        return self.root / ".records" / (task_id + ".json")

    def create_workspace(self, source_project: Path, task_id: str) -> Path:
        try:
            task_id = str(UUID(task_id))
            source = self.validate_source(source_project)
            reject_links(self.root)
            baseline = inventory(source)
            target = self.root / task_id
            with self.lock:
                if target.exists() or self._record_path(task_id).exists():
                    raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "任务工作目录已存在。")
                self.root.mkdir(parents=True, exist_ok=True)
                # Do not follow links during copy; inspect the copied tree again.
                shutil.copytree(source, target, symlinks=True, ignore=copy_ignored)
                if inventory(target) != baseline:
                    raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "复制过程中源文件发生变化。")
                from .code_changes import capture_before
                before_code = capture_before(target, baseline)
                record_path = self._record_path(task_id)
                reject_links(record_path)
                record_path.parent.mkdir(exist_ok=True)
                record_path.write_text(json.dumps({"source": str(source), "baseline": baseline, "before_code": before_code}, ensure_ascii=False), encoding="utf-8")
                self.records[target] = Workspace(target, source, baseline, before_code=before_code)
            return target
        except ApplicationError:
            raise
        except (OSError, ValueError) as exc:
            raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "无法创建任务工作目录。") from exc

    def get(self, workspace: Path) -> Workspace:
        reject_links(workspace)
        path = workspace.resolve()
        if not workspace.is_absolute() or path.parent != self.root or not path.is_dir():
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "该目录不是受管理的任务工作目录。")
        try:
            task_id = str(UUID(path.name))
            if task_id != path.name:
                raise ValueError("noncanonical task id")
            with self.lock:
                if path not in self.records:
                    record_path = self._record_path(task_id)
                    reject_links(record_path)
                    data = json.loads(record_path.read_text(encoding="utf-8"))
                    source = self.validate_source(Path(data["source"]))
                    baseline = data["baseline"]
                    if not isinstance(baseline, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in baseline.items()):
                        raise ValueError("invalid manifest")
                    before_code = data.get("before_code")
                    if before_code is not None and (not isinstance(before_code, dict) or not all(
                            key in baseline and (value is None or isinstance(value, str)) for key, value in before_code.items())):
                        raise ValueError("invalid original source snapshot")
                    self.records[path] = Workspace(path, source, baseline, before_code=before_code)
                return self.records[path]
        except ApplicationError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "无法读取工作目录的归属记录。") from exc

    def resolve(self, record: Workspace, value: str) -> Path:
        if not value or "\x00" in value or "\\" in value or ":" in value:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "请使用工作目录内的相对路径，并以正斜杠分隔。")
        relative = Path(value)
        if relative.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in relative.parts:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "路径超出任务工作目录。")
        # Windows aliases (trailing dots/spaces, device files) are not project files.
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if any(p.rstrip(" .") != p or p.split(".")[0].upper() in reserved for p in relative.parts):
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "项目路径无效。")
        candidate = record.path / relative
        reject_links(candidate)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(record.path):
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "路径超出任务工作目录。")
        return resolved

    def verify_tests(self, record: Workspace) -> None:
        if protected(inventory(record.path)) != protected(record.baseline):
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "不得修改样例测试和构建配置。")

    def changed_files(self, workspace: Path) -> list[str]:
        record = self.get(workspace)
        with record.lock:
            current = inventory(record.path)
            return sorted(k for k in current.keys() | record.baseline.keys() if current.get(k) != record.baseline.get(k))

    def cleanup_workspace(self, workspace: Path) -> None:
        record = self.get(workspace)
        with record.lock, self.lock:
            # Revalidate the actual absolute target immediately before recursive deletion.
            self.get(workspace)
            inventory(record.path)
            from .code_changes import snapshot_path
            changes = snapshot_path(self, record)
            try:
                shutil.rmtree(record.path)
                self._record_path(record.path.name).unlink()
                changes.unlink(missing_ok=True)
                self.records.pop(record.path, None)
            except OSError as exc:
                raise ApplicationError(ErrorCode.WORKSPACE_CLEANUP_FAILED, "无法清理任务工作目录。") from exc

    def capture_changes(self, workspace: Path) -> None:
        from .code_changes import freeze_changes
        freeze_changes(self, self.get(workspace))
