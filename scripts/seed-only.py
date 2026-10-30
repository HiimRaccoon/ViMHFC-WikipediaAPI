"""Seed-only frozen listing acquisition, isolated preparation and bounded pilots."""
import argparse
import json
from pathlib import Path
import sys
import time
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.domain import canonical,Incompatible


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def parser():
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='action',required=True)
    collect=subs.add_parser('collect');collect.add_argument('--context',required=True)
    collect.add_argument('--output-dir',required=True);collect.add_argument('--target',type=int,default=80000)
    collect.add_argument('--max-requests',type=int,default=800)
    collect.add_argument('--freeze-minimum',action='store_true',help='Permit audited minimum60000 inventory after a bounded source/budget stop; never starts a pilot')
    prepare=subs.add_parser('prepare');prepare.add_argument('--context',required=True)
    prepare.add_argument('--inventory',required=True);prepare.add_argument('--output-dir',required=True)
    prepare.add_argument('--source-review')
    for action in ('run','verify'):
        sub=subs.add_parser(action);sub.add_argument('--context',required=True)
        sub.add_argument('--size',type=int,choices=(100,1000),required=True)
        if action=='run':
            sub.add_argument('--max-requests',type=int,required=True)
            sub.add_argument('--reviewed-quarantine');sub.add_argument('--exploratory-progression')
            sub.add_argument('--integrity-registry',help='Explicit anchored operational registry; required once activated')
        else:sub.add_argument('--output',required=True)
    return p


def main(argv=None):
    args=parser().parse_args(argv);context=read(args.context)
    if args.action=='collect':
        from vimfc.pipeline.inventory_acquisition import collect_inventory
        result=collect_inventory(context,args.output_dir,target=args.target,max_requests=args.max_requests,
            freeze_minimum=args.freeze_minimum,progress=lambda event:print(canonical(event),file=sys.stderr,flush=True))
        print(canonical(result));return result['exit_code']
    if args.action=='prepare':
        from vimfc.config import load_config
        from vimfc.pipeline.seed_only import seed_policy,plan_seed_cohort
        from vimfc.pipeline.discovery_experiments import prepare_experiment
        config=load_config(context['config']);inventory=read(args.inventory)
        if not 60000<=len(inventory['seeds'])<=100500:raise Incompatible('A real wide inventory is required before pilot preparation')
        plan=plan_seed_cohort(inventory,seed_policy(config['quotas']),1000)
        result=prepare_experiment(context,config,inventory,plan,args.output_dir,source_review=args.source_review,private_cache=True)
        print(canonical(result));return 0
    if args.action=='verify':
        from vimfc.pipeline.seed_verification import verify_seed_pilot
        result=verify_seed_pilot(context,args.size);write_new(args.output,result)
        print(canonical({k:v for k,v in result.items() if k!='manifest_ids'}));return 0
    from vimfc.storage.pipeline_store import PipelineStore
    from vimfc.pipeline.discovery_experiments import validate_experiment
    from vimfc.pipeline.reviewed_pilot import run_reviewed
    if not 1<=args.max_requests<={100:3000,1000:10000}[args.size]:
        raise Incompatible('Stage-specific request cap exceeded (100:3000;1000:10000)')
    if args.size==1000 and not args.exploratory_progression:
        raise Incompatible('Offline replay/progression receipt required before Seed-only1000')
    with ExitStack() as stack:
        store=stack.enter_context(PipelineStore(context['database']))
        registry=None
        if args.integrity_registry:
            from vimfc.pipeline.integrity_registry import IntegrityRegistry
            registry=stack.enter_context(IntegrityRegistry(args.integrity_registry,store,context['run_id']))
        plan=validate_experiment(context,store)
        if plan['policy']['version']!='seed-only-2' or plan['variant']!='seed-only':raise Incompatible('Seed-only-2 run required')
        result=run_reviewed(store,context['run_id'],context['raw_cache'],args.size,reports=context['reports'],
            max_requests=args.max_requests,max_wait_seconds=60,retry_rounds=0,
            reviewed_quarantine=args.reviewed_quarantine,stop_on_source=True,
            integrity_registry=registry,
            exploratory_progression=read(args.exploratory_progression) if args.exploratory_progression else None)
    print(canonical(result));return result['exit_code']


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(canonical({'stopped':True,'error_type':type(error).__name__,'error':str(error)}),file=sys.stderr)
        raise SystemExit(getattr(error,'exit_code',3))
