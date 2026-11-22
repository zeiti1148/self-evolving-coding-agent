"""Real Maven cross-verification for a Java target; opt-in because it runs six suites.

Set RUN_MAVEN_CONFLICT=1 to execute. The fixture reuses the benchmark project's
build configuration so the dependency set is the one this machine has cached.
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
from backend.config import ROOT, Settings
from backend.experience import ExperienceStore
from backend.projects import ProjectDefinition, RunnerConfig
from backend.shared import AgentResult, TaskCreateRequest, TaskStep, ToolCall
from backend.tests.test_experience_consolidation import record, save, scan
from backend.tools import ToolService, WorkspaceManager

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_MAVEN_CONFLICT') != '1',
    reason='set RUN_MAVEN_CONFLICT=1 to run six real Maven cross-verification suites')

JAVA_PATH = 'src/main/java/demo/Converter.java'
TARGET = f'{JAVA_PATH}#Converter.convert'
POM = (ROOT / 'benchmarks' / 'java' / 'pom.xml').read_text(encoding='utf-8')
CONVERTER = """package demo;

public class Converter {{
    public static int convert(int value) {{
        {body}
    }}
}}
"""
TEST = """package demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

public class ConverterTest {{
    @Test
    void keepsTheContractValue() {{
        assertEquals({expected}, Converter.convert(1));
    }}
}}
"""


def jdk():
    base = ROOT / '.cache' / 'jdk17'
    candidates = [path for path in base.glob('jdk-*') if (path / 'bin' / 'javac.exe').exists()]
    return candidates[0] if candidates else None


@pytest.fixture(autouse=True)
def java_home(monkeypatch):
    path = jdk()
    if path is None:
        pytest.skip('JDK 17 under .cache/jdk17 is missing')
    monkeypatch.setenv('JAVA_HOME', str(path))


def project(tmp_path):
    """Two independent repairs of the same Java method with genuinely different behaviour."""
    # Maven writes deep surefire paths; pytest's tmp_path plus task UUIDs exceeds
    # the Windows MAX_PATH budget, so give the workspaces a short parent.
    short_root = Path(tempfile.mkdtemp(prefix='jvw-', dir=tempfile.gettempdir()))
    folders, definitions = {}, {}
    for index, expected in enumerate((1, 2)):
        folder = tmp_path / f'java{index}'
        (folder / 'src/main/java/demo').mkdir(parents=True)
        (folder / 'src/test/java/demo').mkdir(parents=True)
        (folder / 'pom.xml').write_text(POM, encoding='utf-8')
        (folder / JAVA_PATH).write_text(CONVERTER.format(body='return 0;'), encoding='utf-8')
        (folder / 'src/test/java/demo/ConverterTest.java').write_text(TEST.format(expected=expected), encoding='utf-8')
        name = f'java-project{index}'
        folders[name] = folder
        definitions[name] = ProjectDefinition(id=name, label=name, source=folder, language='java',
            repository_id=name, origin='upload', runner_config=RunnerConfig())
    settings = replace(Settings(), workspace_root=short_root / 'workspaces', database_path=tmp_path / 'app.db',
                       projects=folders, project_definitions=definitions, enable_knowledge_models=False)
    tasks, store = TaskStore(settings.database_path), ExperienceStore(settings.database_path)
    tasks.initialize(); store.initialize()
    manager = WorkspaceManager(settings.workspace_root, folders)
    sources = []
    for index, name in enumerate(folders):
        task = tasks.create(TaskCreateRequest(source_project=name, task_description='Fix the converter contract'))
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
        assert execute('write_file', {'path': JAVA_PATH, 'content': CONVERTER.format(body=body)}).success
        final = execute('run_tests', {}, True)
        assert final.success, final.output[-2000:]
        entry = record(repository_id=name, language='java', source_task_id=task.task_id,
            issues=[dict(error_type='AssertionError', root_cause='The converter violates the tested contract.',
                         solution='Verified implementation: ' + body, files=[JAVA_PATH], symbols=[TARGET])],
            evidence=[dict(source_task_id=task.task_id, tool_call_id=final.call_id, runner='maven',
                           tests=final.metadata['tests'])])
        source = save(store, entry)
        sources.append(source)
        manager.capture_changes(workspace)
        tasks.finish(task.task_id, AgentResult(task_id=task.task_id, status='succeeded',
            summary='Verified real Maven execution', steps=calls, tool_results=results,
            repair_succeeded=True, tests_passed=True, changed_files=[JAVA_PATH],
            experience_saved=True, experience_id=source.id, experience_ids=[source.id]), audit={})
    scan(store)
    return store, sources, ConflictVerifier(settings, tasks, manager), short_root


def payload(sources):
    return [dict(id=source.id, source_task_id=source.source_task_id, language=source.language,
                 repository_id=source.repository_id,
                 evidence=[evidence.model_dump(mode='json') for evidence in source.evidence])
            for source in sources]


def test_java_targets_run_real_bidirectional_maven_verification(tmp_path):
    store, sources, verifier, short_root = project(tmp_path)
    assert len(sources) == 2
    result = verifier.verify(str(uuid4()), payload(sources), [TARGET], lambda *args, **kwargs: None)
    assert result['targets'] == [TARGET]
    runs = result['runs']
    # Two self-checks and four cross-checks, all executed by Maven.
    assert len(runs) == 6
    outcomes = [run['outcome'] for run in runs]
    assert outcomes.count('passed') == 2
    assert outcomes.count('failed') == 4
    failed = [run for run in runs if run['outcome'] == 'failed']
    # The exchanged implementation reproduces the source's original failure, not a build error.
    assert all(run['failures'] == 1 and run['errors'] == 0 and run['skipped'] == 0 for run in failed)
    assert all(run['reproduces_baseline'] for run in failed)
    assert all(run['tests'] == 1 for run in runs)
    # The workspace copies live in a short directory, so clean them up explicitly.
    shutil.rmtree(short_root, ignore_errors=True)
