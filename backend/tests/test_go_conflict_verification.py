"""Real Go cross-verification for a Go target; opt-in like the Java variant."""
import os
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
    os.environ.get('RUN_GO_CONFLICT') != '1',
    reason='set RUN_GO_CONFLICT=1 to run six real go test suites')

GO_PATH = 'counter.go'
TARGET = f'{GO_PATH}#Count'
GO_MOD = 'module demo\n\ngo 1.20\n'
COUNTER = """package demo

func Count(value int) int {{
	{body}
}}
"""
TEST = """package demo

import "testing"

func TestContract(t *testing.T) {{
	if got := Count(1); got != {expected} {{
		t.Fatalf("got %d", got)
	}}
}}
"""


@pytest.fixture(autouse=True)
def go_available():
    import shutil
    if not shutil.which('go'):
        pytest.skip('go toolchain is not on PATH')


def project(tmp_path):
    """Two independent repairs of the same Go function with opposite behaviour."""
    folders, definitions = {}, {}
    for index, expected in enumerate((1, 2)):
        folder = tmp_path / f'go{index}'
        folder.mkdir()
        (folder / 'go.mod').write_text(GO_MOD, encoding='utf-8')
        (folder / GO_PATH).write_text(COUNTER.format(body='return 0'), encoding='utf-8')
        (folder / 'counter_test.go').write_text(TEST.format(expected=expected), encoding='utf-8')
        name = f'go-project{index}'
        folders[name] = folder
        definitions[name] = ProjectDefinition(id=name, label=name, source=folder, language='go',
            repository_id=name, origin='upload', runner_config=RunnerConfig())
    settings = replace(Settings(), workspace_root=tmp_path / 'workspaces', database_path=tmp_path / 'app.db',
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
        body = f'return {index + 1}'
        assert execute('write_file', {'path': GO_PATH, 'content': COUNTER.format(body=body)}).success
        final = execute('run_tests', {}, True)
        assert final.success, final.output[-2000:]
        entry = record(repository_id=name, language='go', source_task_id=task.task_id,
            issues=[dict(error_type='AssertionError', root_cause='The counter violates the tested contract.',
                         solution='Verified implementation: ' + body, files=[GO_PATH], symbols=[TARGET])],
            evidence=[dict(source_task_id=task.task_id, tool_call_id=final.call_id, runner='go',
                           tests=final.metadata['tests'])])
        source = save(store, entry)
        sources.append(source)
        manager.capture_changes(workspace)
        tasks.finish(task.task_id, AgentResult(task_id=task.task_id, status='succeeded',
            summary='Verified real go test execution', steps=calls, tool_results=results,
            repair_succeeded=True, tests_passed=True, changed_files=[GO_PATH],
            experience_saved=True, experience_id=source.id, experience_ids=[source.id]), audit={})
    scan(store)
    return store, sources, ConflictVerifier(settings, tasks, manager)


def payload(sources):
    return [dict(id=source.id, source_task_id=source.source_task_id, language=source.language,
                 repository_id=source.repository_id,
                 evidence=[evidence.model_dump(mode='json') for evidence in source.evidence])
            for source in sources]


def test_go_targets_run_real_bidirectional_go_verification(tmp_path):
    store, sources, verifier = project(tmp_path)
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
