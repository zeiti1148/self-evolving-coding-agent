import json
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.store import TaskStore
from backend.config import Settings
from backend.shared import AgentResult, TaskCreateRequest, TaskStep, ToolCall, ToolResult


def fixture_app(tmp_path, factory):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n')
    (source / 'value.py').write_text('def value():\n    return None\n')
    settings = replace(Settings(), workspace_root=tmp_path / 'workspaces', database_path=tmp_path / 'db.sqlite',
                       projects={'sample': source}, llm_timeout=2)
    return create_app(settings, llm_factory=factory), settings


def seed(app, *, final=True, passed=True):
    tasks = app.state.tasks
    task_id = tasks.create(TaskCreateRequest(task_description='Fix missing value', source_project='sample')).task_id
    tasks.start(task_id)
    manager = app.state.tools.manager
    workspace = manager.create_workspace(app.state.settings.projects['sample'], task_id)
    (workspace / 'value.py').write_text('def value():\n    return 1\n')
    call = ToolCall(id=str(uuid4()), name='run_tests', arguments={})
    result = ToolResult(call_id=call.id, tool_name='run_tests', success=True,
                        metadata={'runner':'pytest', 'tests':1, 'failures':0, 'errors':0, 'skipped':0, 'final_verification':final})
    tasks.append(task_id, TaskStep(sequence=1, call=call, result=result))
    tasks.finish(task_id, AgentResult(task_id=task_id, status='stopped', summary='Budget used',
        steps=[call], tool_results=[result], repair_succeeded=passed, tests_passed=passed,
        changed_files=['value.py'], error_message='决策次数已用完。'), audit={'decision_rounds':70})
    manager.capture_changes(workspace)
    return task_id, workspace


def wait_done(client, task_id):
    for _ in range(300):
        state = client.get(f'/api/tasks/{task_id}/experience-retry').json()
        if state['state'] != 'running':
            return state
        time.sleep(.01)
    raise AssertionError('retry did not finish')


class Extractor:
    def __init__(self, release=None):
        self.calls = 0
        self.started = threading.Event()
        self.release = release
    def complete(self, messages, **kwargs):
        self.calls += 1
        self.started.set()
        if self.release:
            assert self.release.wait(5)
        return {'role':'assistant', 'content':json.dumps({'experiences':[{
            'task_summary':'Handle missing value', 'root_cause':'Returns None', 'solution':'Return expected value',
            'error_type':'AssertionError', 'applicability':['Missing value'], 'constraints':['Preserve expected output'],
            'issues':[{'error_type':'AssertionError', 'root_cause':'Returns None', 'solution':'Return expected value', 'files':['value.py']}]
        }]})}


def test_retry_preserves_stopped_trace_and_deduplicates_paid_calls(tmp_path):
    release = threading.Event()
    llm = Extractor(release)
    app, _ = fixture_app(tmp_path, lambda: llm)
    with TestClient(app) as client:
        task_id, _ = seed(app)
        before = client.get(f'/api/tasks/{task_id}').json()
        endpoint = f'/api/tasks/{task_id}/experience-retry'
        assert client.post(endpoint).status_code == 202
        assert llm.started.wait(2)
        try:
            assert client.post(endpoint).json()['state'] == 'running'
            assert llm.calls == 1
        finally:
            release.set()
        assert wait_done(client, task_id)['state'] == 'succeeded'
        after = client.get(f'/api/tasks/{task_id}').json()
        assert after['status'] == 'stopped' and after['error_message'] == before['error_message']
        assert after['finished_at'] == before['finished_at'] and after['steps_completed'] == before['steps_completed']
        assert after['result']['steps'] == before['result']['steps']
        assert after['result']['tool_results'] == before['result']['tool_results']
        assert after['result']['experience_saved'] is True
        assert client.post(endpoint).json()['state'] == 'succeeded' and llm.calls == 1
        feed = client.get('/api/experiences/automation').json()
        assert feed['items'][0]['source']['source_task_id'] == task_id


@pytest.mark.parametrize('problem', ['changed_source','changed_test','missing_snapshot','not_final','failed'])
def test_retry_rejects_stale_or_unverified_evidence_without_model_call(tmp_path, problem):
    llm = Extractor()
    app, _ = fixture_app(tmp_path, lambda: llm)
    with TestClient(app) as client:
        task_id, workspace = seed(app, final=problem!='not_final', passed=problem!='failed')
        if problem == 'changed_source':
            (workspace / 'value.py').write_text('def value(): return 2\n')
        if problem == 'changed_test':
            (workspace / 'test_value.py').write_text('def test_value(): pass\n')
        if problem == 'missing_snapshot':
            (workspace.parent / '.records' / (task_id + '.changes.json')).unlink()
        assert client.post(f'/api/tasks/{task_id}/experience-retry').status_code == 409
        assert llm.calls == 0


def test_restart_recovers_retry_without_automatic_paid_replay(tmp_path):
    app, settings = fixture_app(tmp_path, Extractor)
    with TestClient(app):
        task_id, _ = seed(app)
        app.state.tasks.begin_experience_retry(task_id)
    recovered = TaskStore(settings.database_path)
    recovered.initialize()
    audit = recovered.get_audit(task_id)
    assert audit['experience_retry']['state'] == 'failed'
    assert '重启' in audit['experience_retry']['notice']
    assert audit['experience_retry_history'][-1]['state'] == 'failed'
    recovered.close()


def test_mid_request_source_change_prevents_saving(tmp_path):
    release = threading.Event()
    llm = Extractor(release)
    app, _ = fixture_app(tmp_path, lambda: llm)
    with TestClient(app) as client:
        task_id, workspace = seed(app)
        assert client.post(f'/api/tasks/{task_id}/experience-retry').status_code == 202
        assert llm.started.wait(2)
        (workspace / 'value.py').write_text('def value(): return 2\n')
        release.set()
        assert wait_done(client, task_id)['state'] == 'failed'
        assert not app.state.experiences.list_experiences()
