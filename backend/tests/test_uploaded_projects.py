import io
import json
import stat
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.project_uploads import extract_project
from backend.config import Settings
from backend.projects import RunnerConfig
from backend.shared import ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.native_runners import executable, go_verdict
from backend.shared import ErrorCode


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    return stream.getvalue()


def configured(tmp_path):
    return replace(Settings(), projects={}, project_definitions={}, database_path=tmp_path/'app.db', workspace_root=tmp_path/'workspaces')


def python_files(name='app', default=0):
    return {'project/pyproject.toml': '[tool.pytest.ini_options]\npythonpath=["."]\n',
            f'project/{name}.py': f'def count(value):\n    return int(value)\n',
            'project/tests/test_count.py': f'from {name} import count\ndef test_none(): assert count(None) == {default}\ndef test_number(): assert count("4") == 4\n'}


def test_upload_persists_and_config_is_immutable(tmp_path):
    settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/projects/upload?label=My%20Project', content=archive(python_files()), headers={'Content-Type':'application/zip'})
        assert response.status_code == 201, response.text
        project = response.json()
        assert project['language'] == 'python' and project['origin'] == 'upload' and project['file_count'] == 3
        changed = client.post(f"/api/projects/{project['id']}/configure", json={'runner_config':{'test_targets':['tests/test_count.py']}})
        assert changed.status_code == 201 and changed.json()['id'] != project['id']
        assert settings.project(project['id']).runner_config.test_targets == []
    with TestClient(create_app(configured(tmp_path))) as client:
        assert {p['id'] for p in client.get('/api/projects').json()} == {project['id'], changed.json()['id']}


@pytest.mark.parametrize('path',['../outside.txt','root/../../outside','C:/outside','root/x:secret','root/CON.txt','root/a.','root/../x','root\\x'])
def test_zip_traversal_and_windows_aliases_are_rejected(tmp_path, path):
    root = tmp_path/'upload'; root.mkdir()
    with pytest.raises(ValueError):
        extract_project(io.BytesIO(archive({path:'bad', **python_files()})), root)
    assert not (tmp_path/'outside.txt').exists()


def test_zip_symlinks_and_case_collisions_rejected(tmp_path):
    root = tmp_path/'upload'; root.mkdir()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as zipped:
        link = zipfile.ZipInfo('project/link'); link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, '../outside')
    stream.seek(0)
    with pytest.raises(ValueError): extract_project(stream, root)
    with pytest.raises(ValueError): extract_project(io.BytesIO(archive({'x.py':'a','X.py':'b'})), root)


def test_upload_preserves_test_target_fixture_excludes_secrets_and_dependencies(tmp_path):
    root = tmp_path/'upload'; root.mkdir()
    files = python_files() | {'project/tests/roots/target/page.txt':'fixture', 'project/.env':'SECRET',
                             'project/node_modules/package/index.js':'excluded'}
    source, *_ = extract_project(io.BytesIO(archive(files)), root)
    manager = WorkspaceManager(tmp_path/'workspaces', {'uploaded':source})
    copied = manager.create_workspace(source, str(uuid4()))
    assert (copied/'tests/roots/target/page.txt').read_text() == 'fixture'
    assert not (copied/'.env').exists() and not (copied/'node_modules').exists()


def test_test_selectors_cannot_inject_flags_or_escape():
    for target in ['--override-ini=addopts=', '../elsewhere', 'C:/x', 'tests\\x']:
        with pytest.raises(ValueError): RunnerConfig(test_targets=[target])


def test_custom_named_acceptance_file_is_read_only(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    (source/'pyproject.toml').write_text('[project]\nname="demo"\nversion="1.0"\n')
    (source/'acceptance.py').write_text('def test_ok(): assert True\n')
    manager = WorkspaceManager(tmp_path/'workspaces', {'one':source})
    workspace = manager.create_workspace(source, str(uuid4()))
    tools = ToolService(manager, tmp_path/'maven', runner_config=RunnerConfig(test_targets=['acceptance.py']))
    result = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name='write_file', arguments={'path':'acceptance.py','content':'skip'}))
    assert not result.success


def test_upload_manifest_cannot_forge_a_different_repository(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        base = python_files()
        a = client.post('/api/projects/upload',content=archive(base | {'project/.agent-upload.json':json.dumps({'task_description':'one'})})).json()
        b = client.post('/api/projects/upload',content=archive(base | {'project/.agent-upload.json':json.dumps({'task_description':'two'})})).json()
        assert a['repository_id'] == b['repository_id']
        assert a['source_digest'] != b['source_digest']


@pytest.mark.parametrize('language,runner',[('go','go'),('cpp','ctest')])
def test_native_experience_api_accepts_verified_rules(tmp_path, language, runner):
    from backend.tests.test_experience_consolidation import record
    from backend.shared import ExperienceCreate
    app = create_app(configured(tmp_path))
    value = record().model_dump()
    value['language'] = language
    value['evidence'][0]['runner'] = runner
    with TestClient(app) as client:
        saved = app.state.experiences.save_experience(ExperienceCreate(**value))
        assert client.get('/api/experiences?language='+language).status_code == 200
        assert client.get('/api/experiences/jobs?language='+language).status_code == 200
        assert client.get('/api/experiences/'+saved.id+'/maintenance').status_code == 200


@pytest.mark.parametrize('language',['go','cpp'])
def test_real_native_runner_failure_then_fix(tmp_path, language):
    source = tmp_path/'source'; source.mkdir()
    if language == 'go':
        executable('go')
        files = {'go.mod':'module example.org/counter\n\ngo 1.20\n', 'counter.go':'package counter\nfunc Count(x int) int { return x }\n',
                 'counter_test.go':'package counter\nimport "testing"\nfunc TestNegative(t *testing.T) { if Count(-2)!=0 {t.Fatal("negative count")} }\nfunc TestPositive(t *testing.T) { if Count(3)!=3 {t.Fatal("positive count")} }\n'}
        file, fixed = 'counter.go', 'package counter\nfunc Count(x int) int { if x<0 { return 0 }; return x }\n'
    else:
        executable('cmake'); executable('ctest')
        files = {'CMakeLists.txt':'cmake_minimum_required(VERSION 3.20)\nproject(counter LANGUAGES CXX)\nenable_testing()\nadd_executable(counter counter.cpp tests/test_count.cpp)\nadd_test(NAME negative COMMAND counter -2)\nadd_test(NAME positive COMMAND counter 3)\n',
                 'counter.cpp':'int count(int x) { return x; }\n',
                 'tests/test_count.cpp':'#include <cstdlib>\nint count(int); int main(int argc,char**argv){int x=std::atoi(argv[1]); return count(x)==(x<0?0:x)?0:1;}\n'}
        file, fixed = 'counter.cpp', 'int count(int x) { return x < 0 ? 0 : x; }\n'
    for name, content in files.items():
        path = source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    manager = WorkspaceManager(tmp_path/'workspaces', {'project':source})
    workspace = manager.create_workspace(source, str(uuid4()))
    tools = ToolService(manager, tmp_path/'maven', runner_config=RunnerConfig())
    def invoke(name, **args): return tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name=name, arguments=args))
    before = invoke('run_tests')
    assert before.error_code == ErrorCode.TEST_FAILED, before.model_dump()
    assert before.metadata['tests'] == 2 and before.metadata['failures'] == 1
    assert not invoke('write_file', path='counter_test.go' if language=='go' else 'CMakeLists.txt', content='skip tests').success
    assert invoke('write_file', path=file, content=fixed).success
    after = invoke('run_tests')
    assert after.success and after.metadata['tests'] == 2 and after.metadata['failures'] == 0, after.model_dump()
    assert (source/file).read_text() != fixed


def test_go_no_tests_build_failure_or_truncated_json_cannot_pass():
    for text, meta in [('{}',{}), ('{"Action":"fail","Package":"build"}',{}), ('{"Test":"T","Action":"pass"}',{'truncated':True})]:
        assert go_verdict(text, meta, None)[2] == ErrorCode.COMMAND_FAILED


class ScriptedRepairModel:
    """Deterministic repair responses; tests the regular uploaded-task workflow."""
    def __init__(self): self.decisions = 0
    def complete(self, messages, *, tools=None):
        if tools:
            self.decisions += 1
            if self.decisions > 1: return {'role':'assistant','content':'修复已完成，请验收。'}
            return {'role':'assistant','content':None,'tool_calls':[{'id':'write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'app.py','content':'def count(value):\n    return 0 if value is None else int(value)\n'})}}]}
        return {'content':json.dumps({'experiences':[dict(task_summary='Handle optional counts',root_cause='Numeric conversion cannot accept missing values.',solution='Check missing input before conversion and return the project default.',applicability=['Optional count input'],constraints=['Keep numeric string conversion unchanged.'],tags=['python'],issues=[dict(error_type='TypeError',root_cause='Numeric conversion cannot accept missing values.',solution='Check missing input before conversion and return the project default.',files=['app.py'])])]})}
    def close(self): pass


def test_uploaded_project_runs_as_regular_task_and_preserves_evidence(tmp_path):
    with TestClient(create_app(configured(tmp_path), llm_factory=ScriptedRepairModel)) as client:
        uploaded = client.post('/api/projects/upload', content=archive(python_files())).json()
        created = client.post('/api/tasks', json={
            'source_project': uploaded['id'], 'task_description': 'Fix optional count handling', 'max_steps': 3})
        assert created.status_code == 202, created.text
        identifier = created.json()['task_id']
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            response = client.get('/api/tasks/' + identifier)
            assert response.status_code == 200, response.text
            task = response.json()
            if task['status'] not in {'pending', 'running'}: break
            time.sleep(.1)
        assert task['status'] == 'succeeded', task
        assert task['result']['experience_saved'] is True
        experience_id = task['result']['experience_id']
        assert client.get('/api/experiences/' + experience_id).status_code == 200
        evidence = client.get('/api/tasks/' + identifier + '/evidence').json()
        assert evidence['tests']['before']['failures'] == 1
        assert evidence['tests']['after']['failures'] == 0
        assert evidence['tests']['final_verified'] is True
        assert evidence['tests']['original_tests_unchanged'] is True
    with TestClient(create_app(configured(tmp_path))) as client:
        assert client.get('/api/tasks/' + identifier).json()['status'] == 'succeeded'
        assert client.get('/api/experiences/' + experience_id).status_code == 200
