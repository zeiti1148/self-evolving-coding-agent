"""Synchronous five-tool dispatcher with strict argument validation."""
from __future__ import annotations

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.shared import ApplicationError, ErrorCode, ToolCall, ToolResult
from .process import maven_argv, run_process
from .workspace import WorkspaceManager, inventory, protected, reject_links


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListArgs(Arguments):
    path: str = "."
    recursive: bool = False
    max_entries: int = Field(default=200, ge=1, le=2000)


class ReadArgs(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_chars: int = Field(default=50_000, ge=1, le=200_000)


class WriteArgs(Arguments):
    path: str
    content: str = Field(max_length=200_000)
    create_parent: bool = False


class CommandArgs(Arguments):
    command: list[str] = Field(min_length=1, max_length=8)
    cwd: str = "."
    timeout_seconds: int = Field(default=60, ge=1, le=180)


class TestArgs(Arguments):
    project_path: str = "."
    timeout_seconds: int = Field(default=180, ge=1, le=300)


ARGUMENTS = {"list_files": ListArgs, "read_file": ReadArgs, "write_file": WriteArgs,
             "run_command": CommandArgs, "run_tests": TestArgs}


class ToolService:
    def __init__(self, manager: WorkspaceManager, maven_home: Path):
        self.manager = manager
        self.maven_home = maven_home

    def execute_tool(self, workspace: Path, call: ToolCall) -> ToolResult:
        base = {"call_id": call.id, "tool_name": call.name}
        try:
            record = self.manager.get(workspace)
            if call.name not in ARGUMENTS:
                raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, "Unknown tool name.")
            args = ARGUMENTS[call.name].model_validate(call.arguments)
            with record.lock:
                # Fresh check, including all directories, before a file/process operation.
                inventory(record.path)
                output, metadata, code = getattr(self, "_" + call.name)(record, args)
                metadata.setdefault("truncated", False)
            return ToolResult(**base, success=code is None, output=output, metadata=metadata,
                              error_code=code, error_message=code.value.replace("_", " ").capitalize() if code else None)
        except ValidationError:
            return ToolResult(**base, success=False, error_code=ErrorCode.INVALID_TOOL_CALL,
                              error_message="Invalid tool arguments.")
        except ApplicationError as exc:
            return ToolResult(**base, success=False, error_code=exc.code, error_message=exc.message)
        except FileNotFoundError:
            return ToolResult(**base, success=False, error_code=ErrorCode.FILE_NOT_FOUND, error_message="Project file was not found.")
        except (OSError, UnicodeError):
            return ToolResult(**base, success=False, error_code=ErrorCode.FILE_IO_ERROR, error_message="Cannot access the requested text file.")
        except Exception:
            return ToolResult(**base, success=False, error_code=ErrorCode.INTERNAL_TOOL_ERROR, error_message="Tool execution failed.")

    def _list_files(self, record, args: ListArgs):
        root = self.manager.resolve(record, args.path)
        if not root.is_dir():
            raise ApplicationError(ErrorCode.FILE_NOT_FOUND, "Project directory was not found.")
        entries = []
        for path in sorted(root.rglob("*") if args.recursive else root.iterdir()):
            reject_links(path)
            entries.append(path.relative_to(record.path).as_posix() + ("/" if path.is_dir() else ""))
            if len(entries) > args.max_entries:
                break
        truncated = len(entries) > args.max_entries
        output = "\n".join(entries[:args.max_entries])
        return output[:50_000], {"truncated": truncated or len(output) > 50_000}, None

    def _read_file(self, record, args: ReadArgs):
        if args.end_line is not None and args.end_line < args.start_line:
            raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, "end_line must not precede start_line.")
        path = self.manager.resolve(record, args.path)
        # newline='' preserves CRLF, trailing newlines and code indentation.
        with path.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read(2_000_001)
        if "\x00" in text or len(text) > 2_000_000:
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "Only bounded UTF-8 text files can be read.")
        output = "".join(text.splitlines(keepends=True)[args.start_line - 1:args.end_line])
        return output[:args.max_chars], {"truncated": len(output) > args.max_chars}, None

    def _write_file(self, record, args: WriteArgs):
        path = self.manager.resolve(record, args.path)
        relative = path.relative_to(record.path).as_posix()
        if protected({relative: ""}):
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "Benchmark tests and build configuration are read-only.")
        if args.create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(args.content)
            self.manager.resolve(record, args.path)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return "File written.", {"path": relative, "changed_files": self.manager.changed_files(record.path)}, None

    def _run_command(self, record, args: CommandArgs):
        cwd = self.manager.resolve(record, args.cwd)
        command = args.command
        if command in (["java", "-version"], ["javac", "-version"]):
            executable = shutil.which(command[0])
            if not executable:
                raise ApplicationError(ErrorCode.COMMAND_FAILED, "Java executable was not found.")
            argv = [executable, "-version"]
        elif command in (["mvn", "-version"], ["mvn", "compile"], ["mvn", "test"], ["mvn", "-B", "test"], ["mvn", "-B", "compile"]):
            self.manager.verify_tests(record)
            argv = maven_argv(self.maven_home, cwd, [a for a in command[1:] if a != "-B"])
        else:
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "Command or arguments are not on the allowlist.")
        output, metadata, code = run_process(argv, cwd, args.timeout_seconds)
        metadata["changed_files"] = self.manager.changed_files(record.path)
        return output, metadata, code

    def _run_tests(self, record, args: TestArgs):
        cwd = self.manager.resolve(record, args.project_path)
        self.manager.verify_tests(record)
        if not (cwd / "pom.xml").is_file():
            raise ApplicationError(ErrorCode.COMMAND_FAILED, "No Maven project exists at this path.")
        # Delete only validated in-workspace report directories; never reuse stale reports.
        for reports in cwd.rglob("surefire-reports"):
            if reports.parent.name == "target":
                self.manager.resolve(record, reports.relative_to(record.path).as_posix())
                reject_links(reports)
                shutil.rmtree(reports)
        output, metadata, code = run_process(maven_argv(self.maven_home, cwd, ["clean", "test"]), cwd, args.timeout_seconds)
        self.manager.verify_tests(record)
        metadata["changed_files"] = self.manager.changed_files(record.path)
        if code == ErrorCode.COMMAND_TIMEOUT:
            return output, metadata, code
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        reports = list(cwd.rglob("target/surefire-reports/TEST-*.xml"))
        try:
            for report in reports:
                reject_links(report)
                if report.stat().st_size > 2_000_000:
                    raise ValueError("report too large")
                suite = ET.fromstring(report.read_bytes())
                if suite.tag != "testsuite":
                    raise ValueError("invalid test report")
                for key in counts:
                    value = int(suite.attrib.get(key, "0"))
                    if value < 0:
                        raise ValueError("negative test count")
                    counts[key] += value
        except (ET.ParseError, ValueError):
            return output, metadata, ErrorCode.COMMAND_FAILED
        metadata.update(counts)
        if counts["tests"] <= 0 or counts["skipped"] > 0:
            return output, metadata, ErrorCode.COMMAND_FAILED
        if counts["failures"] + counts["errors"] > 0:
            return output, metadata, ErrorCode.TEST_FAILED
        return output, metadata, code
