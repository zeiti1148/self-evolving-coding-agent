"""Reproducible contract-labelled semantic evaluation; no paid model requests."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.experience.knowledge import canonical,rule_text
from backend.experience.relations import classify
from backend.experience.semantic import LocalSemantics


TEMPLATES=[
 ('When {x} is null, return "guest".', 'Return "guest" if {x} is null.', 'When {x} is null, return "blocked".'),
 ('Reject {x} by raising ValueError for a null input.', 'For a null {x}, raise ValueError.', 'Accept a null {x} and return normally.'),
 ('Keep the original {x} unchanged for valid inputs.', 'For valid inputs, preserve {x} without modification.', 'For valid inputs, always replace {x} with "changed".'),
 ('当{x}为空值时，返回"guest"。', '{x}是空值的情况下，应返回"guest"。', '当{x}为空值时，返回"blocked"。'),
 ('如果{x}是空值，抛出ValueError异常。', '遇到空值{x}时，通过ValueError拒绝输入。', '遇到空值{x}时正常返回，不抛出任何异常。'),
 ('有效输入时保持{x}的原始内容不变。', '对于有效输入，保留{x}的原有值，不作修改。', '有效输入时将{x}一律替换为"changed"。'),
]


def corpus():
    cases=[]
    for kind in ['exact','paraphrase','compatible','conflict','different_scope','unknown']:
        for i in range(100):
            template=i%6
            x=('name','address','title','nickname','description','label')[i%6]+str(i//6)
            source,paraphrase,contradiction=[text.format(x=x) for text in TEMPLATES[template]]
            data=dict(language='python' if i%2 else 'java',scope='project',repository_id='semantic-fixtures',
                compatibility_key='contract-v1',applicability=['登记的输入处理契约'],constraints=[],required_capabilities=[],
                issue=dict(error_type='ValueError' if i%2 else 'IllegalArgumentException',root_cause='输入边界需要处理。',
                           solution=source,files=[f'case{i}.py' if i%2 else f'Case{i}.java'],symbols=[f'case{i}#handle']))
            right=deepcopy(data)
            label='equivalent' if kind in {'exact','paraphrase'} else kind
            if kind=='paraphrase':
                right['issue']['solution']=paraphrase
            elif kind=='conflict':
                right['issue']['solution']=contradiction
            elif kind=='compatible':
                right['issue']['symbols']=[f'case{i}#other']
                right['issue']['solution']=contradiction
            elif kind=='different_scope':
                right['compatibility_key']='contract-v2'
                right['issue']['solution']=contradiction
            elif kind=='unknown':
                right['issue']['solution']='行为由调用方尚未登记的业务条件决定。'
                right['applicability']=['业务条件未知']
            cases.append(dict(id=f'{kind}-{i}',family=f'template-{template}',split='development' if template<3 else 'heldout',
                              kind=kind,label=label,left=data,right=right,
                              label_source='generated_contract',oracle=dict(same_behavior=kind in {'exact','paraphrase'},
                              same_target=kind!='compatible',same_version=kind!='different_scope')))
    return cases


def metrics(rows):
    total=len(rows)
    def count(label,pred):
        return sum(r['label']==label and r['prediction']==pred for r in rows)
    values={'count':total,'accuracy':sum(r['label']==r['prediction'] for r in rows)/max(1,total),
            'unknown_rate':sum(r['prediction']=='unknown' for r in rows)/max(1,total),
            'false_merges':sum(r['prediction']=='equivalent' and r['label']!='equivalent' for r in rows),
            'missed_conflicts':sum(r['label']=='conflict' and r['prediction']!='conflict' for r in rows),
            'false_conflicts':sum(r['prediction']=='conflict' and r['label']!='conflict' for r in rows)}
    for label in ['equivalent','conflict']:
        predicted=sum(r['prediction']==label for r in rows)
        truth=sum(r['label']==label for r in rows)
        values[label]={'precision':count(label,label)/predicted if predicted else None,
                       'recall':count(label,label)/truth if truth else None,'decisions':predicted}
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.cache/knowledge-semantic-experiment')
    parser.add_argument('--models',type=Path,default=ROOT/'.cache/knowledge-models')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cases=corpus();(args.output/'corpus.json').write_text(canonical(cases),encoding='utf-8')
    semantic=LocalSemantics(args.models)
    if not semantic.load():
        raise RuntimeError('Local models unavailable: '+str(semantic.error))
    started=time.monotonic();scores={}
    score_path=args.output/'scores.json'
    if score_path.exists():
        old=json.loads(score_path.read_text(encoding='utf-8'))
        if old.get('manifest')==semantic.manifest:
            scores=old['scores']
    for i,case in enumerate(cases):
        if case['kind'] not in {'exact','compatible','different_scope'} and case['id'] not in scores:
            scores[case['id']]=semantic.infer(rule_text(case['left']),rule_text(case['right']))
        if i%20==0 or i==len(cases)-1:
            score_path.write_text(json.dumps({'manifest':semantic.manifest,'scores':scores}),encoding='utf-8')
            print('scored',i+1,'/',len(cases),flush=True)
    # Reuse measured probabilities but execute the real hard-condition policy.
    original_infer=semantic.infer
    cached={(rule_text(c['left']),rule_text(c['right'])):scores[c['id']] for c in cases if c['id'] in scores}
    semantic.infer=lambda a,b:cached[(a,b)] if (a,b) in cached else original_infer(a,b)
    calibration={}
    development=[c for c in cases if c['split']=='development']
    for relation in ['equivalent','conflict']:
        candidates=[]
        for threshold in [.95,.97,.99,.995]:
            semantic.calibration={k:dict(enabled=True,threshold=threshold,model_manifest=semantic.manifest)
                                  for k in ['equivalent','conflict']}
            rows=[dict(label=c['label'],prediction=classify(c['left'],c['right'],semantic)[0]) for c in development if c['kind']!='exact']
            value=metrics(rows)[relation]
            candidates.append(dict(threshold=threshold,**value))
        qualified=[v for v in candidates if v['decisions']>=30 and (v['precision'] or 0)>=.99]
        chosen=min(qualified,key=lambda v:v['threshold']) if qualified else None
        calibration[relation]=dict(enabled=chosen is not None,threshold=chosen['threshold'] if chosen else None,
                                   model_manifest=semantic.manifest,development=candidates)
    semantic.calibration=calibration
    results={}
    for mode in ['deterministic','local_nli']:
        rows=[]
        for case in cases:
            prediction,details=classify(case['left'],case['right'],semantic if mode=='local_nli' else None)
            rows.append(dict(id=case['id'],split=case['split'],kind=case['kind'],label=case['label'],prediction=prediction,details=details))
        results[mode]=dict(development=metrics([r for r in rows if r['split']=='development']),
                           heldout=metrics([r for r in rows if r['split']=='heldout']),rows=rows)
    # Heldout is a release gate, not another threshold tuning set.
    held=results['local_nli']['heldout']
    for relation in ['equivalent','conflict']:
        value=held[relation]
        calibration[relation]['heldout']=value
        if value['decisions']<30 or (value['precision'] or 0)<.99:
            calibration[relation]['enabled']=False
            calibration[relation]['gate_reason']='heldout precision or coverage requirement not met'
    from scripts.knowledge_adversarial_experiment import CASES
    challenge_rows=[]
    for index,(label,left_text,right_text) in enumerate(CASES):
        left,right=deepcopy(cases[0]['left']),deepcopy(cases[0]['right'])
        left['issue']['solution']=left_text;right['issue']['solution']=right_text
        prediction,details=classify(left,right,semantic)
        challenge_rows.append(dict(id=index,label=label,prediction=prediction,details=details))
    for relation in ['equivalent','conflict']:
        mistakes=[r['id'] for r in challenge_rows if r['prediction']==relation and r['label']!=relation]
        calibration[relation]['challenge_gate']={'passed':not mistakes,'false_decision_ids':mistakes,'cases':len(CASES)}
        if mistakes:
            calibration[relation]['enabled']=False
            calibration[relation]['gate_reason']='adversarial contract release gate failed'
    (args.models/'calibration.json').write_text(json.dumps(calibration,indent=2,ensure_ascii=False),encoding='utf-8')
    report=dict(corpus_sha256=hashlib.sha256(canonical(cases).encode()).hexdigest(),manifest=semantic.manifest,
        elapsed_seconds=time.monotonic()-started,calibration=calibration,results=results,challenge=metrics(challenge_rows),
        limitation='Controlled contract-generated fixtures; not an estimate of production accuracy. Development uses English templates; heldout uses distinct Chinese templates.')
    (args.output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:{'development':v['development'],'heldout':v['heldout']} for k,v in results.items()},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
