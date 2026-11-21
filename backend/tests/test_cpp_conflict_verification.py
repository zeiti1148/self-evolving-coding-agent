"""Real CMake/CTest cross-verification for a C++ target; opt-in like Java and Go.

MSBuild's TryCompile fails once the build path passes roughly 120 characters, so
this fixture keeps the workspace root at the drive root with a short name.
"""
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from backend.api.conflict_verification import ConflictVerifier
from backend.api.store import TaskStore
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.projects import ProjectDefinition, RunnerConfig
from backend.shared import AgentResult, TaskCreateRequest, TaskStep, ToolCall
from backend.tests.test_experience_consolidation import record, save, scan
from backend.tools import ToolService, WorkspaceManager

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_CPP_CONFLICT') != '1',
    reason='set RUN_CPP_CONFLICT=1 to run six real CMake/CTest suites')

CPP_PATH = 'counter.cpp'
TARGET = f'{CPP_PATH}#count'
CMAKE = """cmake_minimum_required(VERSION 3.20)
project(counter LANGUAGES CXX)
enable_testing()
add_executable(counter counter.cpp tests/test_count.cpp)
add_test(NAME contract COMMAND counter)
"""
COUNTER = """int count(int x) {{
    {body}
}}
"""
TEST = """#include <cstdio>

int count(int);

int main() {{
    int got = count(1);
    if (got != {expected}) {{
        std::printf("got %d\\n", got);
        return 1;
    }}
    return 0;
}}
"""


@pytest.fixture(autouse=True)
def native_toolchain():
    from backend.tools.native_runners import executable
    for name in ('cmake', 'ctest'):
        try:
            executable(name)
        except Exception:
            pytest.skip(f'{name} is not available')


def short_workspace_root():
    """Drive-root directory with a short name; MSBuild cannot handle deeper paths."""
    for base in (Path('C:/vc'), Path(tempfile.gettempdir())):
        try:
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix='w', dir=base))
        except OSError:
            continue
    raise RuntimeError('no writable short directory for the C++ fixture')


def project(tmp_path):
    short_root = short_workspace_root()
    folders, definitions = {}, {}
    for index, expected in enumerate((1, 2)):
        folder = short_root / f'c{index}'
        (folder / 'tests').mkdir(parents=True)
        (folder / 'CMakeLists.txt').write_text(CMAKE, encoding='utf-8')
        (folder / CPP_PATH).write_text(COUNTER.format(body='return 0;'), encoding='utf-8')
        (folder / 'tests/test_count.cpp').write_text(TEST.format(expected=expected), encoding='utf-8')
        name = f'cpp-project{index}'
        folders[name] = folder
        definitions[name] = ProjectDefinition(id=name, label=name, source=folder, language='cpp',
            repository_id=name, origin='upload', runner_config=RunnerConfig())
    settings = replace(Settings(), workspace_root=short_root / 'ws', database_path=tmp_path / 'app.db',
                       projects=folders, project_definitions=definitions, enable_knowledge_models=False)
    tasks, store = TaskStore(settings.database_path), ExperienceStore(settings.database_path)
    tasks.initialize(); store.initialize()
    manager = WorkspaceManager(settings.workspace_root, folders)
    sources = []
    for index, name in enumerate(folders):
        task = tasks.create(TaskCreateRequest(source_project=name, task_description='Fix the counter contract'))
        tasks.start(task.task_id)
        workspace = manager.create_workspace(folders[name], task.task_id)
        tools = ToolService(manager, settings.maven_home, runner_config=definitions[name].runner_config)
        calls, results = [], []

        def execute(tool, arguments, final=False):
            call = ToolCall(id=str(uuid4()), name=tool, arguments=arguments)
            result = tools.execute_tool(workspace, call)
            if final:
                result.metadata['final_verification'] = True
            calls.append(call); results.append(result)
            tasks.append(task.task_id, TaskStep(sequence=len(calls), call=call, result=result))
            return result

        assert not execute('run_tests', {}).success
        body = f'return {index + 1};'
        assert execute('write_file', {'path': CPP_PATH, 'content': COUNTER.format(body=body)}).success
        final = execute('run_tests', {}, True)
        assert final.success, final.output[-2000:]
        entry = record(repository_id=name, language='cpp', source_task_id=task.task_id,
            issues=[dict(error_type='AssertionError', root_cause='The counter violates the tested contract.',
                         solution='Verified implementation: ' + body, files=[CPP_PATH], symbols=[TARGET])],
            evidence=[dict(source_task_id=task.task_id, tool_call_id=final.call_id, runner='ctest',
                           tests=final.metadata['tests'])])
        source = save(store, entry)
        sources.append(source)
        manager.capture_changes(workspace)
        tasks.finish(task.task_id, AgentResult(task_id=task.task_id, status='succeeded',
            summary='Verified real CTest execution', steps=calls, tool_results=results,
            repair_succeeded=True, tests_passed=True, changed_files=[CPP_PATH],
            experience_saved=True, experience_id=source.id, experience_ids=[source.id]), audit={})
    scan(store)
    return store, sources, ConflictVerifier(settings, tasks, manager), short_root


def payload(sources):
    return [dict(id=source.id, source_task_id=source.source_task_id, language=source.language,
                 repository_id=source.repository_id,
                 evidence=[evidence.model_dump(mode='json') for evidence in source.evidence])
            for source in sources]


def test_cpp_targets_run_real_bidirectional_ctest_verification(tmp_path):
    store, sources, verifier, short_root = project(tmp_path)
    try:
        assert len(sources) == 2
        result = verifier.verify(str(uuid4()), payload(sources), [TARGET], lambda *args, **kwargs: None)
        runs = result['runs']
        assert len(runs) == 6
        outcomes = [run['outcome'] for run in runs]
        assert outcomes.count('passed') == 2
        assert outcomes.count('failed') == 4
        failed = [run for run in runs if run['outcome'] == 'failed']
        assert all(run['failures'] == 1 and run['errors'] == 0 and run['skipped'] == 0 for run in failed)
        assert all(run['reproduces_baseline'] for run in failed)
        assert all(run['tests'] == 1 for run in runs)
    finally:
        shutil.rmtree(short_root, ignore_errors=True)
