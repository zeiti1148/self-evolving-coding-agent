"""The runner environment is deliberately narrow; two properties must not regress."""
import os
import sys
from pathlib import Path

from backend.tools.process import run_process

PRINT = 'import os; print(os.environ.get({name!r}, "missing"))'


def test_runner_env_keeps_systemdrive_for_native_toolchains():
    # CMake cannot identify an MSVC toolset without SYSTEMDRIVE; without this the
    # C++ configure step fails and the run is reported as a command error.
    output, metadata, code = run_process(
        [sys.executable, '-c', PRINT.format(name='SYSTEMDRIVE')], Path.cwd(), 30)
    assert code is None
    assert output.strip() == os.environ.get('SYSTEMDRIVE', 'missing')


def test_runner_env_never_passes_model_credentials(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'secret-value')
    output, metadata, code = run_process(
        [sys.executable, '-c', PRINT.format(name='LLM_API_KEY')], Path.cwd(), 30)
    assert code is None
    assert output.strip() == 'missing'


def test_runner_env_cannot_be_overridden_with_other_keys():
    with __import__('pytest').raises(ValueError):
        run_process([sys.executable, '-c', 'pass'], Path.cwd(), 30, extra_env={'LLM_API_KEY': 'x'})
