"""Download pinned public model snapshots explicitly; runtime inference is local-only."""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PINNED_REVISIONS = {'embedding': '614241f622f53c4eeff9890bdc4f31cfecc418b3',
                    'nli': '8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'.cache/knowledge-models')
    parser.add_argument('--direct',action='store_true',help='Ignore proxy environment for this download process only.')
    args=parser.parse_args()
    if args.direct:
        for key in list(os.environ):
            if 'proxy' in key.lower():
                os.environ.pop(key)
    os.environ['HF_HUB_DISABLE_XET']='1'
    from huggingface_hub import snapshot_download
    from backend.experience.semantic import EMBEDDING_MODEL,NLI_MODEL
    args.directory.mkdir(parents=True,exist_ok=True)
    manifest={}
    for name,model in [('embedding',EMBEDDING_MODEL),('nli',NLI_MODEL)]:
        sha=PINNED_REVISIONS[name]
        destination=args.directory/name
        snapshot_download(model,revision=sha,local_dir=destination,
            allow_patterns=['*.json','*.txt','*.model','*.safetensors'],max_workers=2)
        manifest[name]={'model':model,'revision':sha,'path':str(destination.resolve())}
        (args.directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(name,sha,flush=True)
    from backend.experience.semantic import LocalSemantics
    engine=LocalSemantics(args.directory)
    if not engine.load():
        raise RuntimeError('Local model load failed: '+str(engine.error))
    print('local smoke',engine.infer('Return an empty string when input is null.','For null input return an empty string.'),flush=True)


if __name__=='__main__':
    main()
