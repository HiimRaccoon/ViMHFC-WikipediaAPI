"""Offline discovery/review artifacts and explicitly invoked isolated Pilot100/1000."""
import argparse
from collections import Counter
import json
from pathlib import Path
import socket,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.domain import Incompatible,VimfcError,canonical,digest
from vimfc.pipeline.discovery_archive import Archive
from vimfc.pipeline.discovery_policy import default_policy,plan_cohort
from vimfc.pipeline.discovery_experiments import prepare_experiment,offline_metrics,validate_experiment
from vimfc.pipeline.pair_evaluation import build_review_pack,write_review_pack
from vimfc.pipeline.pairs import build_pairs
from vimfc.pipeline.report import ResourceSampler
from vimfc.source.association_audit import audit_associations

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def new_output(context,output):
    output=Path(output).resolve()
    roots=[Path(context['database']).resolve().parent,Path(context['raw_cache']).resolve()]
    if context.get('reports'):roots.append(Path(context['reports']).resolve()/context['run_id'])
    if output.exists() or any(output.is_relative_to(r) for r in roots):raise Incompatible('Use new output outside historical database/raw/run directories')
    return output

def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)

def offline(context,output):
    def denied(*args,**kwargs):raise AssertionError('Offline discovery/evaluation forbids network access')
    socket.socket.connect=denied
    output=new_output(context,output)
    with Archive(context) as archive:
        output.mkdir(parents=True)
        with ResourceSampler(output,1) as sampler:
            records=archive.records();inventory=archive.inventory(records)
            pairs=archive.pairs()
            if canonical(build_pairs(list(records.values()),archive.config['ranking']).pairs)!=canonical(pairs):
                raise Incompatible('Stored pairs do not replay with frozen rules/records')
            pack=build_review_pack(records,pairs,seed='pilot1000-review79-20261027')
            write_review_pack(output/'manual-review79',pack)
            manifests={r['classification']['manifest_id']:r['classification']['dependency_manifest'] for p in pack['pairs'] for d in p['documents'] for r in [records[d['page_id']]]}
            for identity,manifest in manifests.items():write_new(output/'manual-review79/manifests'/f'{identity}.json',manifest)
            associations=audit_associations(archive.db,archive.cache,context['snapshot'],archive.hint_articles())
            write_new(output/'historical-associations.json',associations);write_new(output/'inventory.json',inventory)
            results=[];policy=default_policy(archive.config['quotas'])
            for variant in ('legacy','seed-only','hybrid'):
                plan=plan_cohort(inventory,policy,variant,1000);write_new(output/'plans'/f'{variant}.json',plan)
                results.append(offline_metrics(plan,records,archive.config['ranking']))
            report={'format':'vimfc-discovery-offline-comparison-1','snapshot':context['snapshot'],'source_run':context['run_id'],
                'inventory_id':inventory['inventory_id'],'candidate_budget_per_variant':1000,'variants':results,
                'source_api_requests':0,'new_pilots_executed':0,'human_review_completed':False,
                'caveats':['Archived listing/eligibility-conditioned inventory, not a population sample.',
                    'Prior-run eligible seeds are declared frozen inputs; no adaptive worker-order expansion.',
                    'New neighbor candidates without archived process records remain unverified.',
                    'Offline restore/planning memory and wall time are not live pilot API cost or production performance.']}
            write_new(output/'offline-comparison.json',report)
        write_new(output/'offline-resources.json',{'resources':sampler.report(),'scope':'Offline validation, planning, review packaging and primary artifacts; excludes resource telemetry flush','physical_api_requests':0})
    return {'output':str(output),'review_pairs':len(pack['pairs']),'hint_articles':len(associations['articles']),'variants':results,'physical_api_requests':0}

def parser():
    root=argparse.ArgumentParser(description=__doc__);subs=root.add_subparsers(dest='action',required=True)
    for action in ('offline','prepare','run','status','report'):
        p=subs.add_parser(action);p.add_argument('--context',required=True)
        if action in ('offline','prepare'):p.add_argument('--output-dir',required=True)
        if action=='prepare':
            p.add_argument('--inventory',required=True);p.add_argument('--plan',required=True);p.add_argument('--source-review')
            p.add_argument('--private-cache',action='store_true',help='Copy and hash a common raw-cache baseline per arm for comparable API cost')
        if action=='run':
            p.add_argument('--size',type=int,choices=(100,1000),required=True)
            p.add_argument('--max-requests',type=int,required=True);p.add_argument('--max-wait-seconds',type=int,default=60)
            p.add_argument('--retry-rounds',type=int,default=0);p.add_argument('--reviewed-quarantine')
        if action=='report':p.add_argument('--size',type=int,choices=(100,1000),required=True);p.add_argument('--output',required=True)
    p=subs.add_parser('compare');p.add_argument('--contexts',nargs=3,required=True);p.add_argument('--size',type=int,choices=(100,1000),required=True);p.add_argument('--output',required=True)
    return root

def saved_report(context,size):
    with Archive(context) as archive:
        row=archive.db.execute('SELECT report_json FROM pilot_reports WHERE run_id=? AND size=?',(context['run_id'],size)).fetchone()
        if not row:raise Incompatible('No report for this stage; do not infer pilot completion')
        result=json.loads(row[0])
        if result['config_hash']!=context['config_hash'] or result['snapshot']!=context['snapshot']:raise Incompatible('Report identity mismatch')
        return result

def main(argv=None):
    try:
        args=parser().parse_args(argv)
        if args.action=='compare':
            contexts=[read(p) for p in args.contexts]
            if {c['variant'] for c in contexts}!={'legacy','seed-only','hybrid'}:raise Incompatible('Supply legacy, seed-only and hybrid contexts once each')
            if len({c['run_id'] for c in contexts})!=3:raise Incompatible('Comparison requires three distinct experiment runs')
            semantic=[];inventories=[]
            for c in contexts:
                with Archive(c) as archive:
                    if archive.config.get('discovery',{}).get('variant')!=c['variant']:raise Incompatible('Context treatment label differs from archived run')
                    semantic.append(digest({k:archive.config[k] for k in ('classification','extraction','ranking','quotas')}))
                    inventories.append(archive.config.get('discovery',{}).get('inventory_id'))
            if len(set(semantic))!=1:raise Incompatible('Comparison semantic rules/quotas differ')
            if None in inventories or len(set(inventories))!=1:raise Incompatible('Comparison sampling inventories differ')
            reports=[saved_report(c,args.size) for c in contexts]
            if len({r['snapshot'] for r in reports})!=1 or any(r['size']!=args.size for r in reports):raise Incompatible('Comparison snapshot/budget mismatch')
            result={'format':'vimfc-discovery-live-comparison-1','stage_size':args.size,
                'cache_baselines':[c.get('cache_baseline') for c in contexts],
                'api_cost_comparable':all(c.get('cache_baseline',{}).get('mode')=='private-copy' for c in contexts) and len({c.get('cache_baseline',{}).get('baseline_id') for c in contexts})==1,
                'variants':[{'variant':c['variant'],**{k:r.get(k) for k in ('run_id','snapshot','cohort_hash','config_hash','rule_versions','data_complete','pilot_accepted','work_counts','corpus','pair_audit','subtype_audit','performance')}} for c,r in zip(contexts,reports)],
                'human_valid_two_hop_yield':None,'scope':'Per-invocation warm/cold/work scopes differ; inspect performance before attributing efficiency. No automatic next-stage authorization.'}
            write_new(args.output,result);print(canonical({'output':str(Path(args.output).resolve())}));return 0
        context=read(args.context)
        if args.action=='offline':print(canonical(offline(context,args.output_dir)));return 0
        if args.action=='prepare':
            inventory=read(args.inventory);plan=read(args.plan)
            with Archive(context) as archive:
                if archive.inventory(archive.records())!=inventory:raise Incompatible('Inventory is not the verified archived source input')
                result=prepare_experiment(context,archive.config,inventory,plan,args.output_dir,source_review=args.source_review,private_cache=args.private_cache)
            print(canonical(result));return 0
        if args.action=='status':
            with Archive(context) as archive:
                rows=[dict(r) for r in archive.db.execute('SELECT * FROM candidate_work WHERE run_id=?',(context['run_id'],))]
                result={'run_id':context['run_id'],'variant':context['variant'],'snapshot':context['snapshot'],'candidate_count':len(archive.cohort),
                    'work_counts':dict(Counter(r['status'] for r in rows)),'retry_deadlines':[{'page_id':r['page_id'],'retry_at':r['next_retry_at']} for r in rows if r['status']=='deferred'],
                    'status':archive.run['status'],'production_authorized':False}
            print(canonical(result));return 0
        if args.action=='report':
            write_new(args.output,saved_report(context,args.size));print(canonical({'output':str(Path(args.output).resolve())}));return 0
        if not 1<=args.max_requests<=10000 or not 0<=args.max_wait_seconds<=3600 or not 0<=args.retry_rounds<=5:
            raise Incompatible('Invalid request/wait/retry budget')
        from vimfc.storage.pipeline_store import PipelineStore
        from vimfc.pipeline.reviewed_pilot import run_reviewed
        with PipelineStore(context['database']) as store:
            validate_experiment(context,store)
            result=run_reviewed(store,context['run_id'],context['raw_cache'],args.size,reports=context['reports'],max_requests=args.max_requests,
                max_wait_seconds=args.max_wait_seconds,retry_rounds=args.retry_rounds,reviewed_quarantine=args.reviewed_quarantine)
        print(canonical({k:result.get(k) for k in ('run_id','attempt_id','completed','data_complete','pilot_accepted','source_http_attempts','failure','receipt','exit_code')}))
        return result['exit_code']
    except (VimfcError,OSError,ValueError,KeyError) as error:
        print(canonical({'error':type(error).__name__,'message':str(error)}));return getattr(error,'exit_code',3)

if __name__=='__main__':raise SystemExit(main())
