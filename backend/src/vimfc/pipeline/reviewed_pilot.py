"""Explicit operational guard for a reviewed archived-cohort pilot invocation."""
import json
from copy import deepcopy
from pathlib import Path
import sqlite3
import re
import sys
import time
import uuid
from vimfc.domain import Incompatible,canonical,now,VimfcError,SourceUnavailable
from vimfc.domain import digest
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.raw_cache import RawCache
from vimfc.storage.pipeline_store import PipelineStore
from .provider import HistoricalProvider
from .runner import Builder
from .report import ResourceSampler


INTEGRITY_CODES={'source_hash_mismatch','invalid_historical_cache','dependency_input_invalid','dependency_input_missing','invalid_item_hint'}


class IntegrityStop(Incompatible):
    def __init__(self,error):
        super().__init__(f'integrity_stop: {error.code}: {error}')
        self.source_code=error.code;self.details=error.details


def prepare_reviewed_replay(source_database,source_run,current_config,raw_root,output_dir,reports):
    """Clone to a NEW database; the historical database is opened read-only."""
    source_database=Path(source_database).resolve();raw_root=Path(raw_root).resolve();output=Path(output_dir).resolve()
    if output.exists() or not raw_root.is_dir() or output.is_relative_to(raw_root):raise Incompatible('New replay output/raw root invalid')
    with sqlite3.connect(source_database.as_uri()+'?mode=ro',uri=True) as source:
        row=source.execute('SELECT snapshot,config_json,config_hash FROM pipeline_runs WHERE pipeline_run_id=?',(source_run,)).fetchone()
        if not row:raise Incompatible('Unknown source run')
        snapshot,encoded,old_hash=row;old=json.loads(encoded)
        if digest(old)!=old_hash or snapshot!='2026-10-25T03:00:00Z':raise Incompatible('Source config/snapshot integrity failure')
        for key in ('classification','ranking','seed','streams','namespace_prefixes','quotas'):
            if old[key]!=current_config[key]:raise Incompatible(f'Replay cannot change {key}')
        if any(old['extraction'][k]!=current_config['extraction'][k] for k in ('min_sentences','min_tokens')):raise Incompatible('Quality thresholds changed')
        if source.execute('SELECT count(*) FROM candidates WHERE run_id=?',(source_run,)).fetchone()[0]!=1000:raise Incompatible('Expected archived1000 cohort')
        cfg=deepcopy(old);cfg.update(version=current_config['version'],extraction=deepcopy(current_config['extraction']))
        output.mkdir(parents=True)
        database=output/'pipeline.sqlite'
        with sqlite3.connect(database) as target:source.backup(target)
    encoded=deepcopy(cfg)
    for key in ('classification','extraction','ranking'):
        (output/(key+'_rules.json')).write_text(canonical(encoded.pop(key))+'\n',encoding='utf-8')
        encoded[key+'_file']=key+'_rules.json'
    config_path=output/'pipeline.json';config_path.write_text(canonical(encoded)+'\n',encoding='utf-8')
    with PipelineStore(database) as store:
        run_id=store.create(snapshot,cfg,reuse_run=source_run)
        store.event(run_id,'configuration_source',{'path':str(config_path)})
        candidates=store.candidates(run_id)
        if candidates!=store.candidates(source_run):raise Incompatible('Replay candidate identity/order mismatch')
        # These operational failures depend on the identical article/hint policy,
        # not extraction semantics. Reuse their future deadline, never refresh
        # old null hints early just because a new semantic run has been created.
        cols=[r[1] for r in store.db.execute('PRAGMA table_info(stage_results)')]
        rest=','.join(cols[1:]);stamp=now()
        store.db.execute(f"INSERT INTO stage_results({','.join(cols)}) SELECT ?,{rest} FROM stage_results WHERE run_id=? AND stage='item_hint_refresh' AND status='deferred' AND next_retry_at>? AND json_extract(error_json,'$.code')='missing_item_hint'",(run_id,source_run,stamp))
        store.db.execute("INSERT INTO candidate_work SELECT ?,page_id,status,record_json,next_retry_at,attempt_count,updated_at FROM candidate_work WHERE run_id=? AND status='deferred' AND next_retry_at>? AND json_extract(record_json,'$.error_code')='missing_item_hint'",(run_id,source_run,stamp))
    context={'database':str(database),'raw_cache':str(raw_root),'run_id':run_id,'source_run':source_run,'source_database':str(source_database),
        'snapshot':snapshot,'config':str(config_path),'config_hash':digest(cfg),'cohort_hash':digest(candidates),'reports':str(Path(reports).resolve()),'cohort_count':1000}
    context_path=output/'run-context.json'
    context_path.write_text(canonical(context)+'\n',encoding='utf-8')
    return {**context,'context':str(context_path)}


class PilotGuard:
    def __init__(self,max_requests,*,progress=True,stop_on_source=False):
        if not 1<=max_requests<=10000:raise ValueError('Request budget must be1..10000')
        self.max_requests=max_requests;self.http_attempts=0;self.blocked_attempts=0
        self.emit_progress=progress;self.last_candidate=None
        self.stop_on_source=stop_on_source

    def source_failure(self,error):
        untyped=re.search(r'mismatch|checksum|corrupt|beyond snapshot|future revision',str(error),re.I)
        if error.code in INTEGRITY_CODES or untyped:raise IntegrityStop(error) from error
        if self.stop_on_source:
            if error.retryable or error.code not in ('history_unavailable','historical_content_unavailable'):
                raise Incompatible('reviewed_source_stop: '+str(error.code)+'; no optional transient frontier published') from error

    def http_request(self,request):
        if request.url.params.get('maxlag')!='5':raise Incompatible('maxlag_guard')
        if self.http_attempts>=self.max_requests:
            self.blocked_attempts+=1
            raise Incompatible('request_budget_exhausted; no next HTTP request sent')
        self.http_attempts+=1

    def progress(self,event):
        if self.emit_progress:print(canonical(event),file=sys.stderr,flush=True)
        if event.get('page_id') is not None:self.last_candidate=event['page_id']
        untyped=event.get('status')=='source_failed' and re.search(r'mismatch|checksum|corrupt|beyond snapshot',event.get('error',''),re.I)
        if event.get('error_code') in INTEGRITY_CODES or untyped:
            raise Incompatible(f"integrity_stop: {event['error_code']}; page={event.get('page_id')}; checkpoint preserved")


def run_reviewed(store,run_id,raw_root,size,*,reports='reports/pilots',max_requests=2000,max_wait_seconds=60,retry_rounds=2,reviewed_quarantine=None,exploratory_progression=None,stop_on_source=False,integrity_registry=None):
    """Runs exactly one bounded stage, never starts the next size automatically."""
    if size not in (100,1000):raise ValueError('Reviewed invocation permits100 or1000 only')
    run=store.run(run_id)
    from .quarantine import ReviewedIntegrityQuarantine,require_review
    quarantine=ReviewedIntegrityQuarantine.from_file(reviewed_quarantine,run) if reviewed_quarantine else None
    registry=integrity_registry
    if registry:
        if registry.store is not store or registry.run_id!=run_id or registry.path.is_relative_to(Path(raw_root).resolve()):
            raise Incompatible('Registry must bind the same run/store outside trusted raw')
        try:registry.preflight(quarantine)
        except SourceUnavailable as error:raise IntegrityStop(error) from error
    elif store.db.execute("SELECT 1 FROM run_events WHERE run_id=? AND stage='integrity_registry_anchor' LIMIT 1",(run_id,)).fetchone():
        raise Incompatible('Explicit anchored integrity registry is required for this run')
    if quarantine:quarantine.bind(store)
    else:require_review(store,run_id,None)
    if len(store.candidates(run_id))!=1000:raise Incompatible('Requires the complete archived1000 cohort before source work')
    cfg=run['config'];guard=PilotGuard(max_requests,stop_on_source=stop_on_source)
    invocation=str(uuid.uuid4());folder=Path(reports)/run_id/str(size)
    receipt_path=folder/('reviewed-invocation-'+invocation+'.json')
    started=now();mark=time.perf_counter();report=None;failure=None
    with MediaWikiClient(cfg['contact'],user_agent=cfg.get('user_agent') or 'ViMFC/0.1',attempts=cfg['max_attempts']) as client:
        client.client.event_hooks['request'].append(guard.http_request)
        extra={'reviewed_quarantine':quarantine} if quarantine else {}
        if registry:
            extra['integrity_registry']=registry
            client.client.event_hooks['response'].append(registry.capture_response)
        provider=HistoricalProvider(store,run_id,client,RawCache(raw_root),**extra)
        def source_failure(error):
            diagnostic_error=None
            if registry and not error.details.get('registry_preflight'):
                try:
                    if error.code in INTEGRITY_CODES or re.search(r'mismatch|checksum|corrupt|future revision',str(error),re.I):
                        registry.capture_error(error,invocation=invocation,charged_attempts=guard.http_attempts,cap=max_requests)
                    else:
                        state='MISSING_HISTORICAL_DEPENDENCY' if error.code in ('history_unavailable','historical_content_unavailable') else 'SOURCE_UNAVAILABLE'
                        registry.observe_status(state,{'code':error.code,'message':str(error),'details':error.details,'root_article':registry.root_article,
                            'invocation_id':invocation,'charged_attempts':guard.http_attempts})
                except Exception as registry_write_error:
                    error.details['integrity_registry_error']={'type':type(registry_write_error).__name__,'message':str(registry_write_error)}
                    diagnostic_error=True
            guard.source_failure(error) # Always preserve the original IntegrityStop.
            if diagnostic_error:raise Incompatible('Integrity registry write failed; original source error: '+str(error)) from error
        if hasattr(provider,'source'):provider.source.integrity_guard=source_failure
        def progress(event):
            if event.get('status')=='source_failed':
                row=store.db.execute('SELECT record_json FROM candidate_work WHERE run_id=? AND page_id=?',(run_id,event['page_id'])).fetchone()
                if row:event={**event,**json.loads(row[0])}
            guard.progress(event)
            if stop_on_source and (event.get('status')=='source_failed' or
                event.get('status')=='deferred' and event.get('error_code')!='missing_item_hint'):
                raise Incompatible('reviewed_source_stop: '+str(event.get('error_code'))+'; checkpoint preserved')
        builder=Builder(store,run_id,provider,reports=reports,progress=progress,
            max_wait_seconds=max_wait_seconds,retry_rounds=retry_rounds,restore_completed=bool(quarantine),
            exploratory_progression=exploratory_progression)
        with ResourceSampler(Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent,cfg.get('sample_interval_seconds',1)) as sampler:
            try:
                report=builder.pilot(size)
            except (Exception,KeyboardInterrupt) as error:
                failure={'type':type(error).__name__,'message':str(error),'exit_code':getattr(error,'exit_code',1),
                    'source_code':getattr(error,'source_code',None),'details':getattr(error,'details',{})}
        resources=sampler.report()
        counts=dict(store.db.execute('SELECT status,count(*) FROM candidate_work WHERE run_id=? GROUP BY status',(run_id,)).fetchall())
        receipt={'invocation_id':invocation,'run_id':run_id,'size':size,'snapshot':run['snapshot'],'config_hash':run['config_hash'],
            'started_at':started,'ended_at':now(),'end_to_end_seconds':time.perf_counter()-mark,
            'source_http_attempts':guard.http_attempts,'blocked_http_attempts':guard.blocked_attempts,
            'request_budget':max_requests,'max_wait_seconds':max_wait_seconds,'retry_rounds':retry_rounds,
            'physical_request_metrics':dict(client.metrics),'last_candidate':guard.last_candidate,'resources':resources,
            'resource_scope':'Whole guarded invocation including primary Builder exports; receipt serialization excluded',
            'checkpoint_counts':counts,'failure':failure,'stopped':failure is not None,
            'completed':bool(report and report['completed']),'data_complete':bool(report and report['data_complete']),
            'pilot_accepted':bool(report and report['pilot_accepted']),
            'report_scope':'partial_checkpoint_only_integrity_or_budget_stop' if failure else 'bounded_pilot',
            'attempt_id':report.get('attempt_id') if report else None}
        if quarantine:
            quarantined=[json.loads(row[0]) for row in store.db.execute("SELECT record_json FROM candidate_work WHERE run_id=? AND status='quarantined_integrity'",(run_id,))]
            audit=quarantine.audit(quarantined)
            audit_path=folder/('quarantine-audit-'+invocation+'.json')
            folder.mkdir(parents=True,exist_ok=True)
            with audit_path.open('x',encoding='utf-8') as stream:json.dump(audit,stream,ensure_ascii=False,sort_keys=True,indent=2)
            receipt.update(reviewed_quarantine_id=quarantine.review_id,quarantine_audit_path=str(audit_path.resolve()),quarantined_count=len(quarantined))
        if registry:
            try:receipt['integrity_registry']=registry.audit()
            except Exception as registry_error:
                receipt['integrity_registry_error']={'type':type(registry_error).__name__,'message':str(registry_error)}
                if failure is None:
                    failure={'type':'Incompatible','message':'Integrity registry verification failed','exit_code':3,'details':receipt['integrity_registry_error']}
                    receipt.update(failure=failure,stopped=True,completed=False,data_complete=False,pilot_accepted=False)
        folder.mkdir(parents=True,exist_ok=True)
        with receipt_path.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,sort_keys=True,indent=2)
        return {**receipt,'receipt':str(receipt_path.resolve()),'exit_code':failure['exit_code'] if failure else 0 if report['data_complete'] else 5}
