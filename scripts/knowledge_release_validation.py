"""Validate the final code using frozen real traces, without any further paid model calls."""
from contextlib import closing
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time
import zipfile
import shutil
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.jobs import drain, report as maintenance_report
from backend.experience.retrieval import retrieve
from backend.experience.semantic import LocalSemantics
from scripts.experiment import file_hash,write_json
from scripts.knowledge_system_experiment import capacity,group_checks


def main():
    directory=ROOT/'.cache/knowledge-release-validation'
    replay_only='--replay-only' in sys.argv
    directory.mkdir(exist_ok=replay_only)
    previous=json.loads((directory/'report.json').read_text(encoding='utf-8')) if replay_only else None
    if previous:
        assert previous['completed'], 'The capacity run must finish before correcting the replay audit.'
        if not (directory/'source-before-audit-correction.zip').exists():
            shutil.copyfile(directory/'source.zip',directory/'source-before-audit-correction.zip')
    files=[p for folder in ['backend','scripts'] for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts]
    manifest={p.relative_to(ROOT).as_posix():file_hash(p) for p in files}
    with zipfile.ZipFile(directory/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in files: archive.write(path,path.relative_to(ROOT).as_posix())
    result=dict(source_sha256=manifest,source_archive_sha256=file_hash(directory/'source.zip'),python=platform.python_version(),
        platform=platform.system(),sqlite=sqlite3.sqlite_version,packages={name:importlib.metadata.version(name) for name in
        ['sentence-transformers','transformers','torch','tree-sitter','tree-sitter-java','tree-sitter-python','hypothesis','pydantic','fastapi']},
        capacity=[],completed=False)
    if previous:
        result=previous|{key:result[key] for key in ['source_sha256','source_archive_sha256','python','platform','sqlite','packages']}
        result['capacity_source_archive_sha256']=file_hash(directory/'source-before-audit-correction.zip')
    live=ROOT/'.cache/knowledge-live-experiment'
    live_report=json.loads((live/'report.json').read_text(encoding='utf-8'))
    assert live_report['completed'] and live_report['frozen_unchanged']
    if not replay_only:
        with closing(sqlite3.connect((live/'new-frozen.db').as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(directory/'replayed.db')) as target:
            source.backup(target)
    store=ExperienceStore(directory/'replayed.db');store.initialize()
    if not replay_only:
        with connect(store.database_path) as connection:
            connection.execute("UPDATE knowledge_jobs SET state='pending',cursor='',attempts=0,available_at=0")
            connection.execute("UPDATE knowledge_rules SET state='pending' WHERE state='ready'")
    semantic=LocalSemantics()
    state=maintenance_report(store.database_path)
    for _ in range(0 if replay_only else 100):
        state=drain(store.database_path,seconds=60,max_pages=100,semantic=semantic)
        if not state.pending_jobs: break
    assert not state.pending_jobs and not state.failed_jobs
    compared=[]
    for row in live_report['tasks']:
        if row['phase']!='evaluation' or row['arm']!='new':continue
        calls=[]
        events=row['audit']['retrieval_events']
        assert len(events)==len(row['reference_calls'])
        for index,call in enumerate(row['reference_calls']):
            context=deepcopy(call['context'])
            # The first live harness kept a mutable error_types list in context.
            # Agent's independent event audit captured a list snapshot at each call.
            context['error_types']=events[index]['error_types']
            refs=retrieve(store.database_path,call['query'],semantic=semantic,**context)
            before=json.dumps(call['references'],sort_keys=True,ensure_ascii=False)
            after=json.dumps(refs,sort_keys=True,ensure_ascii=False)
            calls.append(dict(identical=before==after,previous_ids=[v['rule_id'] for v in call['references']],current_ids=[v['rule_id'] for v in refs]))
        compared.append(dict(trial_id=row['trial_id'],calls=calls))
    result['replay']=dict(trials=len(compared),calls=sum(len(v['calls']) for v in compared),
        identical=sum(c['identical'] for v in compared for c in v['calls']),rows=compared,
        context_source='Agent retrieval_events: immutable per-call error type snapshots',
        original_frozen_unchanged=file_hash(live/'new-frozen.db')==live_report['new_frozen_sha256'],
        policy_version=semantic.version,maintenance=state.model_dump(mode='json'))
    write_json(directory/'report.json',result)
    print('Final-code replay',result['replay']['identical'],'/',result['replay']['calls'],flush=True)
    if replay_only:
        return
    result['groups']=group_checks(directory);write_json(directory/'report.json',result)
    print('Final-code groups',sum(v['passed'] for v in result['groups']),'/ 40',flush=True)
    for size in [100,1000,10000]:
        result['capacity'].append(capacity(directory,size));write_json(directory/'report.json',result)
        print('Final-code capacity',size,result['capacity'][-1]['retrieval_p95_ms'],flush=True)
    result['completed']=True;write_json(directory/'report.json',result)


if __name__=='__main__':main()
