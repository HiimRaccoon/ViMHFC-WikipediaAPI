"""One explicitly authorized archived Pilot100/1000, with hard integrity/budget stop."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.domain import digest,Incompatible
from vimfc.config import load_config
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.reviewed_pilot import run_reviewed


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context',required=True);p.add_argument('--size',type=int,choices=(100,1000),required=True)
    p.add_argument('--max-requests',type=int,required=True);p.add_argument('--max-wait-seconds',type=int,default=60)
    p.add_argument('--retry-rounds',type=int,default=2)
    p.add_argument('--reviewed-quarantine',help='Explicit reviewed source refusal; never disables integrity validation')
    args=p.parse_args();c=json.loads(Path(args.context).read_text(encoding='utf-8-sig'))
    with PipelineStore(c['database']) as store:
        run=store.run(c['run_id'])
        if run['config_hash']!=c['config_hash'] or digest(load_config(c['config']))!=c['config_hash'] or run['snapshot']!=c['snapshot']:
            raise Incompatible('Immutable run context mismatch')
        rows=store.candidates(c['run_id'])
        if digest(rows)!=c['cohort_hash']:raise Incompatible('Archived candidate content/order changed')
        result=run_reviewed(store,c['run_id'],c['raw_cache'],args.size,reports=c['reports'],max_requests=args.max_requests,
            max_wait_seconds=args.max_wait_seconds,retry_rounds=args.retry_rounds,reviewed_quarantine=args.reviewed_quarantine)
    print(json.dumps({k:result[k] for k in ('run_id','size','completed','data_complete','pilot_accepted','stopped','failure','source_http_attempts','exit_code','receipt')},ensure_ascii=False))
    return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
