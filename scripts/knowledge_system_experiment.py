"""Offline ablations, 40 workflow groups and 100/1,000/10,000-record capacity checks."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
import zipfile
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.experience import ExperienceStore
from backend.experience.database import connect, read_only
from backend.experience.jobs import claim, drain, process_page, report
from backend.experience.knowledge import canonical, rule_text
from backend.experience.relations import classify
from backend.experience.semantic import LocalSemantics
from backend.shared import ExperienceCreate,ConflictResolution
from scripts.knowledge_semantic_experiment import corpus,metrics
from scripts.experiment import write_json


def percentile(values,p):
    return sorted(values)[min(len(values)-1,int((len(values)-1)*p))] if values else None


def record(index=0,**updates):
    task=str(uuid4())
    return ExperienceCreate(**(dict(schema_version=4,language='python',scope='project',repository_id='capacity',
        task_summary='Handle input',root_cause='Input is missing',solution='Reject missing input',verification='Original tests passed',
        compatibility_key='contract-v1',source_task_id=task,applicability=['Registered input contract'],constraints=[],
        issues=[dict(error_type='ValueError',root_cause='Input is missing',solution='Reject missing input',files=[f'file{index}.py'])],
        evidence=[dict(source_task_id=task,tool_call_id=str(uuid4()),runner='pytest',tests=3)])|updates))


def refs(store): return store.retrieve_model_references('missing input',language='python',repository_id='capacity',error_types=[])


def capacity(directory,size):
    store=ExperienceStore(directory/f'capacity-{size}.db');store.initialize()
    latencies=[];started=time.monotonic()
    for index in range(size):
        value=record(index);before=time.perf_counter()
        store.save_experiences([value],source_task_id=value.source_task_id)
        latencies.append((time.perf_counter()-before)*1000)
    save_seconds=time.monotonic()-started
    pending_before=report(store.database_path).pending_jobs
    assert refs(store)==[]
    started=time.monotonic()
    while report(store.database_path).pending_jobs:
        drain(store.database_path,seconds=60,max_pages=1000)
    queue_seconds=time.monotonic()-started
    read_times=[]
    for _ in range(10):
        before=time.perf_counter();selected=refs(store);read_times.append((time.perf_counter()-before)*1000)
    with read_only(store.database_path) as connection:
        counts={table:connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
            ['experiences','knowledge_rules','knowledge_evidence','knowledge_sources','knowledge_relations']}
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
    return dict(size=size,save_seconds=save_seconds,save_p50_ms=statistics.median(latencies),save_p95_ms=percentile(latencies,.95),
        queue_seconds=queue_seconds,pending_before=pending_before,pending_after=report(store.database_path).pending_jobs,
        retrieval_p50_ms=statistics.median(read_times),retrieval_p95_ms=percentile(read_times,.95),reference_count=len(selected),
        database_bytes=store.database_path.stat().st_size,counts=counts,integrity=integrity,
        workload='Distinct exact file scopes, no semantic inference; dense overlaps are separately tested at 61 rules.')


def group_checks(directory):
    """Five repeat seeds x eight workflow risks. No fake accuracy claims about NLI here."""
    results=[]
    for repetition in range(5):
        for kind in ['duplicates','capacity61','case','versions','qualifiers','partial','dependency','undo']:
            store=ExperienceStore(directory/f'group-{repetition}-{kind}.db');values=[]
            def save(value):
                item=store.save_experience(value);values.append(item);return item
            if kind=='duplicates':
                for _ in range(20): save(record())
            elif kind=='capacity61':
                for i in range(61):
                    save(record(issues=[dict(error_type='ValueError',root_cause=f'Context {i}',solution='Reject missing input',files=['same.py'])]))
            elif kind=='case':
                for file,solution in [('Rules.py','Reject input'),('rules.py','Accept input')]:
                    save(record(issues=[dict(error_type='ValueError',root_cause='Missing',solution=solution,files=[file])]))
            elif kind=='versions':
                save(record());save(record(compatibility_key='contract-v2'))
            elif kind=='qualifiers':
                save(record(schema_version=3,solution='Only readonly mode may use a default',constraints=[]))
                save(record(schema_version=3,solution='Write mode must reject missing input',constraints=[]))
            elif kind in {'partial','dependency'}:
                a=dict(error_type='ValueError',root_cause='Missing',solution='Reject input',files=['a.py'])
                b=dict(error_type='TypeError',root_cause='Normalize',solution='Normalize input',files=['b.py'],depends_on=[0] if kind=='dependency' else [])
                save(record(issues=[a,b]));save(record(issues=[a|{'solution':'Accept input'}]))
            else:
                save(record());save(record(issues=[dict(error_type='ValueError',root_cause='Missing',solution='Accept input',files=['file0.py'])]))
            before=[v.model_dump_json() for v in values]
            while report(store.database_path).pending_jobs:
                drain(store.database_path,seconds=60,max_pages=1000)
            selected=refs(store)
            reviews=store.list_conflicts(limit=100)
            checks=[before==[store.get_experience(v.id).model_dump_json() for v in values]]
            if kind=='duplicates':
                with read_only(store.database_path) as connection:
                    checks += [connection.execute('SELECT count(*) FROM knowledge_rules').fetchone()[0]==1,
                               connection.execute('SELECT count(*) FROM knowledge_evidence').fetchone()[0]==20]
                checks.append(len(selected)==1)
            elif kind=='capacity61': checks += [report(store.database_path).pending_jobs==0,len(selected)==5,report(store.database_path).examined>=61*60]
            elif kind in {'case','versions'}: checks.append(len(selected)==2)
            elif kind=='qualifiers': checks += [len(reviews)==1,len(selected)==0]
            elif kind=='partial': checks += [len(selected)==1,selected[0]['error_type']=='TypeError']
            elif kind=='dependency': checks.append(len(selected)==0)
            elif kind=='undo':
                review=reviews[0]
                resolved=store.resolve_conflict(review.id,ConflictResolution(version=review.version,resolution='prefer_source',
                    preferred_source_id=review.sources[0].id,note='Check intended behavior'))
                checks.append(len(refs(store))==1)
                store.reopen_conflict(review.id,resolved.version,'Reinspect changed assumptions')
                checks += [len(refs(store))==0,len(store.review_history(review.id))==2]
            results.append(dict(kind=kind,repetition=repetition,passed=all(checks),checks=checks))
    return results


def baseline_predictions(directory,cases,baseline):
    code=directory/'baseline-code';code.mkdir()
    with zipfile.ZipFile(baseline/'source.zip') as archive:
        if any(not (code/m.filename).resolve().is_relative_to(code.resolve()) for m in archive.infolist()): raise ValueError('Unsafe archive')
        archive.extractall(code)
    script=code/'pair_predictions.py'
    script.write_text('''import json,sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from backend.shared import Experience
from backend.experience.merging import build_preview
from backend.experience.store import _issue_key
cases=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
def entry(data):
 issue={k:v for k,v in data['issue'].items() if k not in {'symbols','depends_on'}}
 task=str(uuid4())
 return Experience(id=str(uuid4()),created_at=datetime.now(timezone.utc),schema_version=3,language=data['language'],scope='project',repository_id=data['repository_id'],
  source_revision=data['compatibility_key'],task_summary=issue['root_cause'],root_cause=issue['root_cause'],solution=issue['solution'],verification='Passed source tests',
  applicability=data['applicability'],required_capabilities=data['required_capabilities'],issues=[issue],source_task_id=task,
  evidence=[dict(source_task_id=task,tool_call_id=str(uuid4()),runner='pytest' if data['language']=='python' else 'maven',tests=3)])
rows=[]
for case in cases:
 a,b=entry(case['left']),entry(case['right'])
 if (a.language,a.repository_id,sorted(a.applicability),sorted(a.required_capabilities))!=(b.language,b.repository_id,sorted(b.applicability),sorted(b.required_capabilities)):
  prediction='unknown'
 else:
  plan=build_preview([a,b],sorted([a.id,b.id]),automatic=True)
  prediction='conflict' if not plan['can_merge'] else 'equivalent' if _issue_key(a.issues[0])==_issue_key(b.issues[0]) else 'compatible'
 rows.append(dict(id=case['id'],split=case['split'],label=case['label'],prediction=prediction))
Path(sys.argv[2]).write_text(json.dumps(rows),encoding='utf-8')
''',encoding='utf-8')
    write_json(directory/'pairs.json',cases)
    subprocess.run([sys.executable,str(script),str(directory/'pairs.json'),str(directory/'old-predictions.json')],cwd=code,check=True,timeout=120)
    return json.loads((directory/'old-predictions.json').read_text())


def embedding_ablation(semantic,cases):
    import numpy as np
    pairs=[v for v in cases if v['kind']=='paraphrase']
    # 100 paraphrases and 100 conflicting distractors. Exact target metadata is
    # deliberately absent from these queries: this measures text retrieval only.
    targets=[v['right'] for v in pairs]+[v['right'] for v in cases if v['kind']=='conflict']
    passages=semantic.embed([v['issue']['solution'] for v in targets])
    queries=semantic.embed([v['left']['issue']['solution'] for v in pairs],query=True)
    order=np.argsort(-(queries@passages.T),axis=1)
    return dict(queries=100,candidates=200,recall_at_1=float(np.mean([i in row[:1] for i,row in enumerate(order)])),
        recall_at_5=float(np.mean([i in row[:5] for i,row in enumerate(order)])),
        recall_at_10=float(np.mean([i in row[:10] for i,row in enumerate(order)])),
        caveat='Exact fixture identity recall; numbered near-duplicate targets make rank ties difficult. Similarity never authorizes merging.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'.cache/knowledge-system-experiment')
    parser.add_argument('--baseline',type=Path,default=ROOT/'.cache/remediation-baseline-20261114T105550Z')
    parser.add_argument('--sizes',type=int,nargs='+',default=[100,1000,10000]);args=parser.parse_args()
    directory=args.output.resolve();directory.mkdir(parents=True,exist_ok=False)
    result=dict(created_at=datetime.now(timezone.utc).isoformat(),sqlite=sqlite3.sqlite_version,capacity=[],groups=[])
    cases=corpus();old=baseline_predictions(directory,cases,args.baseline)
    result['old_pipeline']={split:metrics([v for v in old if v['split']==split]) for split in ['development','heldout']}
    semantic=LocalSemantics()
    if semantic.available: result['embedding_retrieval']=embedding_ablation(semantic,cases)
    source=json.loads((ROOT/'.cache/knowledge-semantic-experiment/report.json').read_text(encoding='utf-8'))
    result['semantic']={mode:{split:entry[split] for split in ['development','heldout']} for mode,entry in source['results'].items()}
    result['semantic']['deterministic_plus_embedding']=deepcopy(result['semantic']['deterministic'])
    result['ablation_note']='Embedding changes ranking only; the deterministic plus embedding arm deliberately makes the same relation decisions as deterministic.'
    result['groups']=group_checks(directory);write_json(directory/'report.json',result)
    print('Workflow groups:',sum(v['passed'] for v in result['groups']),'/ 40',flush=True)
    for size in args.sizes:
        result['capacity'].append(capacity(directory,size));write_json(directory/'report.json',result)
        print('Capacity:',result['capacity'][-1],flush=True)
    result['completed']=True;write_json(directory/'report.json',result)


if __name__=='__main__':main()
