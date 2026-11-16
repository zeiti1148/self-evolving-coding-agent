"""Paired real engineering runs through ordinary upload/configure/task HTTP APIs.

No fake model, direct experience inserts, gold patches or automatic task retries.
Resuming observes previously submitted task IDs; every terminal outcome stays recorded.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import build_opener, ProxyHandler, Request
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.cache/real-update-comparison'
ARMS = {'before1':8011, 'after1':8012, 'before2':8013, 'after2':8014}
ORDER = ['before1', 'after1', 'after2', 'before2']
OPENER = build_opener(ProxyHandler({}))


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def api(arm, path, body=None, raw=False, *, idempotency_key=None):
    headers = {'Content-Type': 'application/zip' if raw else 'application/json'}
    if idempotency_key is not None:
        headers['Idempotency-Key'] = idempotency_key
    payload = body if raw else json.dumps(body).encode() if body is not None else None
    with OPENER.open(Request(f'http://127.0.0.1:{ARMS[arm]}'+path, data=payload, headers=headers), timeout=90) as response:
        return json.load(response)


def snapshot(arm, key):
    folder = OUT / 'runs' / arm
    database = folder / 'app.db'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        selected=['experiences','knowledge_rules','knowledge_sources','knowledge_relations','knowledge_review_history',
                  'knowledge_principles','knowledge_principle_sources','experience_auto_jobs','experience_auto_pairs',
                  'experience_auto_attempts','knowledge_auto_reviews','knowledge_reuse_grants']
        data={name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name in selected if name in tables}
    write(OUT/'observations'/f'{key}.json',data)


def run(record, case, records, *, record_file=None):
    arm=record['arm']; key=record['key']; file=record_file or OUT/'records.json'
    if not record.get('project'):
        archive=Path(case['archive']); assert hashlib.sha256(archive.read_bytes()).hexdigest()==case['archive_sha256']
        project=api(arm,'/api/projects/upload?'+urlencode({'label':case['label']}),archive.read_bytes(),raw=True)
        assert project['source_digest']==case['source_digest']
        record['project']=project;write(file,records)
        project=api(arm,f"/api/projects/{project['id']}/configure",{'runner_config':case['runner_config']})
        record.update(project=project,configured=True);write(file,records)
    elif not record.get('configured'):
        record['project']=api(arm,f"/api/projects/{record['project']['id']}/configure",{'runner_config':case['runner_config']})
        record['configured']=True;write(file,records)
    if not record.get('task_id'):
        parameters=api(arm,'/openapi.json')['paths']['/api/tasks']['post'].get('parameters',[])
        if not any(p.get('in')=='header' and p.get('name','').casefold()=='idempotency-key' for p in parameters):
            raise RuntimeError('Task API lacks durable idempotency; update the idle service before submitting a paid run.')
        if not record.get('submission_key'):
            snapshot(arm,key+'-before')
            request=dict(source_project=record['project']['id'],task_description=case['task_description'],max_steps=30,execution_mode='single')
            record.update(request=request,submission_key=str(uuid4()),submitted_at=datetime.now(timezone.utc).isoformat())
            write(file,records)
        response=api(arm,'/api/tasks',record['request'],idempotency_key=record['submission_key'])
        record['task_id']=response['task_id'];write(file,records)
        print(f"SUBMITTED {key} {record['task_id']}",flush=True)
    task_id=record['task_id']; last=None
    while True:
        status=api(arm,'/api/tasks/'+task_id)
        mark=(status['status'],status['steps_completed'])
        if mark!=last:
            print(f'{key}: {mark}',flush=True);last=mark
            record['status']=status;write(file,records)
        if status['status'] not in {'pending','running'}:break
        time.sleep(3)
    record['status']=status
    result=status.get('result') or {}
    tests=[v for v in result.get('tool_results',[]) if v.get('tool_name')=='run_tests']
    final=next((v for v in reversed(tests) if v.get('metadata',{}).get('final_verification')),None)
    record['verified_success']=bool(status['status']=='succeeded' and result.get('repair_succeeded') and result.get('tests_passed')
        and final and final['success'] and final['metadata'].get('tests')==case['baseline']['tests']
        and all(final['metadata'].get(k)==0 for k in ('failures','errors','skipped')))
    record['steps']=api(arm,'/api/tasks/'+task_id+'/steps')
    write(file,records)
    # Observe the real background workflow, including a genuine needs-confirmation outcome.
    deadline=time.monotonic()+1200
    while True:
        metrics=api(arm,'/api/tasks/'+task_id+'/metrics');record['metrics']=metrics;write(file,records)
        state=metrics['experience']['state']
        with sqlite3.connect((OUT/'runs'/arm/'app.db').as_uri()+'?mode=ro',uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            outstanding={table:db.execute('SELECT count(*) FROM '+table+" WHERE state IN ('pending','running')").fetchone()[0]
                for table in ('knowledge_jobs','experience_auto_jobs','experience_auto_pairs')}
            record['background_outstanding']=outstanding
            record['pending_confirmation_count']=metrics['experience']['pending_confirmation_count']
        print(f"PROCESSING {key}: {state}; requests={metrics['model_requests']} tokens={metrics['token_usage']['total_tokens']} background={metrics['experience']['token_usage']['total_tokens']}",flush=True)
        if not any(outstanding.values()) and (state in {'succeeded','failed','not_supported','not_recorded','not_applicable'}
                or metrics['experience']['pending_confirmation_count']):
            break
        if time.monotonic()>=deadline:
            record['background_observation_timeout']=True;break
        time.sleep(15)
    with sqlite3.connect((OUT/'runs'/arm/'app.db').as_uri()+'?mode=ro',uri=True) as db:
        record['audit']=json.loads(db.execute('SELECT audit_json FROM api_tasks WHERE id=?',(task_id,)).fetchone()[0])
    snapshot(arm,key+'-after')
    if arm.startswith('after') and record['phase']=='pytest-10356' and record['verified_success']:
        frozen=OUT/f'{arm}-retrieval-reference.db'
        if not frozen.exists():
            with sqlite3.connect((OUT/'runs'/arm/'app.db').as_uri()+'?mode=ro',uri=True) as origin, sqlite3.connect(frozen) as target:
                origin.backup(target)
            record['retrieval_snapshot_sha256']=hashlib.sha256(frozen.read_bytes()).hexdigest()
    record['observed_complete']=True;write(file,records)
    print(f"COMPLETED {key}: success={record['verified_success']} elapsed={record['metrics']['elapsed_ms']}",flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--max-tasks',type=int)
    args=parser.parse_args()
    manifest=json.loads((OUT/'cases/manifest.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in manifest['cases']}
    phases=['pytest-10356','sphinx-8548','pytest-10447','pytest-10356-reuse']
    plan=[dict(key=f'{arm}-{phase}',arm=arm,phase=phase,case_id='pytest-10356' if phase.endswith('-reuse') else phase)
          for phase in phases for arm in ORDER]
    protocol=dict(before_revision='d724266393588a1e93ec70f37b9351ef05288c12',after_revision='4ed926a33dd5c2d923852ae482136857fbaf7bd8',
        primary_tasks=16,independent_sequences_per_version=2,decision_budget=30,task_timeout_seconds=900,llm_timeout_seconds=90,
        execution_mode='single',order='replicate1 before-after; replicate2 after-before',
        outcomes='All submitted terminal outcomes retained; no automatic repair retry.',
        sources='Original upstream source plus public regression tests; no production gold patch.',
        initial_memory='Empty independent DB per version and repetition; later cases only see earlier actual repairs in that sequence.',
        retrieval_ablation='After a successful first pytest task, freeze references for a separate contextual/adaptive paired reuse run.',
        comparison_limits='Whole-update comparison includes prompt/tool/extraction changes. Small sample; repeated instances are not independent projects.',
        plan=plan)
    if not args.live:
        print(json.dumps(protocol,ensure_ascii=False,indent=2));return
    (OUT/'observations').mkdir(exist_ok=True)
    if not (OUT/'protocol.json').exists():write(OUT/'protocol.json',protocol)
    records=json.loads((OUT/'records.json').read_text(encoding='utf-8')) if (OUT/'records.json').exists() else plan
    count=0
    for record in records:
        if record.get('observed_complete'):continue
        run(record,cases[record['case_id']],records);count+=1
        if args.max_tasks and count>=args.max_tasks:break


if __name__=='__main__':main()
