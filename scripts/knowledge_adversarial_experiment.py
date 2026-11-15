"""Small hand-labelled challenge set; never used to tune the calibration threshold."""
from copy import deepcopy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.experience.relations import classify
from backend.experience.semantic import LocalSemantics
from scripts.knowledge_semantic_experiment import corpus,metrics
from scripts.experiment import write_json

CASES=[
 ('equivalent','If x is null, raise ValueError; otherwise return x unchanged.',
  'Return x unchanged unless it is null, in which case raise ValueError.'),
 ('equivalent','When x is missing return 0. When x is present return 1.',
  'Return 1 for a present x, and return 0 for a missing x.'),
 ('equivalent','Reject requests unless the caller is authenticated.',
  'Reject requests if the caller is not authenticated.'),
 ('equivalent','输入非空才允许保存；否则抛出异常。',
  '输入为空则抛出异常，不为空才允许保存。'),
 ('conflict','If x is not null return "yes"; otherwise return "no".',
  'If x is null return "yes"; otherwise return "no".'),
 ('conflict','Accept requests only if the caller is authenticated.',
  'Accept requests only if the caller is not authenticated.'),
 ('conflict','Retry only read operations. Never retry writes.',
  'Retry only write operations. Never retry reads.'),
 ('conflict','For every request, validate permissions before writing the file.',
  'For every request, write the file before validating permissions.'),
 ('conflict','缺失输入必须拒绝，正常输入必须接受。',
  '缺失输入必须接受，正常输入必须拒绝。'),
 ('conflict','Accept x exactly when x >= 10, otherwise reject x.',
  'Accept x exactly when x > 10, otherwise reject x.'),
 ('conflict','Reject x if x == 0; otherwise accept x.',
  'Reject x if x != 0; otherwise accept x.'),
 ('unknown','Prefer an available cache when a lookup succeeds.',
  'Always use the cache.'),
 ('unknown','Return a default if the input is missing.',
  'Throw an exception if the caller lacks permission.'),
 ('unknown','The behavior follows the project-specific protocol.',
  'Apply the caller-defined policy after validation.'),
 ('unknown','Use a fallback if the target business permits it.',
  'The target has not specified whether a fallback is permitted.'),
 ('unknown','Input handling follows this complete contract. '+('Preserve normal input. '*400),
  'Reject missing input.')
]


def main():
    semantic=LocalSemantics()
    base=corpus()[0]['left']
    rows=[]
    for index,(label,left,right) in enumerate(CASES):
        a,b=deepcopy(base),deepcopy(base)
        a['issue']['solution']=left;b['issue']['solution']=right
        prediction,details=classify(a,b,semantic)
        rows.append(dict(id=index,label=label,prediction=prediction,left=left,right=right,details=details))
    result=dict(label_source='Hand-authored behavioral contracts; unknown means insufficient shared conditions, not a proven relationship.',
        manifest=semantic.manifest,calibration=semantic.calibration,rows=rows,metrics=metrics(rows),
        release_gate_passed=not any(r['prediction'] in {'equivalent','conflict'} and r['prediction']!=r['label'] for r in rows))
    directory=ROOT/'.cache/knowledge-adversarial-experiment';directory.mkdir(exist_ok=True)
    write_json(directory/'report.json',result)
    print(json.dumps({'metrics':result['metrics'],'gate':result['release_gate_passed']},ensure_ascii=False))


if __name__=='__main__':main()
