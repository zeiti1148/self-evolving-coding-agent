"""Synchronous five-tool dispatcher with strict argument validation."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.shared import ApplicationError, ErrorCode, ToolCall, ToolResult
from .process import maven_argv, run_process
from .workspace import WorkspaceManager, inventory, protected, reject_links, sensitive_path


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
RESULT_ERRORS = {ErrorCode.COMMAND_TIMEOUT: "命令执行超时。", ErrorCode.COMMAND_FAILED: "命令执行失败，请查看原始输出。",
                 ErrorCode.OUTPUT_LIMIT_EXCEEDED: "命令输出超过限制。", ErrorCode.TEST_FAILED: "测试未通过，请查看失败详情。"}


class ToolService:
    def __init__(self, manager: WorkspaceManager, maven_home: Path, *, python_executable: str | Path | None = None):
        self.manager = manager
        # Child processes run in their workspaces, so classpaths must not depend
        # on the caller's current directory or an unexpanded relative .env path.
        self.maven_home = maven_home.resolve()
        self.python_executable = str(python_executable or sys.executable)

    def execute_tool(self, workspace: Path, call: ToolCall) -> ToolResult:
        base = {"call_id": call.id, "tool_name": call.name}
        try:
            record = self.manager.get(workspace)
            if call.name not in ARGUMENTS:
                raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, "无法识别该工具。")
            args = ARGUMENTS[call.name].model_validate(call.arguments)
            with record.lock:
                # Fresh check, including all directories, before a file/process operation.
                inventory(record.path)
                output, metadata, code = getattr(self, "_" + call.name)(record, args)
                metadata.setdefault("truncated", False)
            return ToolResult(**base, success=code is None, output=output, metadata=metadata,
                              error_code=code, error_message=RESULT_ERRORS.get(code, "工具执行失败。") if code else None)
        except ValidationError:
            return ToolResult(**base, success=False, error_code=ErrorCode.INVALID_TOOL_CALL,
                              error_message="工具参数无效。")
        except ApplicationError as exc:
            return ToolResult(**base, success=False, error_code=exc.code, error_message=exc.message)
        except FileNotFoundError:
            return ToolResult(**base, success=False, error_code=ErrorCode.FILE_NOT_FOUND, error_message="未找到项目文件。")
        except (OSError, UnicodeError):
            return ToolResult(**base, success=False, error_code=ErrorCode.FILE_IO_ERROR, error_message="无法访问指定的文本文件。")
        except Exception:
            return ToolResult(**base, success=False, error_code=ErrorCode.INTERNAL_TOOL_ERROR, error_message="工具执行失败。")

    def _list_files(self, record, args: ListArgs):
        root = self.manager.resolve(record, args.path)
        if not root.is_dir():
            raise ApplicationError(ErrorCode.FILE_NOT_FOUND, "未找到项目目录。")
        entries = []
        for path in sorted(root.rglob("*") if args.recursive else root.iterdir()):
            reject_links(path)
            if sensitive_path(path.relative_to(record.path)):
                continue
            entries.append(path.relative_to(record.path).as_posix() + ("/" if path.is_dir() else ""))
            if len(entries) > args.max_entries:
                break
        truncated = len(entries) > args.max_entries
        output = "\n".join(entries[:args.max_entries])
        return output[:50_000], {"truncated": truncated or len(output) > 50_000}, None

    def _read_file(self, record, args: ReadArgs):
        if args.end_line is not None and args.end_line < args.start_line:
            raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, "结束行号不能小于起始行号。")
        path = self.manager.resolve(record, args.path)
        if sensitive_path(path.relative_to(record.path)):
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "文件工具无权读取本地凭据。")
        # newline='' preserves CRLF, trailing newlines and code indentation.
        with path.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read(2_000_001)
        if "\x00" in text or len(text) > 2_000_000:
            raise ApplicationError(ErrorCode.FILE_IO_ERROR, "只能读取大小在限制范围内的 UTF-8 文本文件。")
        output = "".join(text.splitlines(keepends=True)[args.start_line - 1:args.end_line])
        return output[:args.max_chars], {"truncated": len(output) > args.max_chars}, None

    def _write_file(self, record, args: WriteArgs):
        path = self.manager.resolve(record, args.path)
        relative = path.relative_to(record.path).as_posix()
        if protected({relative: ""}):
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "样例测试和构建配置为只读。")
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
        return "文件已写入。", {"path": relative, "changed_files": self.manager.changed_files(record.path)}, None

    def _run_command(self, record, args: CommandArgs):
        cwd = self.manager.resolve(record, args.cwd)
        command = args.command
        if command in (["java", "-version"], ["javac", "-version"]):
            executable = shutil.which(command[0])
            if not executable and os.environ.get("JAVA_HOME"):
                executable = str(Path(os.environ["JAVA_HOME"]) / "bin" / (command[0] + (".exe" if os.name == "nt" else "")))
            if not executable:
                raise ApplicationError(ErrorCode.COMMAND_FAILED, "未找到 Java 可执行程序。")
            argv = [executable, "-version"]
        elif command in (["mvn", "-version"], ["mvn", "compile"], ["mvn", "test"], ["mvn", "-B", "test"], ["mvn", "-B", "compile"]):
            self.manager.verify_tests(record)
            argv = maven_argv(self.maven_home, cwd, [a for a in command[1:] if a != "-B"])
        elif command in (["python", "--version"], ["python", "-V"]):
            argv = [self.python_executable, "-I", "--version"]
        else:
            raise ApplicationError(ErrorCode.COMMAND_NOT_ALLOWED, "该命令或参数不在允许列表中。")
        output, metadata, code = run_process(argv, cwd, args.timeout_seconds)
        metadata["changed_files"] = self.manager.changed_files(record.path)
        return output, metadata, code

    def _run_tests(self, record, args: TestArgs):
        cwd = self.manager.resolve(record, args.project_path)
        self.manager.verify_tests(record)
        relative = cwd.relative_to(record.path)
        prefix = relative.as_posix() + "/" if relative != Path(".") else ""
        java = prefix + "pom.xml" in record.baseline
        python = any(prefix + marker in record.baseline for marker in ("pyproject.toml", "pytest.ini", "setup.cfg", ".agent-project.json"))
        if java and python:
            raise ApplicationError(ErrorCode.COMMAND_FAILED, "无法确定测试工具，请登记仅包含一种编程语言的项目根目录。")
        if python:
            return self._run_python_tests(record, cwd, args.timeout_seconds)
        if not java:
            raise ApplicationError(ErrorCode.COMMAND_FAILED, "此路径下没有已登记的 Maven 或 Python 项目。")
        return self._run_maven_tests(record, cwd, args.timeout_seconds)

    def _run_maven_tests(self, record, cwd: Path, timeout_seconds: int):
        # Delete only validated in-workspace report directories; never reuse stale reports.
        for reports in cwd.rglob("surefire-reports"):
            if reports.parent.name == "target":
                self.manager.resolve(record, reports.relative_to(record.path).as_posix())
                reject_links(reports)
                shutil.rmtree(reports)
        output, metadata, code = run_process(maven_argv(self.maven_home, cwd, ["clean", "test"]), cwd, timeout_seconds)
        self.manager.verify_tests(record)
        metadata["changed_files"] = self.manager.changed_files(record.path)
        metadata["runner"] = "maven"
        if code == ErrorCode.COMMAND_TIMEOUT:
            return output, metadata, code
        reports = list(cwd.rglob("target/surefire-reports/TEST-*.xml"))
        return self._test_verdict(output, metadata, code, reports, "maven")

    def _run_python_tests(self, record, cwd: Path, timeout_seconds: int):
        # A new report outside the model-writable copy cannot be reused or forged
        # through write_file. Each invocation has its own filename.
        report_dir = self.manager.root / ".test-reports" / record.path.name
        reject_links(report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        report = report_dir / (str(uuid4()) + ".xml")
        argv = [self.python_executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "-o", "addopts=", "-o", "python_files=test_*.py *_test.py", "--junitxml=" + str(report)]
        output, metadata, code = run_process(argv, cwd, timeout_seconds)
        self.manager.verify_tests(record)
        metadata.update(changed_files=self.manager.changed_files(record.path), runner="pytest")
        if code == ErrorCode.COMMAND_TIMEOUT:
            return output, metadata, code
        return self._test_verdict(output, metadata, code, [report] if report.is_file() else [], "pytest")

    @staticmethod
    def _test_verdict(output: str, metadata: dict, code, reports: list[Path], runner: str):
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        failures: list[dict[str, str]] = []
        error_types: set[str] = set()
        try:
            for report in reports:
                reject_links(report)
                if report.stat().st_size > 2_000_000:
                    raise ValueError("report too large")
                root = ET.fromstring(report.read_bytes())
                suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite")) if root.tag == "testsuites" else []
                if not suites:
                    raise ValueError("invalid test report")
                for suite in suites:
                    for key in counts:
                        value = int(suite.attrib.get(key, "0"))
                        if value < 0:
                            raise ValueError("negative test count")
                        counts[key] += value
                    for case in suite.findall("testcase"):
                        for problem in [*case.findall("failure"), *case.findall("error")]:
                            error_type = problem.get("type", "").rsplit(".", 1)[-1]
                            description = (problem.get("message", "") + "\n" + (problem.text or "")).strip()
                            found = re.findall(r"\b[A-Za-z_][A-Za-z_0-9]*(?:Exception|Error)\b", description)
                            if not error_type or error_type in {"pytest.skip", "pytest.fail"}:
                                error_type = found[-1] if found else "AssertionError"
                            if error_type:
                                error_types.add(error_type)
                            error_types.update(found)
                            if len(failures) < 20:
                                failures.append({"test": (case.get("classname", "") + "." + case.get("name", ""))[:300],
                                                 "error_type": error_type[:200], "message": description[:1500]})
        except (ET.ParseError, ValueError):
            return output, metadata, ErrorCode.COMMAND_FAILED
        metadata.update(counts)
        metadata.update(error_types=sorted(error_types)[:20], test_failures=failures)
        if counts["tests"] <= 0 or counts["skipped"] > 0:
            return output, metadata, ErrorCode.COMMAND_FAILED
        # pytest exit codes 2/3/4/5 report collection, internal, usage or no-test
        # faults. A synthetic JUnit error must not mask a broken environment.
        if runner == "pytest" and metadata.get("exit_code") not in {0, 1}:
            return output, metadata, ErrorCode.COMMAND_FAILED
        if counts["failures"] + counts["errors"] > 0:
            return output, metadata, ErrorCode.TEST_FAILED
        return output, metadata, code
