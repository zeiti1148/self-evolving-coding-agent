"""Consolidate the unmodified outputs of two independently executed business tasks."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.jobs import drain
from backend.experience.knowledge import canonical
from backend.experience.principles import consolidate_principle, retrieve_principles
from backend.experience.relations import classify
from backend.experience.store import _insert
from backend.llm_configuration import api_identity
from backend.shared import Experience


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_variant(task, directory, variant, code, extra_tests=None):
    source=ROOT/'benchmarks/cross-business-merge'/task['case']['id']
    target=directory/(task['case']['id']+'-'+variant)
    target.mkdir(parents=True,exist_ok=False)
    shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    (target/task['case']['file']).write_text(code,encoding='utf-8')
    if extra_tests:
        (target/'tests/test_independent.py').write_text(extra_tests,encoding='utf-8')
    xml=target/'test-results.xml'
    process=subprocess.run([sys.executable,'-X','utf8','-m','pytest','-q','-p','no:cacheprovider','--junitxml='+str(xml)],
                           cwd=target,capture_output=True,timeout=60)
    suites=ET.parse(xml).getroot().findall('testsuite')
    counts={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
    return dict(variant=variant,exit_code=process.returncode,**counts,workspace=str(target),
                stdout=process.stdout.decode('utf-8',errors='replace'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--llm-env-file',type=Path,required=True)
    parser.add_argument('--expect-api-identity',required=True)
    args=parser.parse_args()
    settings=replace(Settings.from_env(llm_env_file=args.llm_env_file),llm_timeout=120)
    identity=api_identity(settings)
    if identity['identity']!=args.expect_api_identity:
        raise ValueError('API profile changed; no requests sent')
    directory=args.run.resolve()
    previous_report=json.loads((directory/'merge-report.json').read_text(encoding='utf-8')) if (directory/'merge-report.json').is_file() else None
    tasks=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    assert tasks['completed'] and len(tasks['tasks'])==2 and tasks['api_configuration']==identity
    records=[Experience.model_validate(data) for task in tasks['tasks'] for data in task['experiences']]
    assert len(records)==2 and len({r.repository_id for r in records})==2
    assert len({r.source_task_id for r in records})==2
    assert all(not r.get('request_experience_ids') for task in tasks['tasks'] for r in task['requests'])
    source_hashes={task['database']:sha(Path(task['database'])) for task in tasks['tasks']}
    store=ExperienceStore(directory/'shared-principles.db');store.initialize()
    with connect(store.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        for record in records:
            if not connection.execute('SELECT 1 FROM experiences WHERE id=?',(record.id,)).fetchone():
                _insert(connection,record)
    drain(store.database_path,semantic=None,seconds=10)
    with connect(store.database_path) as connection:
        data=[json.loads(r[0]) for r in connection.execute('SELECT data FROM knowledge_rules GROUP BY repository_id ORDER BY repository_id')]
    original_relation=classify(data[0],data[1])
    namespace='cross-business-demo'
    model=ChatClient(settings)
    model.set_deadline(time.monotonic()+300)
    try:
        result=consolidate_principle(store.database_path,[r.id for r in records],model,namespace=namespace)
    finally:
        model.close()
        audit=[*(previous_report or {}).get('merge_requests',[]),*model.audit]
        evidence=dict(tasks=tasks,api_configuration=identity,merge_requests=audit,database=str(store.database_path),
                      project_rule_relation=original_relation,result=locals().get('result'),completed=False)
        if locals().get('result',{}).get('reused') and previous_report:
            evidence['result']=previous_report['result']
            evidence['reused_existing_merge']=True
        (directory/'merge-report.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('MERGE',result['state'],flush=True)
    if result['state']!='succeeded':
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return
    patterns=retrieve_principles(store.database_path,namespace=namespace)
    assert len(patterns)==1 and len(patterns[0]['source_ids'])==2
    assert not retrieve_principles(store.database_path,namespace='another-namespace')
    repeated=consolidate_principle(store.database_path,[r.id for r in records],model,namespace=namespace)
    assert repeated['reused'] and repeated['id']==patterns[0]['id']
    assert all(store.get_experience(r.id)==r for r in records)
    assert all(sha(Path(path))==value for path,value in source_hashes.items())
    checks=[]
    verification=directory/('verification-'+str(time.time_ns()));verification.mkdir()
    for task in tasks['tasks']:
        if task['case']['id']=='invoice':
            hidden='from invoice import invoice_total\ndef test_independent_midpoint():\n assert invoice_total([{"unit_price":"1.005","quantity":1}])["total"]=="1.01"\ndef test_do_not_round_lines():\n assert invoice_total([{"unit_price":"0.125","quantity":2}])["total"]=="0.25"\n'
            wrong=task['after'].replace('Decimal("0.01")','Decimal("0.1")')
            call_args=([{'unit_price':'2.675','quantity':1}],)
            function='invoice_total'
        else:
            hidden='from grades import publish_grade\ndef test_independent_midpoint():\n assert publish_grade([{"score":"63.25","weight":"1"}])["mark"]=="63.3"\ndef test_do_not_round_assessments():\n assert publish_grade([{"score":"72.24","weight":"1"},{"score":"72.26","weight":"1"}])["mark"]=="72.3"\n'
            wrong=task['after'].replace('Decimal("0.1")','Decimal("0.01")')
            call_args=([{'score':'72.25','weight':'1'}],)
            function='publish_grade'
        assert wrong!=task['after']
        verified=verify_variant(task,verification,'correct',task['after'],hidden)
        swapped=verify_variant(task,verification,'wrong-business-precision',wrong,hidden)
        assert verified['exit_code']==0 and not any(verified[k] for k in ['failures','errors','skipped'])
        assert swapped['exit_code']==1 and swapped['failures']>0
        observed={}
        for name,code in [('before',task['before']),('after',task['after']),('wrong_precision',wrong)]:
            scope={};exec(compile(code,task['case']['file'],'exec'),scope)
            observed[name]=scope[function](*call_args)
        checks.append(dict(case=task['case']['id'],verification=verified,swapped_precision=swapped,
                           input=call_args,observed=observed))
    evidence.update(completed=True,principles=patterns,verification=checks,source_database_sha256=source_hashes,
                    raw_records_unchanged=True,idempotent=True,namespace_isolated=True,
                    limitation='New, deliberately faulty business fixtures with actual independent model repair and extraction. '
                    'Shared principles use model proposal/review, not a proof of universal behavioral equivalence. '
                    'No automatic publication or injection into other projects; target contracts and tests remain necessary.')
    (directory/'merge-report.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    output=ROOT/'docs/reports/knowledge-remediation-2026-11-14/cross-business-merge.json'
    output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(completed=True,principle=patterns[0]['principle'],report=str(output),
                         verification=[(r['case'],r['verification']['tests'],r['swapped_precision']['failures']) for r in checks]),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
