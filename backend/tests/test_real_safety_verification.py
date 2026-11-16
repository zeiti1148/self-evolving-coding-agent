"""Launcher guards with isolated accounting fixtures; no server or model is started."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from scripts import run_real_safety_verification as safety


def complete_plan(tmp_path):
    for name,count in [('records.json',16),('retrieval-records.json',4)]:
        rows=[{'task_id':str(uuid4()),'status':{'status':'stopped'},'observed_complete':True} for _ in range(count)]
        (tmp_path/name).write_text(json.dumps(rows),encoding='utf-8')


def test_live_gate_requires_exact_twenty_completed_unique_observations(tmp_path):
    assert safety.completion_gate(tmp_path)['ready'] is False
    complete_plan(tmp_path)
    assert safety.completion_gate(tmp_path)['ready'] is True
    rows=json.loads((tmp_path/'retrieval-records.json').read_text())
    rows[-1]['observed_complete']=False
    (tmp_path/'retrieval-records.json').write_text(json.dumps(rows))
    assert safety.completion_gate(tmp_path)['ready'] is False
    rows[-1]['observed_complete']=True;rows[-1]['status']['status']='running'
    (tmp_path/'retrieval-records.json').write_text(json.dumps(rows))
    assert safety.completion_gate(tmp_path)['ready'] is False
    rows[-1]['status']['status']='succeeded';rows[-1]['task_id']=rows[0]['task_id']
    (tmp_path/'retrieval-records.json').write_text(json.dumps(rows))
    assert safety.completion_gate(tmp_path)['ready'] is False


def test_gate_rejects_reusing_primary_ids_as_ablation(tmp_path):
    complete_plan(tmp_path)
    primary=json.loads((tmp_path/'records.json').read_text())
    (tmp_path/'retrieval-records.json').write_text(json.dumps(primary[:4]))
    assert safety.completion_gate(tmp_path)['ready'] is False


def test_dry_run_is_read_only_and_does_not_start_services(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(safety.runner,'OUT',tmp_path)
    monkeypatch.setattr(safety,'current_core',lambda:{'git_head':'test','source_sha256':{}})
    monkeypatch.setattr(safety,'run_live',lambda:pytest.fail('Dry run must not start a service'))
    safety.main([])
    result=json.loads(capsys.readouterr().out)
    assert result['live_calls_started'] is False and result['gate']['ready'] is False
    assert result['reference_database'] is None and result['service_mode']=='after'
    assert result['max_steps']==30 and result['backend_port']==8019 and result['frontend_port']==5189
    assert result['model_config_file']==str(safety.ROOT/'.env')
    assert list(tmp_path.iterdir())==[]


def test_live_refuses_before_any_clone_or_process_read(tmp_path,monkeypatch):
    monkeypatch.setattr(safety.runner,'OUT',tmp_path)
    monkeypatch.setattr(safety,'current_core',lambda:pytest.fail('Incomplete experiment must stop before preparation'))
    with pytest.raises(ValueError,match='16 primary and 4 retrieval'):
        safety.run_live()
    assert list(tmp_path.iterdir())==[]


def test_core_provenance_detects_changes_in_the_actual_safety_filter_modules(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(safety,'ROOT',tmp_path)
    monkeypatch.setattr(safety.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='same-git-head\n'))
    for name in safety.CORE_FILES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'initial')
    before=safety.current_core()
    for name in ('backend/experience/knowledge.py','backend/experience/library.py'):
        (tmp_path/name).write_bytes(b'patched filtering behavior')
    after=safety.current_core()
    assert before['git_head']==after['git_head']
    for name in ('backend/experience/knowledge.py','backend/experience/library.py'):
        assert before['source_sha256'][name]!=after['source_sha256'][name]


@pytest.fixture
def seeded(tmp_path):
    seed=tmp_path/'after1.db';folder=tmp_path/'safety1';folder.mkdir()
    with closing(sqlite3.connect(seed)) as db:
        db.execute('CREATE TABLE api_tasks (id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO api_tasks VALUES (?)',('inherited-task',))
        db.execute('CREATE TABLE experiences (id TEXT, state TEXT, constraints TEXT)')
        db.execute('INSERT INTO experiences VALUES (?,?,?)',('real-source','paused','original constraints'))
        db.commit()
    core={'git_head':'current','source_sha256':{'backend/experience/store.py':'patched'}}
    return seed,folder,core


def test_sqlite_backup_preserves_seed_and_rows_and_records_current_core(seeded):
    seed,folder,core=seeded
    original=seed.read_bytes()
    provenance=safety.backup_seed(seed,folder,core)
    assert seed.read_bytes()==original
    assert provenance['seed_task_ids']==['inherited-task'] and provenance['core']==core
    assert provenance['backup_sha256']==hashlib.sha256((folder/'app.db').read_bytes()).hexdigest()
    with closing(sqlite3.connect(folder/'app.db')) as db:
        assert db.execute('SELECT * FROM experiences').fetchall()==[('real-source','paused','original constraints')]
        db.execute('INSERT INTO api_tasks VALUES (?)',('new-task',));db.commit()
    # Resume preserves output rows rather than recloning over a completed/paid task.
    assert safety.backup_seed(seed,folder,core)==provenance
    with closing(sqlite3.connect(folder/'app.db')) as db:
        assert db.execute('SELECT count(*) FROM api_tasks').fetchone()[0]==2
    assert seed.read_bytes()==original


def test_existing_backup_rejects_changed_code_or_missing_provenance(seeded):
    seed,folder,core=seeded
    safety.backup_seed(seed,folder,core)
    with pytest.raises(ValueError,match='core changed'):
        safety.backup_seed(seed,folder,{'git_head':'different'})
    (folder/'snapshot-origin.json').unlink()
    with pytest.raises(ValueError,match='no matching backup provenance'):
        safety.backup_seed(seed,folder,core)


def test_registry_keeps_original_task_ids_ahead_of_clone(seeded):
    seed,folder,core=seeded
    registry={'sources':[{'database_path':str(seed),'comparison_group':'original'}]}
    safety.register_source(registry,folder/'app.db',seed,core)
    assert registry['sources'][0]['comparison_group']=='original'
    assert registry['sources'][1]['comparison_group']==safety.GROUP
    assert Path(registry['sources'][1]['database_path'])==folder/'app.db'
    safety.register_source(registry,folder/'app.db',seed,core)
    assert len(registry['sources'])==2
    registry['sources'].reverse()
    with pytest.raises(ValueError,match='relabel cloned historical'):
        safety.register_source(registry,folder/'app.db',seed,core)


def service_args(folder,registry):
    return ['python.exe','scripts/real_comparison_service.py','--arm-dir',str(folder),'--registry',str(registry),
        '--mode','after','--port','8019','--group',safety.GROUP,'--public-base-url','http://127.0.0.1:5189',
        '--task-timeout','900','--llm-timeout','90']


@pytest.mark.parametrize('name,value',[('--reference-database','frozen.db'),('--mode','contextual'),('--port','8012'),('--task-timeout','600')])
def test_safety_service_rejects_frozen_store_or_wrong_identity(tmp_path,name,value):
    folder=tmp_path/'safety1';registry=tmp_path/'registry.json';args=service_args(folder,registry)
    safety.check_service_identity(args,folder,registry)
    if name in args:args[args.index(name)+1]=value
    else:args.extend([name,value])
    with pytest.raises(ValueError):safety.check_service_identity(args,folder,registry)


def test_lost_task_response_reuses_durable_key_and_same_original_request(seeded,monkeypatch):
    seed,folder,core=seeded;origin=safety.backup_seed(seed,folder,core)
    case={'task_description':'Original issue without solution hints'}
    record=dict(submission_key=str(uuid4()),configured=True,project={'id':'uploaded'},
        request={'source_project':'uploaded','task_description':case['task_description'],'max_steps':30,'execution_mode':'single'})
    before=json.dumps(record,sort_keys=True)
    monkeypatch.setattr(safety.runner,'api',lambda *a,**kw:pytest.fail('Checking an intent does not submit it'))
    safety.check_submission(record,case,folder,origin)
    assert json.dumps(record,sort_keys=True)==before
    record['request']['max_steps']=31
    with pytest.raises(ValueError,match='differs'):safety.check_submission(record,case,folder,origin)


def test_resume_rejects_source_or_test_configuration_drift(seeded):
    seed,folder,core=seeded;origin=safety.backup_seed(seed,folder,core)
    case={'source_digest':'original','runner_config':{'test_targets':['tests/full']}}
    record={'project':{'id':'uploaded','source_digest':'other'}}
    with pytest.raises(ValueError,match='source differs'):safety.check_submission(record,case,folder,origin)
    record['project'].update(source_digest='original',runner_config={'test_targets':['tests/smaller']})
    record['configured']=True
    with pytest.raises(ValueError,match='runner differs'):safety.check_submission(record,case,folder,origin)


def test_unrecorded_task_prevents_another_paid_submission(seeded):
    seed,folder,core=seeded;origin=safety.backup_seed(seed,folder,core)
    safety.check_submission({}, {'task_description':'issue'},folder,origin)
    with closing(sqlite3.connect(folder/'app.db')) as db:
        db.execute('INSERT INTO api_tasks VALUES (?)',('unrecorded-paid-task',));db.commit()
    with pytest.raises(ValueError,match='Unrecorded safety tasks'):
        safety.check_submission({}, {'task_description':'issue'},folder,origin)


def test_foreign_backend_listener_does_not_start_or_reuse_any_process(tmp_path,monkeypatch):
    monkeypatch.setattr(safety.safeguards,'running_python_processes',lambda:{})
    monkeypatch.setattr(safety.safeguards,'occupied_port',lambda port:True)
    monkeypatch.setattr(safety.safeguards,'listening_owners',lambda port:{999})
    with pytest.raises(ValueError,match='different process'):
        safety.existing_backend(tmp_path/'safety1',tmp_path/'registry.json',[])


def test_live_database_owner_without_listener_blocks_duplicate(tmp_path,monkeypatch):
    folder=tmp_path/'safety1';registry=tmp_path/'registry.json'
    processes={10:dict(arguments=service_args(folder,registry),parent=0,executable='C:/Python/python.exe')}
    monkeypatch.setattr(safety.safeguards,'running_python_processes',lambda:processes)
    monkeypatch.setattr(safety.safeguards,'occupied_port',lambda port:False)
    with pytest.raises(ValueError,match='alive but not listening'):
        safety.existing_backend(folder,registry,[])
