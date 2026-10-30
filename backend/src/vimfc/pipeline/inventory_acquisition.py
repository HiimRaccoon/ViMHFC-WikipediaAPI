"""One bounded acquisition invocation; telemetry survives a source/integrity stop."""
import json
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import closing
from vimfc.config import load_config
from vimfc.domain import Incompatible,SourceUnavailable,now,digest
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.seed_inventory import SeedInventory,inventory_policy,OPERATIONAL_POLICY
from .report import ResourceSampler
from .inventory_publication import publish_inventory,recover_publication


def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def checkpoint_status(path):
    if not path.exists():return {'charged_attempts':0,'unique_candidates':0,'host_state':None}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'pages','attempts','metadata'}<=tables:return {'charged_attempts':0,'unique_candidates':0,'host_state':None}
            host=db.execute("SELECT value FROM metadata WHERE key='host_state'").fetchone()
            result={'charged_attempts':db.execute('SELECT count(*) FROM attempts').fetchone()[0],
                'unique_candidates':db.execute('SELECT count(*) FROM pages').fetchone()[0],'host_state':None,
                'sealed':bool(db.execute("SELECT 1 FROM metadata WHERE key='frozen'").fetchone())}
            try:
                result['host_state']=json.loads(host[0]) if host else None
                if host and not isinstance(result['host_state'],dict):raise ValueError('Invalid host state shape')
            except (ValueError,TypeError) as error:result['status_read_error']=str(error)
            return result
    except (sqlite3.Error,OSError) as error:
        return {'charged_attempts':None,'unique_candidates':None,'host_state':None,'status_read_error':str(error)}


def collect_inventory(context,output_dir,*,target=80000,max_requests=800,freeze_minimum=False,client_factory=None,progress=None):
    config=load_config(context['config']);output=Path(output_dir).resolve()
    if output.is_relative_to(Path(context['database']).resolve().parent) or output.is_relative_to(Path(context['raw_cache']).resolve()):
        raise Incompatible('Use a separate inventory directory, never historical database/raw roots')
    policy=inventory_policy(config,target=target,max_requests=max_requests)
    if context.get('config_hash') and digest(config)!=context['config_hash']:
        raise Incompatible('Source config identity changed before inventory acquisition')
    if context.get('snapshot') and context['snapshot']!=policy['snapshot']:
        raise Incompatible('Inventory snapshot differs from source context')
    output.mkdir(parents=True,exist_ok=True)
    checkpoint=output/'listing-checkpoint.sqlite'
    before=checkpoint_status(checkpoint);invocation=str(uuid.uuid4());folder=output/'acquisition-attempts'/invocation
    folder.mkdir(parents=True,exist_ok=False)
    started=now();mark=time.perf_counter();failure=None;frozen=None;client=None
    factory=client_factory or (lambda cfg:MediaWikiClient(cfg['contact'],user_agent=cfg.get('user_agent') or 'ViMFC/0.1',attempts=1))
    pacing_seconds=0;inventory=None
    with ResourceSampler(output,1) as sampler:
        try:
            if before.get('status_read_error'):raise Incompatible('Invalid checkpoint status; no source request')
            with SeedInventory(checkpoint,policy) as inventory:
                frozen=recover_publication(inventory,config,output)
                if frozen is None:
                    if (output/'inventory.json').exists():raise Incompatible('Existing inventory has no recoverable publication; no source request')
                    client=factory(config)
                    with client:inventory.collect(client,progress)
                pacing_seconds=inventory.pacing_wait_seconds
        except (Exception,KeyboardInterrupt) as error:
            failure={'type':type(error).__name__,'message':str(error),'code':getattr(error,'code',None),
                'retry_at':getattr(error,'retry_at',None),'details':getattr(error,'details',{}),
                'exit_code':getattr(error,'exit_code',3)}
        pacing_seconds=getattr(inventory,'pacing_wait_seconds',0)
        status=checkpoint_status(checkpoint)
        if status.get('status_read_error') and failure is None:
            failure={'type':'Incompatible','message':'Checkpoint status unavailable after acquisition','code':'checkpoint_status_invalid',
                'retry_at':None,'details':{'read_error':status['status_read_error']},'exit_code':3}
        may_freeze=failure is None
        # Only a clean source stop/budget stop may publish an explicitly permitted
        # minimum-size snapshot. Unknown/integrity failures never trigger freeze.
        if failure:
            may_freeze=(failure.get('code')=='http_503' or
                failure.get('code')=='inventory_request_budget_exhausted' and status['charged_attempts']==max_requests)
        if status.get('status_read_error'):may_freeze=False
        enough=status['unique_candidates'] is not None and (status['unique_candidates']>=target or freeze_minimum and status['unique_candidates']>=60000)
        if frozen is None and enough and may_freeze:
            try:
                with SeedInventory(checkpoint,policy) as inventory:frozen=publish_inventory(inventory,config,output,folder)
            except Exception as error:
                failure={'type':type(error).__name__,'message':str(error),'code':'inventory_freeze_failed',
                    'retry_at':None,'details':{},'exit_code':getattr(error,'exit_code',3)}
                frozen=None
        status=checkpoint_status(checkpoint)
        partial={'invocation_id':invocation,'started_at':started,'status_before':before,'status_after':status,
            'failure':failure,'inventory_published':bool(frozen),'operational_policy':OPERATIONAL_POLICY,
            'inventory_publication_state':'complete' if frozen else 'sealed_pending_publication' if status.get('sealed') else 'unsealed',
            'physical_request_metrics':dict(getattr(client,'metrics',{})),'data_complete':bool(frozen and frozen['source']['target_reached']),
            'minimum_inventory_met':status['unique_candidates'] is not None and status['unique_candidates']>=60000,'pilot_accepted':False}
        write_new(folder/'outcome.json',partial)
    receipt={**partial,'ended_at':now(),'end_to_end_seconds':time.perf_counter()-mark,'resources':sampler.report(),
        'pacing_wait_seconds':pacing_seconds,'http_attempts_this_invocation':sum(v for k,v in getattr(client,'metrics',{}).items() if k.endswith(':requests')),
        'charged_attempts_this_invocation':status['charged_attempts']-before['charged_attempts'] if status['charged_attempts'] is not None and before['charged_attempts'] is not None else None,
        'max_requests_total':max_requests,'stopped':bool(failure),'exit_code':failure['exit_code'] if failure else 0 if frozen else 5,
        'resource_scope':'Acquisition, validation, optional inventory freeze and outcome export; excludes final resources/receipt telemetry flush',
        'receipt_path':str((folder/'receipt.json').resolve()),'inventory_path':str(output/'inventory.json') if frozen else None}
    write_new(folder/'resources.json',{'resources':receipt['resources'],'end_to_end_seconds':receipt['end_to_end_seconds'],
        'measurement_scope':receipt['resource_scope']})
    write_new(folder/'receipt.json',receipt)
    return receipt
