"""Supplementary real safety regression, separate from the registered 20 trials.

Default: read-only plan inspection. --live: after all 20 observations finish,
back up the actually earned after1 database and run one normal uploaded repair.
Never changes seed experience rows, fabricates an earlier state or retries a paid task.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_real_update_comparison as runner
from scripts import run_real_retrieval_comparison as safeguards

ARM='safety1'
PORT=8019
FRONT=5189
GROUP='补充安全回归'
TERMINAL={'succeeded','failed','stopped','cancelled','canceled','error','timed_out'}
CORE_FILES=('backend/agent/core.py','backend/agent/context.py','backend/experience/store.py',
    'backend/experience/knowledge.py','backend/experience/library.py',
    'backend/experience/retrieval.py','backend/experience/retrieval_evidence.py','backend/experience/recovery.py',
    'backend/experience/automation.py','backend/experience/automatic_resolution.py')
LIMITATION=('This supplementary run is excluded from the original 20-trial comparison. '
    'It seeds the current after1 database without editing experience or paused records. '
    'Later unknown relations already block the earlier 7ada source; therefore this run '
    'can verify the current repair flow and actual injected constraints, but does not '
    'reproduce the earlier c82 constraint leak or establish a controlled patch effect.')


def read(path,default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def completion_gate(out):
    """Do not treat an empty/partial list or a running task as a completed trial."""
    result={};all_identifiers=[]
    for name,expected in (('records.json',16),('retrieval-records.json',4)):
        rows=read(out/name,[])
        if isinstance(rows,dict):rows=rows.get('records',[])
        complete=[row for row in rows if row.get('observed_complete') is True
                  and (row.get('status') or {}).get('status') in TERMINAL and row.get('task_id')]
        identifiers=[row.get('task_id') for row in rows]
        all_identifiers.extend(identifiers)
        ready=len(rows)==expected and len(complete)==expected and len(set(identifiers))==expected
        result[name]=dict(expected=expected,records=len(rows),completed=len(complete),ready=ready)
    return dict(ready=all(value['ready'] for value in result.values()) and len(set(all_identifiers))==20,groups=result)


def current_core():
    head=subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True,
                        encoding='utf-8',check=True,timeout=20).stdout.strip()
    return dict(git_head=head,source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in CORE_FILES})


def planned(out):
    return dict(mode='dry_run',gate=completion_gate(out),arm=ARM,backend_port=PORT,frontend_port=FRONT,
        database=str(out/'runs'/ARM/'app.db'),seed=str(out/'runs'/'after1'/'app.db'),
        record_file=str(out/'safety-records.json'),group=GROUP,case_id='pytest-10356',
        model_config_file=str(ROOT/'.env'),service_mode='after',reference_database=None,
        max_steps=30,task_timeout_seconds=900,llm_timeout_seconds=90,
        current_core=current_core(),limitation=LIMITATION,live_calls_started=False)


def backup_seed(seed,folder,core):
    """SQLite online backup reads a consistent source including its WAL."""
    database=folder/'app.db'
    origin_file=folder/'snapshot-origin.json'
    if database.exists():
        origin=read(origin_file)
        if not origin or Path(origin.get('seed_database','')).resolve()!=seed.resolve():
            raise ValueError('Existing safety database has no matching backup provenance; never overwrite it.')
        if origin.get('core')!=core:
            raise ValueError('Safety core changed since the seeded run; preserve that run instead of silently resuming with different code.')
        return origin
    if origin_file.exists():
        raise ValueError('Backup provenance exists without its database; inspect interrupted preparation.')
    if not seed.is_file():raise ValueError('The real after1 seed database is missing.')
    temporary=folder/'app.db.backup-in-progress'
    if temporary.exists():raise ValueError('An interrupted backup exists; inspect it before another attempt.')
    with closing(sqlite3.connect(seed.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(temporary)) as target:
        source.execute('PRAGMA query_only=ON')
        source.backup(target)
        tables={row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        task_ids=[row[0] for row in target.execute('SELECT id FROM api_tasks ORDER BY id')] if 'api_tasks' in tables else []
    origin=dict(seed_database=str(seed.resolve()),seed_method='SQLite online backup, source read-only; no row edits',
        backup_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest(),seed_task_ids=task_ids,core=core,
        model_config_file=str(ROOT/'.env'),created_at=datetime.now(timezone.utc).isoformat(),limitation=LIMITATION)
    temporary.replace(database)
    runner.write(origin_file,origin)
    return origin


def register_source(registry,database,seed,core):
    """Original seed source precedes its clone: task-metrics deduplicates by task ID."""
    entries=registry.get('sources',[])
    seed_path=seed.resolve();target_path=database.resolve()
    seed_indices=[i for i,item in enumerate(entries) if Path(item['database_path']).resolve()==seed_path]
    if len(seed_indices)!=1:raise ValueError('Registry must retain exactly one original seed source for task-ID deduplication.')
    matches=[i for i,item in enumerate(entries) if Path(item['database_path']).resolve()==target_path]
    entry=dict(database_path=str(database),runtime_label='当前修复·补充安全回归',comparison_group=GROUP,
               code_revision=core['git_head'],task_url_base=f'http://127.0.0.1:{FRONT}')
    if matches:
        if len(matches)!=1 or matches[0]<=seed_indices[0] or entries[matches[0]]!=entry:
            raise ValueError('Existing safety registry entry differs or would relabel cloned historical tasks.')
    else:entries.append(entry)
    registry['sources']=entries


def check_service_identity(arguments,folder,registry):
    expected={'--mode':'after','--port':str(PORT),'--group':GROUP,'--public-base-url':f'http://127.0.0.1:{FRONT}'}
    if any(safeguards.option(arguments,key)!=value for key,value in expected.items()):
        raise ValueError('Safety service mode, group, public URL or port differs.')
    if safeguards.option(arguments,'--reference-database') is not None:
        raise ValueError('Safety verification must use the current real store, never FrozenSearchStore.')
    for key,path in (('--arm-dir',folder),('--registry',registry)):
        value=safeguards.option(arguments,key)
        if value is None or safeguards.resolved(value)!=path.resolve():raise ValueError('Safety service paths differ.')
    for name,expected_value in (('--task-timeout',900),('--llm-timeout',90)):
        if float(safeguards.option(arguments,name) or expected_value)!=expected_value:
            raise ValueError('Safety service budgets differ.')


def check_submission(record,case,folder,origin):
    project=record.get('project') or {}
    if project and project.get('source_digest')!=case.get('source_digest'):
        raise ValueError('Uploaded safety source differs from the original registered archive.')
    if record.get('configured'):
        configured=project.get('runner_config') or {}
        if any(configured.get(name)!=value for name,value in case.get('runner_config',{}).items()):
            raise ValueError('Uploaded safety runner differs from the original test configuration.')
    if record.get('task_id') or record.get('submission_key'):
        expected=dict(source_project=(record.get('project') or {}).get('id'),task_description=case['task_description'],
                      max_steps=30,execution_mode='single')
        if not expected['source_project'] or record.get('request')!=expected or not record.get('configured'):
            raise ValueError('Recorded safety request differs from the original case and budget.')
        if not record.get('submission_key'):raise ValueError('Safety task is missing its durable submission key.')
        UUID(record['submission_key'])
        if record.get('task_id') and runner.api(ARM,'/api/tasks/'+record['task_id'])['task_id']!=record['task_id']:
            raise ValueError('Safety task does not belong to this service.')
    else:
        if record.get('request'):raise ValueError('Recorded request has no durable submission key.')
        # Before the durable intent is written, no paid task may already exist.
        with closing(sqlite3.connect((folder/'app.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ids={row[0] for row in db.execute('SELECT id FROM api_tasks')} if 'api_tasks' in tables else set()
        if ids-set(origin['seed_task_ids']):
            raise ValueError('Unrecorded safety tasks exist; do not submit another model run.')


def existing_backend(folder,registry,known):
    owners=safeguards.service_owners(folder,safeguards.running_python_processes(),known)
    if len(owners)>1:raise ValueError('Multiple processes own the safety database; no second service is allowed.')
    if safeguards.occupied_port(PORT):
        if len(owners)!=1 or safeguards.listening_owners(PORT)!=set(owners):
            raise ValueError('Safety port belongs to a different process.')
        check_service_identity(next(iter(owners.values())),folder,registry)
        if not any(entry.get('arm')==ARM and entry.get('backend_pid') for entry in known):
            raise ValueError('Live safety owner has no durable launcher record; inspect ownership before resuming.')
        return True
    if owners:raise ValueError('Safety database owner is alive but not listening; do not launch a second owner.')
    return False


def node_processes():
    rows=safeguards.powershell_json("ConvertTo-Json -InputObject @(Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Select-Object ProcessId,CommandLine) -Compress")
    return {int(row['ProcessId']):safeguards.windows_arguments(row['CommandLine']) if row.get('CommandLine') else None for row in rows}


def frontend_state(known):
    nodes=node_processes()
    recorded={entry['frontend_pid'] for entry in known if entry.get('arm')==ARM and entry.get('frontend_pid')}
    listeners=safeguards.listening_owners(FRONT)
    if safeguards.occupied_port(FRONT):
        if len(listeners)!=1 or not listeners<=recorded:raise ValueError('Safety frontend port has an unowned listener.')
        arguments=nodes.get(next(iter(listeners)))
        if not arguments or not any(safeguards.resolved(arg)==(ROOT/'frontend/node_modules/vite/bin/vite.js').resolve()
            for arg in arguments if arg.endswith('vite.js')):
            raise ValueError('Safety frontend listener is not the recorded Vite process.')
        if safeguards.option(arguments,'--port')!=str(FRONT) or safeguards.option(arguments,'--host')!='127.0.0.1' or '--strictPort' not in arguments:
            raise ValueError('Safety frontend configuration differs.')
        return True
    if recorded & nodes.keys():raise ValueError('Recorded frontend is alive but not listening; do not launch a duplicate.')
    return False


def launch_backend(folder,registry,known):
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    with (folder/'server.out.log').open('a',encoding='utf-8') as stdout,(folder/'server.err.log').open('a',encoding='utf-8') as stderr:
        process=subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'scripts/real_comparison_service.py'),
            '--arm-dir',str(folder),'--mode','after','--label','当前修复·补充安全回归','--group',GROUP,
            '--registry',str(registry),'--public-base-url',f'http://127.0.0.1:{FRONT}',
            '--task-timeout','900','--llm-timeout','90','--port',str(PORT)],cwd=ROOT,
            env=os.environ|{'LLM_CONFIG_SOURCE':'project','LLM_CONFIG_FILE':str(ROOT/'.env')},
            stdout=stdout,stderr=stderr,creationflags=flags)
    known.append(dict(arm=ARM,backend_pid=process.pid,frontend_pid=None,port=PORT,frontend=FRONT))
    runner.write(runner.OUT/'safety-services.json',known)
    deadline=time.monotonic()+30
    while True:
        try:runner.api(ARM,'/api/projects');break
        except OSError:
            if time.monotonic()>deadline:raise
            time.sleep(.5)
    if not existing_backend(folder,registry,known):raise ValueError('New safety backend cannot be verified.')


def launch_frontend(folder,known):
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    with (folder/'frontend.log').open('a',encoding='utf-8') as log:
        process=subprocess.Popen(['C:/Program Files/nodejs/node.exe',str(ROOT/'frontend/node_modules/vite/bin/vite.js'),
            '--host','127.0.0.1','--port',str(FRONT),'--strictPort'],cwd=ROOT/'frontend',
            env=os.environ|{'API_PROXY_TARGET':f'http://127.0.0.1:{PORT}'},stdout=log,stderr=log,creationflags=flags)
    known[-1]['frontend_pid']=process.pid
    runner.write(runner.OUT/'safety-services.json',known)
    deadline=time.monotonic()+30
    while not safeguards.occupied_port(FRONT):
        if time.monotonic()>deadline:raise TimeoutError('Safety frontend did not become ready.')
        time.sleep(.5)
    frontend_state(known)


def run_live():
    out=runner.OUT
    if not completion_gate(out)['ready']:
        raise ValueError('All 16 primary and 4 retrieval trials must be terminal and observed_complete before safety verification.')
    core=current_core()
    cases=read(out/'cases/manifest.json',{})['cases']
    case=next(item for item in cases if item['id']=='pytest-10356')
    folder=out/'runs'/ARM;seed=out/'runs'/'after1'/'app.db';registry_file=out/'registry.json'
    records_file=out/'safety-records.json';records=read(records_file,[])
    if records and (len(records)!=1 or records[0].get('key')!=ARM):raise ValueError('Unexpected supplemental records; never overwrite prior trials.')
    if records and records[0].get('observed_complete'):
        print('Supplemental safety task already observed; no new service or model task started.');return
    known=read(out/'safety-services.json',[])
    alive=existing_backend(folder,registry_file,known)
    front_alive=frontend_state(known)
    if alive and not (folder/'app.db').is_file():raise ValueError('An existing service is initializing the safety DB; do not clone into it.')
    folder.mkdir(parents=True,exist_ok=True)
    origin=backup_seed(seed,folder,core)
    registry=read(registry_file)
    if registry is None:raise ValueError('Original metrics registry is missing.')
    register_source(registry,folder/'app.db',seed,core);runner.write(registry_file,registry)
    if not records:
        records=[dict(key=ARM,arm=ARM,phase='supplemental-safety',case_id='pytest-10356',condition='safety_patch',
            seed_backup_sha256=origin['backup_sha256'],core=core,comparison_group=GROUP,
            case_source_digest=case['source_digest'],case_archive_sha256=case['archive_sha256'],
            excluded_from_primary=True,limitation=LIMITATION)]
        runner.write(records_file,records)
    if records[0].get('core')!=core or records[0].get('seed_backup_sha256')!=origin['backup_sha256']:
        raise ValueError('Recorded safety task belongs to a different seed or implementation.')
    runner.ARMS[ARM]=PORT
    if not alive:launch_backend(folder,registry_file,known)
    provenance=read(folder/'service-provenance.json',{})
    if safeguards.resolved(provenance.get('model_config_file',''))!=(ROOT/'.env').resolve():
        raise ValueError('Safety service did not load this project .env.')
    if provenance.get('reference_database') is not None:raise ValueError('Unexpected frozen reference database.')
    loaded=(provenance.get('kernel') or {}).get('source_sha256') or {}
    if not loaded or any(loaded[name]!=core['source_sha256'].get(name) for name in loaded):
        raise ValueError('Live service core differs from the recorded safety implementation.')
    check_submission(records[0],case,folder,origin)
    if not front_alive:launch_frontend(folder,known)
    # Main runner writes submission_key + exact body before POST and reuses both
    # on recovery. It never retries a terminal task, including failed repairs.
    runner.run(records[0],case,records,record_file=records_file)
    print(json.dumps({'supplemental_task_id':records[0].get('task_id'),'record_file':str(records_file),
        'excluded_from_primary':True,'limitation':LIMITATION},ensure_ascii=False))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    args=parser.parse_args(argv)
    if not args.live:
        print(json.dumps(planned(runner.OUT),ensure_ascii=False,indent=2));return
    with safeguards.orchestration_lock(runner.OUT/'safety-orchestration.lock'):
        run_live()


if __name__=='__main__':main()
