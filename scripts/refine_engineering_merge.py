"""Start independent repairs again with the failures discovered by evaluation.

The first attempt remains immutable. Previously held-out tests are now explicitly
regression tests, not claimed as unseen validation. No reference patch is read.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.demo_engineering_merge import CASES, RESEARCH, prepare
from backend.config import Settings
from backend.llm_configuration import api_identity


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--llm-env-file',type=Path,required=True)
    p.add_argument('--expect-api-identity',required=True)
    args=p.parse_args()
    previous=json.loads((args.previous/'report.json').read_text(encoding='utf-8'))
    settings=Settings.from_env(llm_env_file=args.llm_env_file)
    identity=api_identity(settings)
    if identity['identity']!=args.expect_api_identity:
        raise ValueError('API profile changed')
    # prepare() reads only the public issue metadata for each original case.
    # The separate source path overrides below point at the full original code
    # plus frozen new tests, never at model/reference solution trees.
    inputs=args.run.resolve().parent/(args.run.name+'-inputs')
    for row in previous['tasks']:
        name=row['case']['id']; original=Path(row['case']['source'])
        source=inputs/name
        if not source.exists():
            shutil.copytree(original,source,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.git'))
            destination=source/('testing/test_engineering_inheritance.py' if name=='pytest' else 'tests/test_engineering_inheritance.py')
            shutil.copyfile(ROOT/'benchmarks/engineering-merge'/(name+'_holdout.py'),destination)
        CASES[name]['source']=str(source)
        CASES[name]['evaluation_round']=2
        CASES[name]['promoted_holdouts_visible_to_model']=True
        CASES[name]['previous_attempt']=str(args.previous.resolve())
        if name=='pytest':
            CASES[name]['targets']=['testing/test_mark.py','testing/test_skipping.py','testing/python/collect.py','testing/test_engineering_inheritance.py']
        else:
            CASES[name]['environment_name']='sphinx39-env'
            CASES[name]['targets']=[f.relative_to(source).as_posix() for f in sorted((source/'tests').glob('test_ext_autodoc*.py'))]+['tests/test_engineering_inheritance.py']
            CASES[name]['deselect']=['tests/test_ext_autodoc.py::test_cython']
    prepare(args,settings,identity)


if __name__=='__main__':
    main()
