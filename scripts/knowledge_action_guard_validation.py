"""Evaluate the action guard with existing frozen calibration; never tune it."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.experience.relations import classify, RELATION_POLICY_VERSION
from backend.experience.semantic import LocalSemantics
from scripts.knowledge_semantic_experiment import corpus, metrics
from scripts.knowledge_adversarial_experiment import CASES


def main():
    os.environ.update(KNOWLEDGE_MODELS_ENABLED='true',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    model=LocalSemantics()
    assert model.load(),model.error
    calibration=(model.directory/'calibration.json').read_bytes()
    cases=corpus()
    rows=[]
    for case in cases:
        prediction,details=classify(case['left'],case['right'],model)
        rows.append({key:case[key] for key in ['id','kind','split','label']} |
                    dict(prediction=prediction,details=details))
        if len(rows)%100==0:
            print('Semantic cases completed:',len(rows),flush=True)
    challenges=[]
    for index,(label,left_text,right_text) in enumerate(CASES):
        left=deepcopy(cases[0]['left']);right=deepcopy(left)
        left['issue']['solution']=left_text;right['issue']['solution']=right_text
        prediction,details=classify(left,right,model)
        challenges.append(dict(id=index,label=label,prediction=prediction,details=details))
    assert (model.directory/'calibration.json').read_bytes()==calibration
    report=dict(policy=RELATION_POLICY_VERSION,model_version=model.version,calibration_unchanged=True,
                overall=metrics(rows),heldout=metrics([row for row in rows if row['split']=='heldout']),
                challenge=metrics(challenges),rows=rows,challenge_rows=challenges)
    output=ROOT/'docs/reports/knowledge-remediation-2026-11-14/semantic-action-guard-validation.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ['overall','heldout','challenge']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
