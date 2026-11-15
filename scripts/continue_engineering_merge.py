"""Preserve a stopped task and continue it with an explicit diagnostic summary."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.demo_engineering_merge import CASES, prepare, write
from scripts.validate_engineering_merge import source_digest
from backend.config import Settings
from backend.llm_configuration import api_identity


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--llm-env-file',type=Path,required=True);p.add_argument('--expect-api-identity',required=True)
    args=p.parse_args()
    previous=json.loads((args.previous/'report.json').read_text(encoding='utf-8'))
    settings=Settings.from_env(llm_env_file=args.llm_env_file);identity=api_identity(settings)
    if identity['identity']!=args.expect_api_identity:raise ValueError('API identity changed')
    sphinx=next(t for t in previous['tasks'] if t['case']['id']=='sphinx')
    pytest=next(t for t in previous['tasks'] if t['case']['id']=='pytest')
    assert pytest['result']['repair_succeeded'] and not sphinx['result']['repair_succeeded']
    CASES.clear()
    CASES['sphinx']=dict(sphinx['case'],source=sphinx['workspace'],max_steps=75,
        validation_base_source=sphinx['case']['source'],
        previous_stopped_task=sphinx['task_id'],previous_stopped_report=str(args.previous.resolve()/'report.json'),
        repair_start_revision='continuation:'+source_digest(Path(sphinx['workspace'])),
        continuation_diagnosis='This is an explicitly documented continuation after a 45-decision attempt stopped. '
        'Member discovery found inherited attributes, but real HTML still omits them. Inspect downstream filtering '
        'and option parsing as well as member discovery: Documenter.filter_members and the default object cutoff '
        'of the inherited-members directive. Source-only instance attributes are not normal runtime class attributes. '
        'Preserve documented named-base exclusion, MRO priority, and all existing behavior. '
        'Remove temporary debug file logging from application source before final verification. '
        'You have no other repository experience and no reference solution patch.')
    prepare(args,settings,identity)
    path=args.run/'report.json';report=json.loads(path.read_text(encoding='utf-8'))
    report['tasks'].insert(0,pytest)
    report['continuation_of']=str(args.previous.resolve())
    write(path,report)


if __name__=='__main__':main()
