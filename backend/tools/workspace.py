"""Managed copies of trusted fixtures, including persistent ownership records.

Path validation is defense in depth for local trusted projects, not an OS sandbox.
All links/reparse points are rejected, including links whose targets are in bounds.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from threading import RLock
from uuid import UUID

from backend.shared import ApplicationError, ErrorCode

IGNORED = {".git", "target", "node_modules", ".venv", "__pycache__"}


def reject_links(path: Path) -> None:
    for part in [*reversed(path.absolute().parents), path.absolute()]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "Links and reparse points are not allowed.")


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
            if any(part in IGNORED for part in path.relative_to(root).parts):
                continue
            if path.stat().st_size > 10_000_000:
                raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Project file exceeds the 10 MB limit.")
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if len(result) > 10_000:
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Project has too many files.")
    return result


def protected(files: dict[str, str]) -> dict[str, str]:
    return {name: digest for name, digest in files.items() if
            Path(name).name == "pom.xml" or "src/test/" in name or
            ".mvn" in Path(name).parts or Path(name).name.startswith("mvnw")}


@dataclass
class Workspace:
    path: Path
    source: Path
    baseline: dict[str, str]
    lock: RLock = field(default_factory=RLock)


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
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "Source project is not registered or available.")
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
                    raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "Task workspace already exists.")
                self.root.mkdir(parents=True, exist_ok=True)
                # Do not follow links during copy; inspect the copied tree again.
                shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(*IGNORED))
                if inventory(target) != baseline:
                    raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "Source changed while it was copied.")
                record_path = self._record_path(task_id)
                reject_links(record_path)
                record_path.parent.mkdir(exist_ok=True)
                record_path.write_text(json.dumps({"source": str(source), "baseline": baseline}), encoding="utf-8")
                self.records[target] = Workspace(target, source, baseline)
            return target
        except ApplicationError:
            raise
        except (OSError, ValueError) as exc:
            raise ApplicationError(ErrorCode.WORKSPACE_CREATE_FAILED, "Cannot create task workspace.") from exc

    def get(self, workspace: Path) -> Workspace:
        reject_links(workspace)
        path = workspace.resolve()
        if not workspace.is_absolute() or path.parent != self.root or not path.is_dir():
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "Workspace is not a managed task directory.")
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
                    self.records[path] = Workspace(path, source, baseline)
                return self.records[path]
        except ApplicationError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ApplicationError(ErrorCode.INVALID_WORKSPACE, "Workspace ownership record is unavailable.") from exc

    def resolve(self, record: Workspace, value: str) -> Path:
        if not value or "\x00" in value or "\\" in value or ":" in value:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "Use a relative POSIX path inside the workspace.")
        relative = Path(value)
        if relative.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in relative.parts:
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "Path is outside the workspace.")
        # Windows aliases (trailing dots/spaces, device files) are not project files.
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if any(p.rstrip(" .") != p or p.split(".")[0].upper() in reserved for p in relative.parts):
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "Invalid project path.")
        candidate = record.path / relative
        reject_links(candidate)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(record.path):
            raise ApplicationError(ErrorCode.PATH_OUTSIDE_WORKSPACE, "Path is outside the workspace.")
        return resolved

    def verify_tests(self, record: Workspace) -> None:
        if protected(inventory(record.path)) != protected(record.baseline):
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "Benchmark tests and build configuration must remain unchanged.")

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
            try:
                shutil.rmtree(record.path)
                self._record_path(record.path.name).unlink()
                self.records.pop(record.path, None)
            except OSError as exc:
                raise ApplicationError(ErrorCode.WORKSPACE_CLEANUP_FAILED, "Cannot clean task workspace.") from exc
