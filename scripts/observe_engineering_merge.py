"""Capture concrete outputs from the actual upstream code for the presentation."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.tools.process import run_process

PYTEST_PROBE = '''import json
import pytest
from _pytest.mark.structures import get_unpacked_marks
@pytest.mark.xfail('root')
class Root: pass
@pytest.mark.xfail('left')
class Left(Root): pass
@pytest.mark.xfail('right')
class Right(Root): pass
@pytest.mark.xfail('leaf')
class Leaf(Left,Right): pass
result={'mro':[x.__name__ for x in Leaf.__mro__[:-1]],'marks':[x.args[0] for x in get_unpacked_marks(Leaf)],'module':pytest.__file__}
print('OBSERVATION='+json.dumps(result))
'''

SPHINX_PROBE = r'''import importlib.util,json,re
from pathlib import Path
spec=importlib.util.spec_from_file_location('frozen_sphinx_case',HOLDOUT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class Factory:
    def mktemp(self,name):
        root=Path(DESTINATION)/name;root.mkdir(parents=True,exist_ok=False);return root
gen=module.built.__wrapped__(Factory())
pages=next(gen)
rows=[]
for match in re.finditer(r'<dt id="upstream_derived.Leaf\.([^"]+)">.*?</dt>\s*<dd>(.*?)</dd>',pages['inherited'],re.S):
    rows.append({'attribute':match.group(1),'doc':re.sub('<[^>]+>','',match.group(2)).strip()})
result={'members':rows,'html':str(Path(DESTINATION)/'real-sphinx-mro'/'_build/html/inherited.html')}
try:next(gen)
except StopIteration:pass
print('OBSERVATION='+json.dumps(result))
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--first',type=Path,required=True)
    args=p.parse_args();run=args.run.resolve()
    final=json.loads((run/'report.json').read_text(encoding='utf-8'))
    first=json.loads((args.first/'report.json').read_text(encoding='utf-8'))
    if not final['completed']:
        raise ValueError('Complete repairs first')
    observations={}
    for row in final['tasks']:
        case=row['case'];name=case['id']
        initial=next(t for t in first['tasks'] if t['case']['id']==name)
        observations[name]={}
        for label,source in [('before',Path(case.get('validation_base_source',case['source']))),('first',Path(initial['workspace'])),('after',Path(row['workspace']))]:
            directory=run/'observations'/name/label;directory.mkdir(parents=True,exist_ok=False)
            script=directory/'probe.py'
            header='import sys\nsys.path.insert(0,'+ascii(str(source/case['import_root']))+')\n'
            code=PYTEST_PROBE if name=='pytest' else ('HOLDOUT='+ascii(str(ROOT/'benchmarks/engineering-merge/sphinx_holdout.py'))+'\nDESTINATION='+ascii(str(directory))+'\n'+SPHINX_PROBE)
            script.write_text(header+code,encoding='utf-8')
            output,meta,error=run_process([case['python'],'-I','-X','utf8',str(script)],source,90)
            (directory/'output.log').write_text(output,encoding='utf-8')
            if error:
                raise RuntimeError(output)
            result=json.loads(next(line.partition('OBSERVATION=')[2] for line in output.splitlines() if line.startswith('OBSERVATION=')))
            observations[name][label]=result
    (run/'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(observations,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
