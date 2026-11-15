"""Induce a shared principle from immutable, independently repaired OSS tasks."""
import argparse
from dataclasses import replace
import hashlib
import json
import itertools
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.jobs import drain
from backend.experience.principles import consolidate_principle, retrieve_principles
from backend.experience.store import _insert
from backend.llm_configuration import api_identity
from backend.shared import Experience
from scripts.validate_engineering_merge import source_digest


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--llm-env-file',type=Path,required=True)
    p.add_argument('--expect-api-identity',required=True)
    args=p.parse_args()
    run=args.run.resolve()
    tasks=json.loads((run/'report.json').read_text(encoding='utf-8'))
    settings=replace(Settings.from_env(llm_env_file=args.llm_env_file),llm_timeout=180)
    identity=api_identity(settings)
    if identity['identity']!=args.expect_api_identity or tasks['api_configuration']!=identity:
        raise ValueError('API profile changed; no request sent')
    if not tasks['completed']:
        raise ValueError('Both repairs must complete first')
    validation_path=run/'validation.json'
    validation=json.loads(validation_path.read_text(encoding='utf-8')) if validation_path.exists() else {}
    problems=[]
    if not validation.get('completed'):
        problems.append('Extended validation is incomplete')
    for task in tasks['tasks']:
        check=next((t for t in validation.get('tasks',[]) if t['case']==task['case']['id']),{})
        snapshot=source_digest(Path(task['workspace']))
        for suite in ['model-component','model-holdout']:
            result=next((r for r in check.get('checks',[]) if r['label']==suite),{})
            if (not result.get('tests') or result.get('failures') or result.get('errors')
                    or result.get('execution',{}).get('exit_code')!=0
                    or result.get('source_snapshot')!=snapshot
                    or suite=='model-holdout' and result.get('skipped')):
                problems.append(task['case']['id']+'/'+suite+': failed, missing, or stale evidence')
    gate=dict(eligible=not problems,reasons=problems,api_requests=0,
              validation=str(validation_path),source_ids=[e['id'] for t in tasks['tasks'] for e in t['experiences']])
    (run/'merge-gate.json').write_text(json.dumps(gate,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if problems:
        print('MERGE BLOCKED',json.dumps(problems,ensure_ascii=False),flush=True)
        return
    records=[Experience.model_validate(e) for t in tasks['tasks'] for e in t['experiences']]
    if not 2<=len(records)<=6 or len({e.repository_id for e in records})!=2:
        raise ValueError('Expected bounded records from the two independent source repositories')
    assert len({e.source_task_id for e in records})==2
    assert all(not q.get('request_experience_ids') for t in tasks['tasks'] for q in t['requests'])
    frozen={t['database']:digest(t['database']) for t in tasks['tasks']}
    store=ExperienceStore(run/'shared-principles.db');store.initialize()
    with connect(store.database_path) as c:
        c.execute('BEGIN IMMEDIATE')
        for e in records:
            if not c.execute('SELECT 1 FROM experiences WHERE id=?',(e.id,)).fetchone():
                _insert(c,e)
    drain(store.database_path,semantic=None,seconds=15)
    namespace='real-upstream-engineering'
    report_path=run/'merge.json'
    previous=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    model=ChatClient(settings);model.set_deadline(time.monotonic()+360)
    report=dict(source_ids=[r.id for r in records],source_database_sha256=frozen,
                namespace=namespace,database=str(store.database_path),api_configuration=identity,
                requests=previous.get('requests',[]),completed=False)
    try:
        pairs=[(a,b) for a,b in itertools.combinations(records,2) if a.repository_id!=b.repository_id and a.source_task_id!=b.source_task_id]
        candidates=[]
        for a,b in pairs:
            result=consolidate_principle(store.database_path,[a.id,b.id],model,namespace=namespace)
            candidates.append(dict(source_ids=[a.id,b.id],result=result))
        report['candidate_results']=candidates
        accepted=[r['result'] for r in candidates if r['result']['state']=='succeeded']
        report['result']=accepted[0] if len(accepted)==1 else dict(state='ambiguous_or_no_principle',accepted=len(accepted))
        if len(accepted)==1:
            result=accepted[0]
            patterns=retrieve_principles(store.database_path,namespace=namespace)
            repeated=consolidate_principle(store.database_path,result['principle']['source_ids'],model,namespace=namespace)
            report.update(principles=patterns,idempotent=repeated['reused'] and repeated['id']==result['id'],
                original_records_unchanged=all(store.get_experience(r.id)==r for r in records),
                source_databases_unchanged=all(digest(path)==sha for path,sha in frozen.items()),
                namespace_isolated=not retrieve_principles(store.database_path,namespace='unrelated-project'),
                completed=True)
    finally:
        report['requests'].extend(model.audit)
        model.close()
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('MERGE',report.get('result',{}).get('state'),report_path,flush=True)
    if report.get('principles'):
        print(report['principles'][0]['principle'],flush=True)


if __name__=='__main__':
    main()
