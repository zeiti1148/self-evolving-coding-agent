"""108 bounded real repairs: 12 training + 8 cases x 3 frozen-memory arms x 4 repeats.

Explicit --live is required. No whole-task retries, evaluation feedback, or API secrets
in artifacts. All arms use the same current fixer and provider configuration.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timezone
import json
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.knowledge_benchmarks import generate
from scripts.experiment import FrozenSearchStore, file_hash, freeze_database, request_metrics, write_json
from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.llm_configuration import api_identity
from backend.experience import ExperienceStore
from backend.projects import source_revision
from backend.shared import AgentRequest, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory, copy_ignored
# Load the policy before capturing source identity, rather than lazily during evaluation.
from backend.experience import grounding, knowledge, relations, retrieval, semantic as semantic_module


class Memory:
    def __init__(self, frozen, output, arm, lock, semantic=None):
        self.arm=arm
        self.store=FrozenSearchStore(frozen,output) if frozen else output
        self.calls=[]
        self.lock=lock
        self.semantic=semantic

    def save_experiences(self,*args,**kwargs): return self.store.save_experiences(*args,**kwargs)
    def save_experience(self,*args,**kwargs): return self.store.save_experience(*args,**kwargs)

    def retrieve_model_references(self,query='',**context):
        with self.lock:
            if self.arm=='old':
                from backend.experience.policy import to_model_reference
                context.pop('compatibility_key',None);context.pop('source_revision',None)
                refs=[to_model_reference(v) for v in self.store.retrieve_experiences('',**context)]
                for ref in refs:
                    ref.pop('constraints',None);ref.pop('compatibility_key',None)
                    for issue in ref.get('issues',[]):
                        issue.pop('symbols',None);issue.pop('depends_on',None)
            else:
                refs=self.store.retrieve_model_references(query,semantic=self.semantic,**context)
        self.calls.append(dict(query=query,context=deepcopy(context),references=deepcopy(refs),characters=len(json.dumps(refs,ensure_ascii=False))))
        return refs


def old_database(baseline, raw, destination, directory):
    code=directory/'baseline-code'
    code.mkdir()
    with zipfile.ZipFile(baseline/'source.zip') as archive:
        for member in archive.infolist():
            target=(code/member.filename).resolve()
            if not target.is_relative_to(code.resolve()):
                raise ValueError('Unsafe archive member')
        archive.extractall(code)
    worker=code/'build_frozen.py'
    worker.write_text('''import json,sys
from pathlib import Path
from backend.shared import Experience
from backend.experience.database import connect
from backend.experience.store import _insert
from backend.experience.consolidation import consolidate
rows=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
with connect(Path(sys.argv[2])) as connection:
 connection.execute('BEGIN IMMEDIATE')
 for data in rows:
  data['schema_version']=min(data['schema_version'],3)
  data.pop('compatibility_key',None);data.pop('constraints',None)
  for issue in data['issues']:
   issue.pop('symbols',None);issue.pop('depends_on',None)
  item=Experience.model_validate(data)
  _insert(connection,item)
  consolidate(connection,seed_ids=[item.id])
print('Old frozen pipeline completed')
''',encoding='utf-8')
    result=subprocess.run([sys.executable,str(worker),str(raw),str(destination)],cwd=code,capture_output=True,text=True,timeout=180)
    if result.returncode:
        raise RuntimeError('Old frozen pipeline failed: '+result.stderr[-2500:])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--inspect-api',action='store_true',help='Print redacted configuration identity; no network requests.')
    parser.add_argument('--llm-env-file',type=Path,help='Explicit project-specific API configuration file.')
    parser.add_argument('--expect-api-identity',help='Identity shown by --inspect-api; reject changed or unintended credentials.')
    parser.add_argument('--workers',type=int,choices=[1,2,3,4],default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'.cache/knowledge-live-experiment')
    parser.add_argument('--baseline',type=Path,default=ROOT/'.cache/remediation-baseline-20261114T105550Z')
    args=parser.parse_args()
    if args.inspect_api:
        print(json.dumps(api_identity(Settings.from_env(llm_env_file=args.llm_env_file)),ensure_ascii=False,indent=2))
        return
    if not args.live:
        print('Plan: 12 training + 8 evaluation x 3 arms x 4 repeats = 108 real tasks; 20 decisions / 600 seconds each. Use --live.')
        return
    if args.llm_env_file is None or not args.expect_api_identity:
        parser.error('--live requires --llm-env-file and --expect-api-identity; inspect the configuration first with --inspect-api.')
    configured=Settings.from_env(llm_env_file=args.llm_env_file)
    identity=api_identity(configured)
    if identity['identity'] != args.expect_api_identity:
        parser.error('API configuration identity differs from the inspected profile; no model requests were submitted.')
    if not configured.llm_base_url or not configured.llm_api_key or not configured.llm_model:
        parser.error('The selected API configuration must contain its own URL, key and model.')
    directory=args.output.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    catalog=generate(directory/'fixtures',ROOT)
    cases={v['id']:v for v in catalog}
    settings=replace(configured,task_timeout=600,enable_knowledge_models=False,
        workspace_root=directory/'workspaces',projects={v['id']:Path(v['path']) for v in catalog},project_definitions={})
    if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
        raise RuntimeError('Configured real model credentials are required.')
    manager=WorkspaceManager(settings.workspace_root,settings.projects)
    service=ToolService(manager,settings.maven_home,python_executable=settings.python_executable)
    originals={key:inventory(path) for key,path in settings.projects.items()}
    source_files=[p for parent in ['backend','scripts'] for p in (ROOT/parent).rglob('*.py') if '__pycache__' not in p.parts]
    with zipfile.ZipFile(directory/'current-source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in source_files: archive.write(path,path.relative_to(ROOT).as_posix())
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),model=settings.llm_model,api_configuration=identity,workers=args.workers,
        task_limit=108,decision_limit=20,timeout_seconds=600,completed=False,tasks=[],baselines={},
        catalog=catalog,originals=originals,source_hashes={p.relative_to(ROOT).as_posix():file_hash(p) for p in source_files},
        source_archive_sha256=file_hash(directory/'current-source.zip'))
    write_json(directory/'report.json',report)
    (directory/'tasks').mkdir()
    training=ExperienceStore(directory/'training.db');training.initialize()
    output=ExperienceStore(directory/'evaluation-output.db');output.initialize()
    retrieval_lock=threading.Lock()
    frozen_semantic=None
    for case in catalog:
        task=str(uuid4());workspace=manager.create_workspace(Path(case['path']),task)
        result=service.execute_tool(workspace,ToolCall(id=str(uuid4()),name='run_tests'))
        valid=result.error_code=='TEST_FAILED' and result.metadata.get('tests')==case['expected_tests'] and result.metadata.get('skipped')==0
        report['baselines'][case['id']]=dict(valid=valid,result=result.model_dump(mode='json'))
        write_json(directory/'report.json',report)
        print('Preflight',case['id'],valid,flush=True)
        if not valid: raise RuntimeError('Fixture preflight failed; no model task was submitted.')

    def execute(spec):
        case=cases[spec['case']];started=time.monotonic();task=str(uuid4())
        workspace=manager.create_workspace(Path(case['path']),task)
        frozen=directory/(spec['arm']+'-frozen.db') if spec['phase']=='evaluation' and spec['arm']!='none' else None
        memory=Memory(frozen,training if spec['phase']=='training' else output,spec['arm'],retrieval_lock,frozen_semantic)
        model=ChatClient(settings)
        agent=Agent(model,service.execute_tool,memory,use_experience=spec['arm']!='none',timeout_seconds=600)
        row=dict(spec,task_id=task,workspace=str(workspace),outcome='running')
        path=directory/'tasks'/(spec['trial_id']+'.json')
        write_json(path,row)
        def progress(step):
            row['last_tool']=step.call.name;row['tool_steps']=step.sequence
            row['elapsed_seconds']=round(time.monotonic()-started,2)
            write_json(path,row)
        try:
            request=AgentRequest(task_id=task,workspace=workspace,max_steps=20,language=case['language'],
                repository_id=case['repository_id'],experience_contract=case['experience_contract'],
                source_revision=source_revision(Path(case['path']),originals[case['id']]),task_description=case['task_description'])
            result=agent.run(request,on_step=progress)
            row.update(result=result.model_dump(mode='json'),audit=agent.last_run_audit,reference_calls=memory.calls)
            row['metrics']=request_metrics(agent.last_run_audit)
            row['visible_passed']=result.repair_succeeded and result.tests_passed is True
            # Independent heldout test suite is never exposed in the repair workspace.
            hidden_source=directory/'heldout-source'/spec['trial_id']
            shutil.copytree(Path(case['path']),hidden_source,ignore=copy_ignored)
            for changed in result.changed_files:
                target=(hidden_source/changed).resolve()
                if not target.is_relative_to(hidden_source.resolve()): raise ValueError('Invalid changed path')
                if (workspace/changed).is_file():
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(workspace/changed,target)
            shutil.copyfile(case['hidden'],hidden_source/case['test'])
            hidden_manager=WorkspaceManager(directory/'heldout-workspaces',{spec['trial_id']:hidden_source})
            hidden_service=ToolService(hidden_manager,settings.maven_home,python_executable=settings.python_executable)
            hidden_workspace=hidden_manager.create_workspace(hidden_source,str(uuid4()))
            hidden=hidden_service.execute_tool(hidden_workspace,ToolCall(id=str(uuid4()),name='run_tests'))
            row['heldout_verification']=hidden.model_dump(mode='json')
            row['heldout_passed']=hidden.success and hidden.metadata.get('tests')==case['hidden_tests'] and hidden.metadata.get('skipped')==0
            row['outcome']='passed' if row['visible_passed'] and row['heldout_passed'] else result.status if not row['visible_passed'] else 'heldout_failed'
        except Exception as exc:
            row['outcome']='infrastructure_error';row['error_type']=type(exc).__name__
        finally:
            model.close()
            row['elapsed_seconds']=round(time.monotonic()-started,3)
            row['original_unchanged']=inventory(Path(case['path']))==originals[case['id']]
            write_json(path,row)
        return row

    def run_phase(plan):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures=[pool.submit(execute,spec) for spec in plan]
            for future in as_completed(futures):
                row=future.result();report['tasks'].append(row)
                write_json(directory/'report.json',report)
                print(len(report['tasks']),'/ 108',row['trial_id'],row['outcome'],row['elapsed_seconds'],flush=True)

    run_phase([dict(phase='training',case=c['id'],arm='none',repetition=1,trial_id=c['id']) for c in catalog if c['phase']=='training'])
    raw=[entry.model_dump(mode='json') for entry in training.list_experiences(limit=1000)]
    raw.sort(key=lambda v:(v['created_at'],v['id']))
    write_json(directory/'training-raw.json',raw)
    from backend.experience.jobs import drain
    from backend.experience.semantic import LocalSemantics
    semantic=LocalSemantics()
    frozen_semantic=semantic
    report['semantic_manifest']=deepcopy(semantic.manifest)
    report['semantic_calibration']=deepcopy(semantic.calibration)
    for _ in range(100):
        state=drain(training.database_path,seconds=60,max_pages=100,semantic=semantic if semantic.available else None)
        print('Consolidating training',state.pending_jobs,state.unknown_pairs,flush=True)
        if not state.pending_jobs: break
    if state.pending_jobs or state.failed_jobs: raise RuntimeError('Training maintenance incomplete; evaluation was not started.')
    report['training_maintenance']=state.model_dump(mode='json')
    report['new_frozen_sha256']=freeze_database(training.database_path,directory/'new-frozen.db')
    old_database(args.baseline.resolve(),directory/'training-raw.json',directory/'old-frozen.db',directory)
    report['old_frozen_sha256']=file_hash(directory/'old-frozen.db')
    report['training_records']=len(raw)
    write_json(directory/'report.json',report)
    evaluation=[c for c in catalog if c['phase']=='evaluation']
    plan=[]
    for repetition in range(1,5):
        rng=random.Random(731+repetition);order=evaluation.copy();rng.shuffle(order)
        for index,case in enumerate(order):
            arms=['none','old','new'];rotation=(index+repetition)%3;arms=arms[rotation:]+arms[:rotation]
            for arm in arms:
                plan.append(dict(phase='evaluation',case=case['id'],arm=arm,repetition=repetition,
                    trial_id=f"{case['id']}-{arm}-r{repetition}"))
    report['evaluation_plan']=plan;write_json(directory/'report.json',report)
    run_phase(plan)
    report['frozen_unchanged']=all(file_hash(directory/(arm+'-frozen.db'))==report[arm+'_frozen_sha256'] for arm in ['old','new'])
    report['completed']=len(report['tasks'])==108
    write_json(directory/'report.json',report)
    print('Completed',report['completed'],'Frozen unchanged',report['frozen_unchanged'],flush=True)


if __name__=='__main__': main()
