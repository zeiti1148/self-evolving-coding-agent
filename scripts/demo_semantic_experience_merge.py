"""Replay two actual task extractions through the production semantic worker.

Loads complete, unmodified historical records (including their IDs, original
evidence and dependencies). Uses only pinned local NLI/E5 models; no API calls,
paraphrase generation, manual relation inserts, or calibration changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.experience import ExperienceStore
from backend.experience.database import connect, read_only
from backend.experience.jobs import drain
from backend.experience.knowledge import canonical, provenance_page
from backend.experience.relations import classify, RELATION_POLICY_VERSION
from backend.experience.semantic import LocalSemantics
from backend.experience.store import COLUMNS, _insert, decode_row


SOURCES=[('training.db','a4861a4b-c256-4007-a48b-d1dcf134ab84'),
         ('evaluation-output.db','7733be83-1564-4be4-8339-e81d6717d341')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def original(saved,db,identifier):
    with read_only(saved/db) as connection:
        return decode_row(connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE id=?',(identifier,)).fetchone())


def rerun(case,trial,destination):
    source=Path(case['path'])
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.git'))
    before=(destination/'rules.py').read_text(encoding='utf-8')
    after=(Path(trial['workspace'])/'rules.py').read_text(encoding='utf-8')
    results=[]
    for stage in ['before','after']:
        if stage=='after':
            (destination/'rules.py').write_text(after,encoding='utf-8')
        xml=destination.parent/(destination.name+'-'+stage+'.xml')
        result=subprocess.run([sys.executable,'-X','utf8','-m','pytest','-q','-p','no:cacheprovider',
                               '--junitxml='+str(xml)],cwd=destination,capture_output=True,timeout=60)
        root=ET.parse(xml).getroot()
        suites=[root] if root.tag=='testsuite' else list(root.findall('testsuite'))
        counts={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
        results.append(dict(stage=stage,exit_code=result.returncode,**counts,
                            stdout=result.stdout.decode('utf-8',errors='replace'),report=str(xml)))
    assert results[0]['exit_code']==1 and results[1]['exit_code']==0
    assert results[1]['tests']==11 and not any(results[1][k] for k in ['failures','errors','skipped'])
    namespace={}
    exec(compile(after,str(destination/'rules.py'),'exec'),namespace)
    baseline={}
    exec(compile(before,str(source/'rules.py'),'exec'),baseline)
    probes=[]
    for value,expected in [(None,0),('',0),(' ',0),('bad',0),('-2',0),(' 3 ',3),('0',0)]:
        actual=namespace['quantity'](value)
        assert actual==expected
        try:
            previous=dict(value=baseline['quantity'](value),error=None)
        except (TypeError,ValueError) as exc:
            previous=dict(value=None,error=type(exc).__name__)
        probes.append(dict(input=value,expected=expected,actual=actual,before=previous))
    return dict(code_before=before,code_after=after,tests=results,behavior=probes)


def main():
    os.environ.update(KNOWLEDGE_MODELS_ENABLED='true',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    saved=ROOT/'.cache/knowledge-live-experiment'
    history=json.loads((saved/'report.json').read_text(encoding='utf-8'))
    originals=[original(saved,*source) for source in SOURCES]
    originals_json=[entry.model_dump(mode='json') for entry in originals]
    assert originals[0].source_task_id!=originals[1].source_task_id
    assert originals[0].issues[0].solution!=originals[1].issues[0].solution
    assert all(not entry.issues[0].depends_on for entry in originals)
    source_hashes={db:sha(saved/db) for db,_ in SOURCES}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6]
    directory=ROOT/'.cache/knowledge-semantic-merge-demo'/stamp
    directory.mkdir(parents=True,exist_ok=False)
    report=dict(kind='historical_independent_task_extractions_real_local_semantic_replay',
                created_at=datetime.now(timezone.utc).isoformat(),completed=False,new_model_api_calls=0,
                database=str(directory/'experiences.db'),policy=RELATION_POLICY_VERSION,
                original_model=history['model'],sources=[],snapshots=[],source_database_sha256=source_hashes)
    for index,entry in enumerate(originals):
        trial=next(t for t in history['tasks'] if t['task_id']==entry.source_task_id)
        case=next(c for c in history['catalog'] if c['id']==trial['case'])
        assert trial['visible_passed'] and trial['heldout_passed']
        replay=rerun(case,trial,directory/f'task-{index+1}')
        report['sources'].append(dict(database=SOURCES[index][0],original=originals_json[index],
            selected_issue_index=0,record_sha256=hashlib.sha256(canonical(originals_json[index]).encode()).hexdigest(),
            trial={k:trial[k] for k in ['trial_id','task_id','case','workspace','visible_passed','heldout_passed']},replay=replay))
    semantic=LocalSemantics()
    assert semantic.load(),semantic.error
    threshold=semantic.threshold('equivalent')
    assert threshold==.95, 'This demonstration must not tune or lower the existing threshold'
    report.update(model_version=semantic.version,manifest=semantic.manifest,threshold=threshold,
                  calibration_sha256=sha(semantic.directory/'calibration.json'))
    store=ExperienceStore(directory/'experiences.db');store.initialize()
    context=dict(language='python',repository_id='knowledge-python',compatibility_key='stable-v2',
                 error_types=['TypeError'],semantic=semantic)

    def snapshot(stage):
        with read_only(store.database_path) as connection:
            rules=[dict(r) for r in connection.execute('SELECT id,fingerprint,state,data FROM knowledge_rules ORDER BY id')]
            selected=[r['id'] for r in rules if json.loads(r['data'])['issue']['error_type']=='TypeError']
            jobs=[dict(r) for r in connection.execute('SELECT id,state,attempts,examined FROM knowledge_jobs ORDER BY id')]
            relations=[dict(r) for r in connection.execute('SELECT * FROM knowledge_relations ORDER BY left_rule,right_rule')]
            counts={table:connection.execute('SELECT count(*) FROM '+table).fetchone()[0]
                    for table in ['experiences','knowledge_rules','knowledge_evidence','knowledge_decisions']}
        refs=store.retrieve_model_references('quantity None int TypeError',**context)
        value=dict(stage=stage,counts=counts,selected_rule_ids=selected,rules=rules,jobs=jobs,relations=relations,references=refs)
        report['snapshots'].append(value)
        write_json(directory/'report.json',report)
        return value

    with connect(store.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _insert(connection,originals[0])
    drain(store.database_path,semantic=semantic,seconds=120)
    initial=snapshot('first_task_ready')
    assert len(initial['references'])==1 and initial['references'][0]['evidence_summary']['verified_runs']==1
    with connect(store.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _insert(connection,originals[1])
    pending=snapshot('second_task_ingested')
    assert len(pending['selected_rule_ids'])==2
    started=time.monotonic()
    outcome=drain(store.database_path,semantic=semantic,seconds=180)
    maintenance_seconds=time.monotonic()-started
    final=snapshot('semantic_maintenance_completed')
    assert outcome.pending_jobs==outcome.failed_jobs==0
    assert len(final['references'])==1
    reference=final['references'][0]
    assert set(reference['source_ids'])=={entry.id for entry in originals}
    assert reference['evidence_summary']['verified_runs']==2
    selected=set(final['selected_rule_ids'])
    relation=next(r for r in final['relations'] if {r['left_rule'],r['right_rule']}==selected)
    assert relation['kind']=='equivalent' and relation['status']=='pending'
    details=json.loads(relation['details'])
    assert all(details[name]['entailment']>=threshold for name in ['scores','condition_scores','action_scores'])
    assert final['counts']['knowledge_decisions']==0 and final['counts']['experiences']==2
    provenance=provenance_page(store.database_path,reference['rule_id'])
    assert set(provenance['source_ids'])=={entry.id for entry in originals}
    assert all(store.get_experience(e.id).model_dump(mode='json')==originals_json[i] for i,e in enumerate(originals))
    # Real counterexample: shared read/write constraints once made the old
    # full-context classifier incorrectly equate these opposite branches.
    audit=ROOT/'.cache/knowledge-semantic-real-audit'
    pairs=json.loads((audit/'candidates.json').read_text(encoding='utf-8'))
    before_results=json.loads((audit/'probe-results.json').read_text(encoding='utf-8'))
    controls=[]
    for index in [1,104]:
        left,right=pairs[index]
        prediction,control_details=classify(left['data'],right['data'],semantic)
        assert prediction=='unknown'
        controls.append(dict(index=index,left=left,right=right,before=before_results[index],
                             after=dict(prediction=prediction,details=control_details)))
    assert source_hashes=={db:sha(saved/db) for db,_ in SOURCES}
    assert report['calibration_sha256']==sha(semantic.directory/'calibration.json')
    report.update(completed=True,elapsed_maintenance_seconds=maintenance_seconds,
                  selected_relation=relation,decision_details=details,reference=reference,
                  provenance=provenance,controls=controls,originals_unchanged=True,
                  source_databases_unchanged=True,calibration_unchanged=True,
                  limitation='Two independently executed tasks from the small controlled repair benchmark; not unrelated production repositories. Full original records and evidence are preserved. Only the selected TypeError knowledge is consolidated into one reference; other rules are not claimed merged.')
    write_json(directory/'report.json',report)
    output=ROOT/'docs/reports/knowledge-remediation-2026-11-14/semantic-merge-example.json'
    write_json(output,report)
    print(json.dumps(dict(completed=True,report=str(output),database=str(store.database_path),
        scores=details,sources=reference['source_ids'],evidence=reference['evidence_summary'],
        maintenance_seconds=report['elapsed_maintenance_seconds']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
