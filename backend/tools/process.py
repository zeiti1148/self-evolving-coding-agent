"""Bounded native process execution; no shell strings or batch-file launcher."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from backend.shared import ApplicationError, ErrorCode


class WindowsJob:
    """Kill all descendant processes when the command's lifetime ends on Windows."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise OSError("Cannot attach child process to a Windows Job")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def maven_argv(maven_home: Path, cwd: Path, goals: list[str]) -> list[str]:
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else shutil.which("java")
    jars = list((maven_home / "boot").glob("plexus-classworlds-*.jar"))
    if not java or len(jars) != 1 or not (maven_home / "bin/m2.conf").is_file():
        raise ApplicationError(ErrorCode.COMMAND_FAILED, "Java 或 Maven 不可用，请配置 JAVA_HOME 和 MAVEN_HOME。")
    return [java, "-Dfile.encoding=UTF-8", "-Dmaven.home=" + str(maven_home),
            "-Dclassworlds.conf=" + str(maven_home / "bin/m2.conf"),
            "-Dmaven.multiModuleProjectDirectory=" + str(cwd),
            "-classpath", str(jars[0]), "org.codehaus.plexus.classworlds.launcher.Launcher",
            "-B", "--no-transfer-progress", *goals]


def terminate_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_process(argv: list[str], cwd: Path, timeout: int, limit: int = 50_000) -> tuple[str, dict, ErrorCode | None]:
    started = time.monotonic()
    # Do not pass model credentials or user-provided JVM/Maven command options to projects.
    allowed_env = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "JAVA_HOME", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed_env}
    # Run only the installed pytest core and the registered project's test
    # configuration, regardless of optional plugins in the host environment.
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError as exc:
        raise ApplicationError(ErrorCode.COMMAND_FAILED, "无法启动已配置的可执行程序。") from exc
    job = None
    if os.name == "nt":
        try:
            job = WindowsJob(process)
        except OSError as exc:
            terminate_tree(process)
            process.wait(timeout=10)
            raise ApplicationError(ErrorCode.COMMAND_FAILED, "无法限制命令的子进程范围。") from exc
    chunks: list[bytes] = []
    captured = 0
    total = 0

    def drain() -> None:
        nonlocal captured, total
        assert process.stdout is not None
        while block := process.stdout.read(4096):
            total += len(block)
            remaining = limit * 4 - captured
            if remaining > 0:
                kept = block[:remaining]
                chunks.append(kept)
                captured += len(kept)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if job:
            job.close()
        else:
            terminate_tree(process)
        process.wait(timeout=10)
    finally:
        if job:
            job.close()
        elif os.name != "nt":
            # Also remove children that survived a normally exiting parent.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    reader.join(timeout=10)
    # Maven/Surefire children must not outlive the owning command.
    if reader.is_alive():
        terminate_tree(process)
        reader.join(timeout=2)
        timed_out = True
    output = b"".join(chunks).decode("utf-8", errors="replace")
    metadata = {"exit_code": process.returncode, "duration_ms": round((time.monotonic() - started) * 1000),
                "truncated": total > captured or len(output) > limit}
    code = ErrorCode.COMMAND_TIMEOUT if timed_out else (ErrorCode.COMMAND_FAILED if process.returncode else None)
    return output[:limit], metadata, code
