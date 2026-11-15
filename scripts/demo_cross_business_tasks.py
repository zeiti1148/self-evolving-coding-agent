"""Two distinct business repairs with isolated real model conversations and stores.

Task fixtures are intentionally faulty, runnable demonstrations. They are not
production incidents. No task receives another task's experience or solution.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.llm_configuration import api_identity
from backend.projects import source_revision
from backend.shared import AgentRequest, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory


CASES=[dict(id='invoice',title='电商发票金额计算',file='invoice.py',
    task='修复电商发票 invoice_total。商品单价与优惠券为十进制字符串；先汇总单价乘数量，再减整单优惠券，最低为零，最后按财务四舍五入保留两位小数，中点向上。不能逐行提前舍入。保留 CNY 币种、商品行数与已有非法输入异常。当前部分总额少一分钱，例如单价 2.675 数量 1 应为 2.68。不得修改测试和构建文件。'),
    dict(id='academic-grades',title='教务加权成绩发布',file='grades.py',
    task='修复教务 publish_grade。成绩和权重由十进制字符串输入，按权重计算平均分，权重不要求总和为 1。学校规定最终平均分保留一位小数，恰好中点时进一位；使用最终发布分数判定及格线 60 与奖学金线 85。72.25 应发布为 72.3。成绩范围、权重校验、空成绩异常及返回字段保持现有契约。不得修改测试和构建文件。')]


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--llm-env-file',type=Path,required=True)
    parser.add_argument('--expect-api-identity',required=True)
    parser.add_argument('--resume',type=Path,help='Resume an existing run without repeating successful tasks.')
    args=parser.parse_args()
    settings=Settings.from_env(llm_env_file=args.llm_env_file)
    identity=api_identity(settings)
    if identity['identity']!=args.expect_api_identity:
        raise ValueError('The API profile changed; no request was sent')
    if not args.live:
        print(json.dumps(identity,ensure_ascii=False));return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6]
    directory=args.resume.resolve() if args.resume else ROOT/'.cache/cross-business-merge'/stamp
    if not args.resume:
        directory.mkdir(parents=True,exist_ok=False)
    settings=replace(settings,enable_knowledge_models=False,workspace_root=directory/'workspaces',llm_timeout=120,task_timeout=420)
    report=json.loads((directory/'report.json').read_text(encoding='utf-8')) if args.resume else dict(created_at=datetime.now(timezone.utc).isoformat(),api_configuration=identity,
                directory=str(directory),task_limit=2,completed=False,tasks=[],
                fixture_origin='New runnable business demonstrations with deliberate defects; not production incidents')
    assert report['api_configuration']==identity
    write(directory/'report.json',report)
    print('REPORT',directory/'report.json',flush=True)
    for case in CASES:
        previous=next((row for row in report['tasks'] if row['case']['id']==case['id']),None)
        if previous:
            if (previous.get('result',{}).get('repair_succeeded') and previous.get('result',{}).get('tests_passed')
                    and previous.get('experiences') and all(not event.get('request_experience_ids') for event in previous['requests'])):
                print(case['id'],'REUSING completed independent task',flush=True)
                continue
            raise ValueError('Existing task did not complete successfully; no automatic task retry')
        source=ROOT/'benchmarks/cross-business-merge'/case['id']
        baseline_inventory=inventory(source)
        manager=WorkspaceManager(settings.workspace_root,{case['id']:source})
        service=ToolService(manager,settings.maven_home,python_executable=settings.python_executable)
        task=str(uuid4());workspace=manager.create_workspace(source,task)
        baseline=service.execute_tool(workspace,ToolCall(id=str(uuid4()),name='run_tests'))
        assert not baseline.success and baseline.metadata['failures']>0
        store=ExperienceStore(directory/(case['id']+'.db'));store.initialize()
        model=ChatClient(settings)
        agent=Agent(model,service.execute_tool,store,use_experience=False,timeout_seconds=420)
        row=dict(case=case,task_id=task,workspace=str(workspace),database=str(store.database_path),
                 before=(source/case['file']).read_text(encoding='utf-8'),baseline=baseline.model_dump(mode='json'),
                 retrieval_enabled=False,started_at=datetime.now(timezone.utc).isoformat())
        report['tasks'].append(row);write(directory/'report.json',report)
        def progress(step):
            print(case['id'],step.sequence,step.call.name,flush=True)
        try:
            request=AgentRequest(task_id=task,workspace=workspace,language='python',max_steps=16,
                                 repository_id='cross-business/'+case['id'],experience_contract=case['id']+'-v1',
                                 source_revision=source_revision(source,baseline_inventory),task_description=case['task'])
            result=agent.run(request,on_step=progress)
            row.update(result=result.model_dump(mode='json'),audit=agent.last_run_audit,
                       requests=model.audit,after=(workspace/case['file']).read_text(encoding='utf-8'),
                       originals_unchanged=inventory(source)==baseline_inventory,
                       experiences=[e.model_dump(mode='json') for e in store.list_experiences(limit=100)])
            assert row['originals_unchanged']
            assert result.repair_succeeded and result.tests_passed and row['experiences']
            assert all(not event.get('request_experience_ids') for event in model.audit)
        finally:
            model.close();write(directory/'report.json',report)
        print(case['id'],'DONE',len(row['experiences']),'experiences',flush=True)
    report['completed']=True
    write(directory/'report.json',report)
    print('COMPLETE',directory/'report.json',flush=True)


if __name__=='__main__':
    main()
