"""Isolated controlled extraction/detection; the six verifier runs execute real pytest."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from backend.api.conflict_verification import ConflictVerifier
from backend.experience.transplant import transplant
from backend.api.store import TaskStore
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.automatic_resolution import decide
from backend.experience.automation import AutomaticMergeWorker, feed
from backend.experience.database import connect
from backend.projects import ProjectDefinition, RunnerConfig
from backend.shared import AgentResult, ConflictResolution, TaskCreateRequest, TaskStep, ToolCall
from backend.tests.test_cross_upload_conflicts import ConflictModel, prepared
from backend.tests.test_experience_consolidation import record, save, scan
from backend.tools import ToolService, WorkspaceManager


def real_sources(tmp_path, mode='dominance'):
    folders = {}
    definitions = {}
    for index in range(2):
        folder = tmp_path/f'source{index}'
        folder.mkdir()
        (folder/'pyproject.toml').write_text('[tool.pytest.ini_options]\n')
        (folder/'engine.py').write_text('def convert(value):\n    return None\n')
        tests = ('def test_value():\n    assert convert(1) == '+str(index+1)+'\n' if mode == 'separate' else
                 'def test_number():\n    assert convert("3") == 3\ndef test_none():\n    assert convert(None) == 0\n')
        if mode == 'dominance' and index:
            tests += 'def test_empty():\n    assert convert("") == 0\n'
        (folder/'test_engine.py').write_text('from engine import convert\n'+tests)
        name = f'project{index}'
        folders[name] = folder
        definitions[name] = ProjectDefinition(id=name, label=name, source=folder, language='python',
            repository_id=name, origin='upload', runner_config=RunnerConfig(python_paths=['.'],
                python_executable=sys.executable, test_targets=['test_engine.py']))
    settings = replace(Settings(), workspace_root=tmp_path/'workspaces', database_path=tmp_path/'app.db',
                       projects=folders, project_definitions=definitions, enable_knowledge_models=False)
    tasks, store = TaskStore(settings.database_path), ExperienceStore(settings.database_path)
    tasks.initialize(); store.initialize()
    manager = WorkspaceManager(settings.workspace_root, folders)
    sources=[]
    for index, name in enumerate(folders):
        task = tasks.create(TaskCreateRequest(source_project=name, task_description='Fix this input regression'))
        tasks.start(task.task_id)
        workspace = manager.create_workspace(folders[name], task.task_id)
        tools = ToolService(manager, settings.maven_home, runner_config=definitions[name].runner_config)
        calls, results=[], []
        def execute(name, args, final=False):
            call=ToolCall(id=str(uuid4()), name=name, arguments=args)
            result=tools.execute_tool(workspace, call)
            if final: result.metadata['final_verification']=True
            calls.append(call); results.append(result)
            tasks.append(task.task_id, TaskStep(sequence=len(calls),call=call,result=result))
            return result
        assert not execute('run_tests', {}).success
        implementation = (f'return {index+1}' if mode=='separate' else
            'return 0 if value is None else int(value)' if index==0 else
            'return 0 if value in (None, "") else int(value)' if mode=='dominance' else
            'return 0 if value is None else int(value) + 0')
        assert execute('write_file', {'path':'engine.py','content':'def convert(value):\n    '+implementation+'\n'}).success
        final=execute('run_tests', {}, True)
        assert final.success
        entry=record(repository_id=name, source_task_id=task.task_id,
            issues=[dict(error_type='AssertionError',root_cause='Input conversion violates a tested contract.',
                solution='Verified implementation: '+implementation,files=['engine.py'],symbols=['engine.py#convert'])],
            evidence=[dict(source_task_id=task.task_id,tool_call_id=final.call_id,runner='pytest',tests=final.metadata['tests'])])
        source=save(store,entry); sources.append(source)
        manager.capture_changes(workspace)
        tasks.finish(task.task_id,AgentResult(task_id=task.task_id,status='succeeded',summary='Verified real pytest execution',
            steps=calls,tool_results=results,repair_succeeded=True,tests_passed=True,changed_files=['engine.py'],
            experience_saved=True,experience_id=source.id,experience_ids=[source.id]),audit={})
    scan(store)
    verifier=ConflictVerifier(settings,tasks,manager)
    return store,sources,verifier,manager


@pytest.mark.parametrize('mode,expected', [('dominance','prefer_source'),('separate','keep_separate'),('indistinguishable',None)])
def test_normal_queue_uses_real_bidirectional_tests_and_no_manual_resolution(tmp_path,mode,expected):
    store,sources,verifier,_=real_sources(tmp_path,mode)
    worker=AutomaticMergeWorker(store.database_path,ConflictModel,verifier)
    assert worker.run_once() and worker.run_once()
    review=(store.list_conflicts(status='resolved') if expected else store.list_conflicts())[0]
    automatic=review.automatic_resolution
    assert automatic.actor=='system' and len(automatic.runs)==6
    assert review.resolution==expected
    assert automatic.state==('resolved' if expected else 'needs_confirmation')
    if expected=='prefer_source': assert review.preferred_source_id==sources[1].id
    if expected:
        from backend.experience.reviews import history
        event=history(store.database_path,review.id)[0]
        assert event['data']['actor']=='system' and event['data']['evidence_hash']==automatic.evidence_hash
        while worker.run_once():
            pass  # Source eligibility jobs resume, without rerunning evidence tests.
        assert len(store.list_conflicts(status='resolved')[0].automatic_resolution.runs)==6
    else:
        assert '不能区分' in automatic.reason
    assert all(store.get_experience(s.id)==s for s in sources)
    assert feed(store.database_path,experience_id=sources[-1].id)['items'][0]['changes'][0]['review']['automatic_resolution']


def test_changed_final_snapshot_is_not_accepted_as_test_evidence(tmp_path):
    store,sources,verifier,manager=real_sources(tmp_path)
    (manager.root/sources[0].source_task_id/'engine.py').write_text('def convert(value): return 777\n')
    worker=AutomaticMergeWorker(store.database_path,ConflictModel,verifier)
    worker.run_once();worker.run_once()
    [review]=store.list_conflicts()
    assert review.automatic_resolution.state=='needs_confirmation' and not review.automatic_resolution.runs
    assert '变化' in review.automatic_resolution.reason


def matrix(ids, outcomes=(True,False)):
    runs=[]
    for index,target in enumerate(ids):
        for repeat,implementation in [(1,target),(1,ids[1-index]),(2,ids[1-index])]:
            passed=implementation==target or outcomes[1-index]
            runs.append(dict(id=str(uuid4()),target_source_id=target,implementation_source_id=implementation,repeat=repeat,
                outcome='passed' if passed else 'failed',tests=3,failures=0 if passed else 1,errors=0,skipped=0,
                test_identity='a'*64,failed_tests=[] if passed else ['test_regression'],reproduces_baseline=not passed,
                artifact_sha256='b'*64,artifact_path='controlled-isolated-unit-test.json'))
    return runs


@pytest.mark.parametrize('bad', ['missing','different_collection','flaky','unrelated','invalid'])
def test_unreliable_evidence_never_selects_a_rule(bad):
    ids=['a','b'];runs=matrix(ids)
    cross=next(r for r in runs if r['outcome']=='failed')
    if bad=='missing':runs.pop()
    if bad=='different_collection':cross['test_identity']='different'
    if bad=='flaky':cross['outcome']='passed';cross['failed_tests']=[]
    if bad=='unrelated':cross['reproduces_baseline']=False
    if bad=='invalid':cross['outcome']='invalid'
    assert decide(runs,ids)[0] is None


def test_transplant_preserves_target_interface_and_surrounding_code():
    target='FLAG = 1\ndef f(x: object):\n    return 1\n\ndef other(): return FLAG\n'
    donor='def f(x: str):\n    return 2\n'
    changed=transplant(target,donor,'python','f')
    assert changed=='FLAG = 1\ndef f(x: object):\n    return 2\n\ndef other(): return FLAG\n'
    with pytest.raises(ValueError):transplant(target,'def f(x, y):\n    return 2\n','python','f')
    with pytest.raises(ValueError):transplant(target,'def f(x):\n    return external(x)\n','python','f')


def test_automatic_decision_cannot_overwrite_a_concurrent_human_choice(prepared):
    store,older,newer,worker=prepared
    class RacingVerifier:
        def verify(self,review_id,sources,targets,progress):
            self.review_id=review_id
            return dict(runs=matrix([s['id'] for s in sources]),bindings=[],targets=targets)
        @contextmanager
        def publication_guard(self,evidence):
            store.resolve_conflict(self.review_id,ConflictResolution(version=1,resolution='keep_separate',note='A concurrent user decision'))
            yield
    worker.conflict_verifier=RacingVerifier()
    worker.run_once()
    [review]=store.list_conflicts(status='resolved')
    assert review.resolution=='keep_separate' and review.note=='A concurrent user decision'
    from backend.experience.reviews import history
    assert len(history(store.database_path,review.id))==1
    assert history(store.database_path,review.id)[0]['data'].get('actor')!='system'
