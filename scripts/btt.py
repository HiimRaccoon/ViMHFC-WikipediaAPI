"""BTT forest collection; one explicitly submitted allocation owns the campaign."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from fast20k import environment,install_signals,model_service
from vimfc.domain import canonical
from vimfc.pipeline.fast20k import load,run,restore
from vimfc.pipeline.btt import prepare
from vimfc.pipeline.fast20k_corpus import publish
from vimfc.pipeline.incremental_collection import _publish_json
from vimfc.source.dump_download import exclusive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','source-campaign','workspace','durable','output','logs'):p.add_argument('--'+name,required=True)
    p.add_argument('--profile',default=str(Path(__file__).resolve().parents[1]/'configs/btt.json'))
    p.add_argument('--runtime');p.add_argument('--model');p.add_argument('--allow-network',action='store_true')
    a=p.parse_args();cfg=load(a.profile);stop=install_signals()
    logs=Path(a.logs);logs.mkdir(parents=True,exist_ok=True)
    _publish_json(logs/'environment.json',environment(Path(a.workspace).parent))
    if a.allow_network and os.environ.get('FAST20K_EGRESS_APPROVED')!='1':raise ValueError('Approved compute egress required')
    durable=Path(a.durable)
    # The same outer lock covers initial fork, acquisition, and final publication.
    with exclusive(durable.parent/('.'+durable.name+'-owner.lock')):
        if not (durable/'latest.json').exists():
            prepare(a.bundle,a.workspace,a.durable,a.source_campaign,cfg)
        def execute(qwen):return run(a.bundle,a.workspace,a.durable,cfg,allow_network=a.allow_network,qwen=qwen,stop_requested=stop)
        if a.runtime and a.model:
            with model_service(cfg['qwen'],a.runtime,a.model,logs) as (qwen,status):
                _publish_json(logs/'qwen-status.json',status);result=execute(qwen)
        else:result=execute(None)
        if result['stop_reason']=='TARGET_REACHED':
            if Path(a.output).exists():raise ValueError('Output already exists; inspect existing publication')
            manifest=publish(a.workspace,a.output)
            result.update(corpus_complete=True,corpus_version=manifest['corpus_version'])
            _publish_json(durable/'progress.json',result)
        _publish_json(logs/'result.json',result);print(canonical(result),flush=True)
        return dict(TARGET_REACHED=0,CHECKPOINT_STOP=130,CANDIDATE_EXHAUSTED=2,
                    REQUEST_BUDGET_EXHAUSTED=5,SOURCE_COOLDOWN=75,INTEGRITY_FAILURE=3,OPERATIONAL_ERROR=75)[result['stop_reason']]


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        print(json.dumps(dict(status='FAILED',error_type=type(e).__name__,error=str(e),corpus_complete=False)),file=sys.stderr)
        sys.exit(3)
