"""After repair, compare original, model, and upstream reference on real tests.

Only this post-repair evaluator reads the reference patch. No gold patch or
held-out tests are placed in a repair workspace or passed to the repair model.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.tools.process import run_process
from backend.tools.workspace import inventory


def source_digest(source):
    return hashlib.sha256(json.dumps(inventory(source),sort_keys=True).encode()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def evaluate(case, source, selection, directory, label):
    directory.mkdir(parents=True,exist_ok=True)
    junit=directory/(label+'.xml')
    script=("import sys;sys.path.insert(0,"+ascii(str((source/case['import_root']).resolve()))+");"
            "import "+case['package']+" as p;print('SOURCE_IMPORT',p.__file__);"
            "import pytest;sys.exit(pytest.main(sys.argv[1:]))")
    argv=[case['python'],'-I','-X','utf8','-c',script,'-q','-p','no:cacheprovider','-o','addopts=',
          '--junitxml='+str(junit)]
    for plugin in case['plugins']:
        argv.extend(['-p',plugin])
    argv.extend(selection)
    output,metadata,code=run_process(argv,source,300,limit=200_000)
    log=directory/(label+'.log');log.write_text(output,encoding='utf-8')
    counts=dict(tests=0,failures=0,errors=0,skipped=0)
    cases=[]
    if junit.exists():
        root=ET.parse(junit).getroot()
        for suite in root.findall('testsuite'):
            for key in counts:
                counts[key]+=int(suite.attrib.get(key,0))
            for test in suite.findall('testcase'):
                problem=next(iter(list(test)),None)
                state=problem.tag if problem is not None and problem.tag in {'failure','error','skipped'} else 'passed'
                cases.append(dict(name=test.attrib.get('classname','')+'.'+test.attrib.get('name',''),
                                  status=state,detail=(problem.attrib.get('message','') if problem is not None else '')))
    result=dict(label=label,source=str(source),argv=argv,**counts,
                source_snapshot=source_digest(source),
                passed=counts['tests']-counts['failures']-counts['errors']-counts['skipped'],
                execution=metadata,log=str(log),junit=str(junit),cases=cases)
    print(case['id'],label,json.dumps({k:result[k] for k in ['tests','passed','failures','errors','skipped']}),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    run=args.run.resolve()
    report=json.loads((run/'report.json').read_text(encoding='utf-8'))
    if not report['completed']:
        raise ValueError('Both repairs must finish before the evaluator reads gold patches')
    path=run/'validation.json'
    results=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(tasks=[],
        holdouts_visible_to_model=False,reference_patch_visible_to_model=False,official_docker_score=False)
    dataset=json.loads((ROOT/'.cache/engineering-repair-research/verified.json').read_text(encoding='utf-8'))
    for row in report['tasks']:
        case=row['case']
        if any(t['case']==case['id'] for t in results['tasks']):
            continue
        source=Path(case.get('validation_base_source',case['source']))
        work=Path(row['workspace'])
        reference=run/'evaluation'/case['id']/'reference'
        if not reference.exists():
            shutil.copytree(source,reference,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.git'))
            patch=next(d['patch'] for d in dataset if d['instance_id']==case['instance'])
            patchfile=reference.parent/'upstream-reference.patch'
            patchfile.write_text(patch,encoding='utf-8',newline='\n')
            subprocess.run(['git','apply','--check',str(patchfile)],cwd=reference,check=True,
                           env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(reference.parent)))
            subprocess.run(['git','apply',str(patchfile)],cwd=reference,check=True,
                           env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(reference.parent)))
        suffix = '_fresh_holdout.py' if case.get('evaluation_round') == 2 else '_holdout.py'
        holdout=ROOT/'benchmarks/engineering-merge'/(case['id']+suffix)
        if case['id']=='pytest':
            broad=['testing/test_mark.py','testing/test_skipping.py','testing/python/collect.py']
        else:
            broad=[p.relative_to(source).as_posix() for p in sorted((source/'tests').glob('test_ext_autodoc*.py'))]
        if case.get('evaluation_round') == 2:
            broad.append('testing/test_engineering_inheritance.py' if case['id']=='pytest' else 'tests/test_engineering_inheritance.py')
        task=dict(case=case['id'],holdout_sha256=hashlib.sha256(holdout.read_bytes()).hexdigest(),
                  component_selection=broad,checks=[])
        results['tasks'].append(task)
        save(path,results)
        for variant,location in [('before',source),('model',work),('reference',reference)]:
            for suite,selection in [('component',broad),('holdout',[str(holdout)])]:
                task['checks'].append(evaluate(case,location,selection,run/'evaluation'/case['id'],variant+'-'+suite))
                save(path,results)
    results['completed']=True
    save(path,results)


if __name__=='__main__':
    main()
