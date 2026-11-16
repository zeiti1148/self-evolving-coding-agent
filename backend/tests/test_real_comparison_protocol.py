"""Launcher safeguards only; never launch a server or submit a real model task."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from scripts import run_real_retrieval_comparison as protocol


@pytest.fixture
def trial(tmp_path):
    folder=tmp_path/'runs'/'contextual1'
    frozen=tmp_path/'after1-retrieval-reference.db'
    registry=tmp_path/'registry.json'
    case={'task_description':'Original engineering defect'}
    record=dict(arm='contextual1',condition='contextual',configured=True,project={'id':'uploaded-source'},
                submission_key=str(uuid4()),request=dict(source_project='uploaded-source',
                task_description=case['task_description'],max_steps=30,execution_mode='single'))
    arguments=['python.exe','scripts/real_comparison_service.py','--arm-dir',str(folder),'--mode','contextual',
               '--port','8015','--reference-database',str(frozen),'--registry',str(registry)]
    return record,case,folder,frozen,registry,arguments


def process(arguments,*,parent=0,executable='C:/Python/python.exe'):
    return dict(arguments=arguments,parent=parent,executable=executable)


def test_command_line_preserves_windows_spaces_and_unicode(trial):
    if sys.platform!='win32':pytest.skip('Windows native command line parser')
    values=['C:/Program Files/Python/python.exe','scripts/real_comparison_service.py','--arm-dir',
            'D:/中级实训/project cache/contextual1','--label','paired "original" task']
    assert protocol.windows_arguments(subprocess.list2cmdline(values))==values


def test_windows_venv_redirector_is_one_owner_but_siblings_are_two(trial):
    _,_,folder,_,_,arguments=trial
    processes={10:process(arguments,executable='C:/project/.venv/Scripts/python.exe'),
               11:process(arguments,parent=10),12:process(['python.exe','unrelated.py'])}
    assert set(protocol.service_owners(folder,processes,[]))=={11}
    processes[13]=process(arguments)
    assert set(protocol.service_owners(folder,processes,[]))=={11,13}


def test_recorded_pid_reuse_is_distinguished_from_unreadable_owner(trial):
    _,_,folder,_,_,_=trial
    known=[{'arm':'contextual1','backend_pid':10}]
    assert protocol.service_owners(folder,{10:process(['python.exe','other.py'])},known)=={}
    with pytest.raises(ValueError,match='cannot be inspected'):
        protocol.service_owners(folder,{10:process(None)},known)


@pytest.mark.parametrize('option,value', [('--mode','after'),('--port','8016'),
    ('--reference-database','wrong.db'),('--registry','wrong.json'),('--task-timeout','600'),('--llm-timeout','60')])
def test_resume_rejects_different_treatment_or_budget(trial,option,value):
    record,_,folder,frozen,registry,arguments=trial
    if option in arguments:arguments[arguments.index(option)+1]=value
    else:arguments.extend([option,value])
    with pytest.raises(ValueError):
        protocol.check_service_identity(arguments,record,folder,8015,frozen,registry)


def test_resume_lost_acceptance_preserves_key_and_exact_request(trial,monkeypatch):
    record,case,_,_,_,_=trial
    original=dict(record)
    monkeypatch.setattr(protocol.runner,'api',lambda *args,**kwargs:pytest.fail('No submission while checking recovery'))
    protocol.check_resume(record,case)
    assert record==original
    record['request']=record['request']|{'max_steps':31}
    with pytest.raises(ValueError,match='differs'):
        protocol.check_resume(record,case)


def test_existing_task_resume_only_reads_that_task(trial,monkeypatch):
    record,case,_,_,_,_=trial
    record['task_id']=str(uuid4())
    seen=[]
    def api(*args,**kwargs):
        seen.append((args,kwargs));return {'task_id':record['task_id']}
    monkeypatch.setattr(protocol.runner,'api',api)
    protocol.check_resume(record,case)
    assert seen==[((record['arm'],'/api/tasks/'+record['task_id']),{})]


def test_alive_non_listening_database_owner_prevents_new_server(trial,monkeypatch):
    record,case,folder,frozen,registry,arguments=trial
    monkeypatch.setattr(protocol,'running_python_processes',lambda:{10:process(arguments)})
    monkeypatch.setattr(protocol,'occupied_port',lambda port:False)
    with pytest.raises(ValueError,match='not listening'):
        protocol.existing_service(record,case,folder,8015,frozen,registry,[])


def test_foreign_listener_rejected_and_durable_intent_can_resume(trial,monkeypatch):
    record,case,folder,frozen,registry,arguments=trial
    monkeypatch.setattr(protocol,'running_python_processes',lambda:{10:process(arguments)})
    monkeypatch.setattr(protocol,'occupied_port',lambda port:True)
    monkeypatch.setattr(protocol,'listening_owners',lambda port:{11})
    with pytest.raises(ValueError,match='not owned'):
        protocol.existing_service(record,case,folder,8015,frozen,registry,[])
    monkeypatch.setattr(protocol,'listening_owners',lambda port:{10})
    assert protocol.existing_service(record,case,folder,8015,frozen,registry,[])
    del record['submission_key']
    with pytest.raises(ValueError,match='durable submission intent'):
        protocol.existing_service(record,case,folder,8015,frozen,registry,[])


def test_two_runner_processes_cannot_own_same_orchestration_lock(tmp_path):
    path=tmp_path/'orchestration.lock'
    with protocol.orchestration_lock(path):
        with pytest.raises(RuntimeError,match='orchestration lock'):
            with protocol.orchestration_lock(path):pytest.fail('Second lock unexpectedly acquired')
    with protocol.orchestration_lock(path):
        pass
    assert path.read_bytes()==b'1'
