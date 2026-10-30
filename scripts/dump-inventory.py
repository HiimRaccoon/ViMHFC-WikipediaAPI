"""Build a verified official viwiki metadata inventory; never starts pilots."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.config import load_config
from vimfc.domain import canonical
from vimfc.pipeline.dump_acquisition import acquire


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--target',type=int,choices=(60000,80000,100000),default=100000)
    args=parser.parse_args()
    context=json.loads(Path(args.context).read_text(encoding='utf-8-sig'))
    config=load_config(context['config'])
    from vimfc.domain import digest
    if digest(config)!=context['config_hash'] or context['snapshot']!='2026-10-25T03:00:00Z':
        parser.error('Source context config/snapshot mismatch')
    result=acquire(config,args.output_dir,target=args.target,
        progress=lambda e:print(canonical(e),file=sys.stderr,flush=True))
    print(canonical(result));return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
