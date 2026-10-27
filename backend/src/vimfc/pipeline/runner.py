"""Only deliberately invoked bounded pilots can acquire source data."""
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import time
import uuid
from vimfc.domain import canonical, digest, now, at_or_before, SourceUnavailable, InvalidConfig, Incompatible, InvalidCorpus
from .pairs import build_pairs
from .select import select_corpus
from .report import corpus_report, template_audit, classification_audit, pair_generation_audit, performance_report, ResourceSampler, ProgressWindow
from .quarantine import QuarantinedIntegrity


class StageExecutor:
    def __init__(self,store,run_id,*,client=None):
        self.store,self.run_id=store,run_id
        self.run=store.run(run_id)
        self.client=client
        self.metrics=defaultdict(lambda:{'active_seconds':0,'new':0,'reused':0,'failed':0})

    def measure(self,stage,work):
        """Time non-checkpointable validation/recovery without caching its result."""
        metric=self.metrics[stage];start=time.perf_counter()
        before=Counter(getattr(self.client,'metrics',{}))
        try:
            result=work();metric['new']+=1
            return result
        except BaseException:
            metric['failed']+=1
            raise
        finally:
            metric['active_seconds']+=time.perf_counter()-start
            metric['backoff_seconds']=metric.get('backoff_seconds',0)+sum(
                v-before[k] for k,v in getattr(self.client,'metrics',{}).items() if k.endswith(':backoff_seconds'))

    def execute(self,stage,identity,dependencies,work):
        identity=str(identity)
        dep=digest(dependencies)
        metric=self.metrics[stage]
        result=self.store.result(self.run_id,identity,stage,dep)
        if result is not None:
            metric['reused']+=1
            return result
        reuse=self.run.get('reuse_run')
        if reuse:
            result=self.store.result(reuse,identity,stage,dep)
            incomplete=isinstance(result,dict) and (result.get('outcome')=='unresolved' or result.get('classification',{}).get('outcome')=='unresolved')
            if stage=='acquire_manifest' and isinstance(result,dict) and result.get('format')=='vimfc-primary-first-1':
                incomplete=incomplete or any(f.get('source_code') for f in result.get('frontier',[]))
            if result is not None and not incomplete:
                self.store.complete(self.run_id,identity,stage,dep,result)
                metric['reused']+=1
                return result
        key=(self.run_id,identity,stage,dep)
        token=str(uuid.uuid4())
        lease=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace('+00:00','Z')
        with self.store.transaction():
            # Compare and claim under one writer transaction. Another worker can
            # neither slip between the check/write nor overwrite a completed row.
            result=self.store.result(self.run_id,identity,stage,dep)
            if result is not None:
                metric['reused']+=1
                return result
            row=self.store.db.execute('SELECT * FROM stage_results WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=?',key).fetchone()
            stamp=now()
            if row and row['next_retry_at'] and not at_or_before(row['next_retry_at'],stamp):
                if row['error_json']:
                    error=json.loads(row['error_json'])
                    raise SourceUnavailable(error['message'],retryable=True,code=error['code'],retry_at=row['next_retry_at'],details=error['details'])
                raise SourceUnavailable('Wait for the recorded retry time; use pipeline retry when eligible',retryable=True,code='stage_cooldown',retry_at=row['next_retry_at'])
            if row and row['lease_until'] and row['lease_until']>stamp:
                raise SourceUnavailable('Stage worker lease is still active',retryable=True,code='stage_lease',retry_at=row['lease_until'])
            if row and row['status']=='failed' and not row['next_retry_at']:
                if row['error_json']:
                    error=json.loads(row['error_json'])
                    raise SourceUnavailable(error['message'],code=error['code'],details=error['details'])
                raise SourceUnavailable('Permanent source failure; inspect run diagnostics')
            if row and row['status']=='pending' and row['result_json'] and not row['next_retry_at'] and not row['lease_until']:
                result=json.loads(row['result_json'])
                if digest(result)!=row['result_hash']:
                    raise Incompatible('Pending stage result checksum mismatch')
                if not result.get('retryable'):
                    metric['reused']+=1
                    return result
            self.store.db.execute('''INSERT INTO stage_results(run_id,identity,stage,dependency_hash,status,lease_until,lease_token,attempt_count) VALUES (?,?,?,?,'pending',?,?,1)
                ON CONFLICT(run_id,identity,stage,dependency_hash) DO UPDATE SET status='pending',lease_until=excluded.lease_until,lease_token=excluded.lease_token,attempt_count=attempt_count+1''',(*key,lease,token))
        start=time.perf_counter()
        request_start=Counter(getattr(self.client,'metrics',{}))
        try:
            result=work()
            with self.store.transaction():
                owner=self.store.db.execute('SELECT lease_token,status FROM stage_results WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=?',key).fetchone()
                if owner['lease_token']!=token or owner['status']!='pending':
                    raise SourceUnavailable('Lost worker lease; stale completion discarded',retryable=True)
                pending=isinstance(result,dict) and (result.get('outcome')=='pending' or result.get('status')=='pending_source')
                if pending:
                    retry=(result.get('retry_at') or (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat().replace('+00:00','Z')) if result.get('retryable') else None
                    self.store.db.execute("UPDATE stage_results SET status='pending',result_json=?,result_hash=?,lease_until=NULL,next_retry_at=?,last_error=? WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=?",(canonical(result),digest(result),retry,result.get('reason','pending_source'),self.run_id,identity,stage,dep))
                else:
                    self.store.complete(self.run_id,identity,stage,dep,result)
        except SourceUnavailable as error:
            retry=(error.retry_at or (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat().replace('+00:00','Z')) if error.retryable else None
            status='deferred' if error.retryable else 'failed'
            value={'message':str(error),'code':error.code,'details':error.details}
            self.store.db.execute("UPDATE stage_results SET status=?,lease_until=NULL,lease_token=NULL,last_error=?,next_retry_at=?,error_json=? WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=? AND lease_token=? AND status='pending'",(status,str(error),retry,canonical(value),*key,token))
            self.store.event(self.run_id,'stage_deferred' if error.retryable else 'stage_failed',{'identity':identity,'stage':stage,'dependency_hash':dep,'retry_at':retry,**value})
            metric['deferred' if error.retryable else 'failed']=metric.get('deferred' if error.retryable else 'failed',0)+1
            raise
        except BaseException:
            # Ctrl+C must not leave an otherwise idle worker lease blocking resume.
            self.store.db.execute("UPDATE stage_results SET lease_until=NULL,lease_token=NULL,next_retry_at=?,last_error='interrupted' WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=? AND lease_token=? AND status='pending'",(now(),*key,token))
            raise
        finally:
            metric['active_seconds']+=time.perf_counter()-start
            counters=getattr(self.client,'metrics',{})
            metric['backoff_seconds']=metric.get('backoff_seconds',0)+sum(
                value-request_start[key] for key,value in counters.items() if key.endswith(':backoff_seconds'))
        metric['new']+=1
        return result


class PilotControls:
    def __init__(self,store,run_id):
        self.store,self.run_id=store,run_id

    def report(self,size):
        row=self.store.db.execute('SELECT report_json FROM pilot_reports WHERE run_id=? AND size=?',(self.run_id,size)).fetchone()
        return json.loads(row[0]) if row else None

    def pilot(self,size):
        if size not in (100,1000,10000):
            raise InvalidConfig('Pilot sizes are exactly 100, 1000, 10000')
        if self.store.run(self.run_id)['production_report']:
            raise InvalidConfig('Production run cannot be relabelled as a pilot')
        previous={1000:100,10000:1000}.get(size)
        if previous:
            report=self.report(previous)
            if not report or not report['completed'] or not report.get('data_complete',not report.get('source_failure_count',0)):
                raise InvalidConfig(f'Complete and inspect pilot {previous} first')
        return size

    def save_report(self,report):
        self.store.db.execute('INSERT INTO pilot_reports VALUES (?,?,?) ON CONFLICT(run_id,size) DO UPDATE SET report_json=excluded.report_json',(self.run_id,report['size'],canonical(report)))

    def production(self,report):
        if not report:
            raise InvalidConfig('An explicitly reviewed final pilot report is required')
        run=self.store.run(self.run_id)
        if report.get('run_id')!=self.run_id or report.get('config_hash')!=run['config_hash']:
            raise Incompatible('Pilot report has stale run/rule identity')
        if report.get('size')!=10000 or not report.get('completed') or report.get('data_complete') is not True or report.get('critical_defects'):
            raise InvalidConfig('Final completed 10000 pilot with no critical defects is required')
        archived=self.report(10000)
        if not archived or digest(archived)!=digest(report):
            raise InvalidConfig('Reviewed report differs from the archived final pilot')
        for size in (100,1000):
            prior=self.report(size)
            if not prior or not prior['completed'] or prior.get('data_complete') is not True or prior['config_hash']!=run['config_hash']:
                raise InvalidConfig('Required final-rule staged reports are missing')
        self.store.db.execute('UPDATE pipeline_runs SET production_report=?,updated_at=? WHERE pipeline_run_id=?',(digest(report),now(),self.run_id))


class Builder:
    def __init__(self,store,run_id,provider,reports='reports/pilots',*,progress=None,max_wait_seconds=0,retry_rounds=0,deferred_only=False,restore_completed=False):
        if not 0<=max_wait_seconds<=3600 or not 0<=retry_rounds<=5:
            raise InvalidConfig('Scheduler wait must be 0..3600 seconds; retry rounds 0..5')
        self.store,self.run_id,self.provider=store,run_id,provider
        self.run=store.run(run_id)
        self.config=self.run['config']
        self.executor=StageExecutor(store,run_id,client=getattr(provider,'client',None))
        self.controls=PilotControls(store,run_id)
        self.reports=Path(reports)
        self.progress=progress
        self.max_wait_seconds,self.retry_rounds=max_wait_seconds,retry_rounds
        self.deferred_only=deferred_only
        self.restore_completed=restore_completed
        self.reviewed_quarantine=getattr(provider,'reviewed_quarantine',None)
        self.host_health=None
        self._health_baseline=None

    def collect(self,target):
        row=self.store.db.execute('SELECT state_json FROM discovery_streams WHERE run_id=? AND stream_id=?',(self.run_id,'manifest')).fetchone()
        state=json.loads(row[0]) if row else {}
        current=self.store.db.execute('SELECT count(*) FROM candidates WHERE run_id=?',(self.run_id,)).fetchone()[0]
        while current<target:
            pages,state,exhausted=self.provider.discover(state,min(100,target-current))
            with self.store.transaction():
                for candidate in pages:
                    cursor=self.store.db.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)',(self.run_id,candidate['page_id'],current,canonical(candidate)))
                    current+=cursor.rowcount
                self.store.db.execute('INSERT INTO discovery_streams VALUES (?,?,?) ON CONFLICT(run_id,stream_id) DO UPDATE SET state_json=excluded.state_json',(self.run_id,'manifest',canonical(state)))
            if exhausted and current<target:
                raise InvalidCorpus(f'Discovery exhausted at {current} unique candidates; target {target}')
        return self.store.candidates(self.run_id,target)

    def records(self,size=None):
        output=[]
        for candidate in self.store.candidates(self.run_id,size):
            deps={'snapshot':self.run['snapshot'],'config':self.run['config_hash'],'candidate':candidate}
            if self.config['classification'].get('dependency_policy'):
                row=self.store.db.execute('SELECT m.record_json FROM article_manifests b JOIN dependency_manifests m USING(manifest_id) WHERE b.run_id=? AND b.page_id=?',(self.run_id,candidate['page_id'])).fetchone()
                if not row:continue
                manifest=json.loads(row[0])
                deps['input']={'manifest_id':manifest['manifest_id'],'article_hash':digest(manifest['article'])}
                if self.config.get('discovery'):
                    deps['input']['discovery_scope_id']=digest(self.provider.discovery_scope_for_article(manifest['article']))
            result=self.store.result(self.run_id,candidate['page_id'],'process',digest(deps))
            if result:
                output.append(result)
        return output

    def _process(self,target,pilot):
        with ResourceSampler(self.store_path(),self.config.get('sample_interval_seconds',1)) as sampler:
            return self._process_measured(target,pilot,sampler)

    def _process_measured(self,target,pilot,sampler):
        started=getattr(self,'_health_started_at',None) or now()
        start=getattr(self,'_health_started_clock',None) or time.perf_counter()
        records=[]
        progress=ProgressWindow(target)
        progress.observe(start,0)
        request_start=self._health_baseline if self._health_baseline is not None else Counter(getattr(getattr(self.provider,'client',None),'metrics',{}))
        source=getattr(self.provider,'source',None)
        cache_start={'hits':getattr(source,'cache_hits',0),'lookups':getattr(source,'cache_lookups',0)}
        source_metrics_start=Counter(getattr(source,'metrics',{}))
        hierarchy_metrics_start=Counter(getattr(getattr(self.provider,'wikidata',None),'metrics',{}))
        provider_executor=getattr(self.provider,'executor',None)
        if provider_executor:
            provider_executor.metrics.clear()
        mark=time.perf_counter()
        if self.deferred_only and len(self.store.candidates(self.run_id,target))!=target:
            raise InvalidConfig('Deferred-only resume requires the complete archived cohort; discovery is disabled')
        candidates=self.collect(target)
        discovery_seconds=time.perf_counter()-mark
        discovery_backoff=sum(v-request_start[k] for k,v in getattr(getattr(self.provider,'client',None),'metrics',{}).items() if k.endswith(':backoff_seconds'))
        if self.progress:
            self.progress({'event':'cohort_ready','run_id':self.run_id,'candidates':len(candidates),'snapshot':self.run['snapshot']})
        from .scheduling import CandidateScheduler
        scheduler=CandidateScheduler(self.store,self.run_id,max_wait_seconds=self.max_wait_seconds,
            retry_rounds=self.retry_rounds,progress=self.progress,host_guard=getattr(self.provider,'host_guard',None),
            restore_quarantine=(lambda page,record:self.reviewed_quarantine.restore_candidate(self.store,page,record)) if self.reviewed_quarantine else None)
        initial=self._restore_terminal_records(candidates,allow_unseen=not self.deferred_only) if self.deferred_only or self.restore_completed else None
        restored_processed=sum(r.get('status') not in ('source_failed','quarantined_integrity') for r in (initial or {}).values())
        def work(candidate):
            deps={'snapshot':self.run['snapshot'],'config':self.run['config_hash'],'candidate':candidate}
            prepared={}
            try:
                if hasattr(self.provider,'process_dependencies'):
                    prepared=self.provider.process_dependencies(candidate)
                    if prepared:deps['input']=prepared
                result=self.executor.execute('process',candidate['page_id'],deps,
                    lambda c=candidate:self.provider.process_prepared(c,prepared) if prepared and hasattr(self.provider,'process_prepared') else self.provider.process(c))
                return result
            except QuarantinedIntegrity as error:
                if not self.reviewed_quarantine:raise
                return self.reviewed_quarantine.candidate_record(self.store,candidate['page_id'],error)
            finally:
                if hasattr(self.provider,'release_prepared'):self.provider.release_prepared(candidate)
        def observe(candidate,record,current,invoked):
            counts=scheduler.report(list(current.values()))
            state=progress.observe(time.perf_counter(),counts['processed']-restored_processed)
            state['queued']=target-len(current)+counts['deferred']
            if counts['deferred'] or counts['source_failed'] or counts.get('quarantined_integrity'):state['eta_seconds']=None
            self.store.event(self.run_id,'progress',{**state,
                'cache_reused':self.executor.metrics['process']['reused']+restored_processed,'visited':len(current),**counts})
            if self.progress:
                event='candidate_quarantined' if record.get('status')=='quarantined_integrity' else 'candidate_deferred' if record.get('status')=='deferred' else 'candidate_processed'
                self.progress({'event':event,
                    'run_id':self.run_id,'page_id':candidate['page_id'],'target':target,'visited':len(current),**counts,
                    'invoked':invoked,'status':record.get('status'),'classification':record.get('classification',{}).get('outcome'),
                    'error_code':record.get('error_code'),'retry_at':record.get('retry_at')})
        records=scheduler.run(candidates,work,observe,initial_records=initial)
        for record in records:
            if record.get('status') in ('deferred','source_failed'):
                self._enrich_failure(record)
        eligible=[a for a in records if a.get('quality',{}).get('eligible')]
        mark=time.perf_counter()
        pairs=build_pairs(eligible,self.config['ranking'])
        pair_seconds=time.perf_counter()-mark
        mark=time.perf_counter()
        selection=select_corpus(eligible,pairs.pairs,self.config['ranking'],self.config['quotas'])
        selection_seconds=time.perf_counter()-mark
        with self.store.transaction():
            self.store.db.execute('DELETE FROM pair_candidates WHERE run_id=?',(self.run_id,))
            self.store.db.executemany('INSERT INTO pair_candidates VALUES (?,?,?,?,?,?)',[(self.run_id,p['kind'],p['endpoint_a'],p['endpoint_b'],pairs.ranking_version,canonical(p)) for p in pairs.pairs])
            self.store.db.execute('DELETE FROM selection_steps WHERE run_id=?',(self.run_id,))
            self.store.db.executemany('INSERT INTO selection_steps VALUES (?,?,?,?)',[(self.run_id,self.config['ranking']['selection_version'],step['step'],canonical(step)) for step in selection.steps])
            waiting=any(r.get('status') in ('deferred','source_failed') for r in records)
            quarantined=sum(r.get('status')=='quarantined_integrity' for r in records)
            self.store.db.execute('UPDATE pipeline_runs SET status=?,updated_at=? WHERE pipeline_run_id=?',('awaiting_integrity_review' if quarantined else 'awaiting_sources' if waiting else 'selecting' if selection.shortage else 'ready_to_freeze',now(),self.run_id))
        metrics=dict(self.executor.metrics)
        if hasattr(self.provider,'executor'):
            metrics.update(self.provider.executor.metrics)
        for stage in ('acquire','extract','classify','resolve_links','quality'):
            metrics.setdefault(stage,{'active_seconds':0,'new':0,'reused':0,'failed':0,'executed':False})
        metrics.update({'discovery':{'active_seconds':discovery_seconds,'backoff_seconds':discovery_backoff,'new':0,'reused':0},'pairs':{'active_seconds':pair_seconds,'new':0,'reused':0},'selection':{'active_seconds':selection_seconds,'new':0,'reused':0}})
        if self.host_health:metrics['host_preflight']={'active_seconds':self._health_seconds,'backoff_seconds':0,'new':int(self.host_health.get('probe_performed',False)),'reused':0}
        physical={k:v-request_start[k] for k,v in getattr(getattr(self.provider,'client',None),'metrics',{}).items() if v!=request_start[k]}
        backoff=sum(v for k,v in physical.items() if k.endswith(':backoff_seconds'))
        for name,metric in metrics.items():
            metric['wall_seconds']=metric['active_seconds']
            metric['backoff_seconds']=metric.get('backoff_seconds',0)
            metric['active_seconds']=max(0,metric['wall_seconds']-metric['backoff_seconds'])
            metric['queue_seconds']=0
        performance=performance_report(metrics,time.perf_counter()-start,physical,{'hits':getattr(source,'cache_hits',0)-cache_start['hits'],'lookups':getattr(source,'cache_lookups',0)-cache_start['lookups']},sampler.report())
        performance['source_cache']=dict(Counter(getattr(source,'metrics',{}))-source_metrics_start)
        performance['hierarchy_cache']=dict(Counter(getattr(getattr(self.provider,'wikidata',None),'metrics',{}))-hierarchy_metrics_start)
        performance['reuse_run']=self.run.get('reuse_run')
        performance['measurement_scope']='current_invocation'
        if self.run.get('reuse_run'):
            performance['production_eta']={'status':'unavailable','seconds':None,'reason':'New-rule replay reuses historical raw cache; this is not a cold production measurement.'}
        performance['candidate_counts']={'cohort':target,'eligible':len(eligible),'source_failed':sum(r.get('status')=='source_failed' for r in records),'pending_classification':sum(r.get('classification',{}).get('outcome')=='pending' for r in records),
            'deferred':sum(r.get('status')=='deferred' for r in records),
            'quarantined_integrity':quarantined,
            'unresolved_classification':sum(r.get('classification',{}).get('outcome')=='unresolved' for r in records),
            'quality_rejected':sum(bool(r.get('quality')) and not r['quality']['eligible'] and r.get('status') not in ('pending_source','unresolved_classification') for r in records)}
        performance['candidate_rates']={key:{'count':count,'denominator':target,'rate':count/target if target else None} for key,count in performance['candidate_counts'].items() if key!='cohort'}
        report={'run_id':self.run_id,'attempt_id':str(uuid.uuid4()),'size':target,'config_hash':self.run['config_hash'],'snapshot':self.run['snapshot'],'started_at':started,'ended_at':now(),
            'completed':True,'critical_defects':[],'cohort_hash':digest(candidates),'cohort_page_ids':[c['page_id'] for c in candidates],
            'rule_versions':{k:self.config[k]['version'] for k in ('classification','extraction','ranking')},
            'performance':performance,'template_audit':template_audit(records,target),'classification_audit':classification_audit(records,target,self.config['classification']),
            'pair_audit':pair_generation_audit(records,pairs.pairs,self.config['ranking']),'corpus':corpus_report(eligible,pairs.pairs,self.config['quotas']),
            'selection':asdict(selection),'omitted_pair_counts':pairs.omitted,'source_failure_count':sum(r.get('status')=='source_failed' for r in records),
            'research_corpus_delivered':False,'execution_mode':'pilot' if pilot else 'production',
            'remaining_risks':['Real source yield and full-size performance require inspection. Heuristic pair scores do not measure human yield.']}
        report['scheduler_version']='host-aware-1'
        report['work_counts']=scheduler.report(records)
        report['deferred_count']=report['work_counts']['deferred']
        report['quarantined_count']=quarantined
        if self.reviewed_quarantine:
            report['quarantine_audit']=self.reviewed_quarantine.audit(records)
        if quarantined:
            report['critical_defects'].append({'code':'reviewed_integrity_quarantine','count':quarantined,
                'page_ids':[r['page_id'] for r in records if r.get('status')=='quarantined_integrity']})
        report['scheduler']={'retry_rounds':self.retry_rounds,'max_wait_seconds':self.max_wait_seconds,
            'wait_seconds':scheduler.waited,'candidate_attempts':scheduler.visits,'deferred_only':self.deferred_only,
            'checkpoint_restored':scheduler.checkpoint_restored,'host_pauses':scheduler.host_pauses}
        if self.host_health:report['host_preflight']=self.host_health
        performance['scheduler']=report['scheduler']
        performance['work_counts']=report['work_counts']
        # Completeness concerns acquisition/processing, not corpus eligibility.
        # An immutable optional frontier from an earlier invocation cannot be
        # repaired in place. Report it, without rewriting its logical input.
        from .scheduling import transient_source_code
        pinned_transient=sum(any(transient_source_code(f.get('source_code')) for f in
            r.get('classification',{}).get('dependency_manifest',{}).get('frontier',[])) for r in records)
        report['pinned_transient_manifest_count']=pinned_transient
        report['data_complete']=report['work_counts']['processed']==target and not pinned_transient and not quarantined
        report['pilot_accepted']=False
        report['acceptance_status']='blocked_integrity_quarantine' if quarantined else 'awaiting_review' if report['data_complete'] else 'blocked_incomplete_data'
        report['work_audit']={'counts':report['work_counts'],'articles':[
            {k:r.get(k) for k in ('page_id','status','error_code','retryable','retry_at','error_details')} for r in records]}
        prior=self.controls.report(target) if pilot else None
        report['replay']={'same_archived_cohort':prior.get('cohort_hash')==report['cohort_hash'] if prior else None,
            'same_selection':prior.get('selection')==report['selection'] if prior else None,'pair_manifest_hash':digest(pairs)}
        baseline=self.controls.report(1000) if target==10000 else None
        if baseline:
            report['scaling']={'cohort_ratio':target/baseline['size'],'elapsed_ratio':performance['end_to_end_seconds']/max(.001,baseline['performance']['end_to_end_seconds']),
                'baseline_peak_rss_bytes':baseline['performance']['resources'].get('peak_rss_bytes'),'baseline_request_rates':baseline['performance']['request_rates'],
                'warning':'Cumulative cohorts include reused candidates; compare new/reused work and cold/warm rates before extrapolating.'}
        report['pair_diagnostics']={'score_distribution':dict(Counter(p['score'] for p in pairs.pairs)),'positive_counts':dict(Counter(p['kind'] for p in pairs.pairs if p['score']>0)),
            'subtype_attribute_compatibility_blocks':dict(Counter(str(p['provenance'][0].get('block')) for p in pairs.pairs if p['kind']=='comparison')),
            'shortcut_risk_pair_count':sum(p['features']['shortcut_risk']>0 for p in pairs.pairs),'lead_bridge_count':sum(p['kind']=='bridge' for p in pairs.pairs),
            'partner_endpoint_coverage':len({p[k] for p in pairs.pairs for k in ('endpoint_a','endpoint_b')})}
        from .manifest_report import dependency_audits
        report['dependency_manifest_audit'],report['fingerprint_audit']=dependency_audits(records,self.store,self.run)
        from .manifest_report import subtype_audit
        report['subtype_audit']=subtype_audit(records)
        folder=self.reports/self.run_id/str(target)
        folder.mkdir(parents=True,exist_ok=True)
        self._preserve_latest(folder)
        attempt_folder=folder/'attempts'/report['attempt_id']
        attempt_folder.mkdir(parents=True)
        for filename,value in (('metrics.json',report),('template-audit.json',report['template_audit']),('classification-audit.json',report['classification_audit']),('performance.json',performance),('pair-audit.json',report['pair_audit'])):
            self._atomic(attempt_folder/filename,value)
        for name in ('dependency_manifest_audit','fingerprint_audit','subtype_audit','work_audit'):
            filename=name.replace('_','-')+'.json'
            self._atomic(folder/filename,report[name])
            self._atomic(attempt_folder/filename,report[name])
        if self.reviewed_quarantine:
            for parent in (folder,attempt_folder):self._atomic(parent/'quarantine-audit.json',report['quarantine_audit'])
        for record in records:
            manifest=record.get('classification',{}).get('dependency_manifest') or record.get('dependency_manifest')
            if manifest:
                for parent in (folder,attempt_folder):
                    (parent/'manifests').mkdir(exist_ok=True)
                    self._atomic(parent/'manifests'/(manifest['manifest_id']+'.json'),manifest)
        self._atomic(folder/'metrics.json',report)
        self._atomic(folder/'template-audit.json',report['template_audit'])
        self._atomic(folder/'classification-audit.json',report['classification_audit'])
        self._atomic(folder/'performance.json',performance)
        self._atomic(folder/'pair-audit.json',report['pair_audit'])
        self._atomic(folder/'cohort.json',candidates)
        self._atomic(folder/'summary.json',{k:report[k] for k in ('run_id','size','config_hash','snapshot','cohort_hash','source_failure_count','deferred_count','work_counts','data_complete','pilot_accepted','acceptance_status','research_corpus_delivered')})
        stage_lines='\n'.join(f'| {name} | {v["wall_seconds"]:.3f} | {v["active_seconds"]:.3f} | {v.get("new",0)} | {v.get("reused",0)} | {v.get("new_articles_per_minute")} |' for name,v in performance['stages'].items())
        summary=f'# Bounded {target} candidate assessment\n\nRun: {self.run_id}; snapshot: {self.run["snapshot"]}; config: {self.run["config_hash"]}.\n\nEligible: {len(eligible)}; unresolved extraction: {report["template_audit"]["unresolved"]}; source failures: {report["source_failure_count"]}.\n\n| Stage | Wall seconds | Active seconds | New | Reused | New articles/minute |\n|---|---:|---:|---:|---:|---:|\n{stage_lines}\n\nEnd-to-end seconds: {performance["end_to_end_seconds"]:.3f}. Stage times overlap and must not be summed. Queue time is zero for this serial scheduler; API backoff is separate.\n\nRequests/rates: {canonical(performance["request_rates"])}. Raw cache: {canonical(performance["cache"])}.\n\nPeak RSS bytes: {performance["resources"]["peak_rss_bytes"]}; disk growth bytes: {performance["resources"]["disk_growth_bytes"]}. Production ETA: {canonical(performance["production_eta"])}.\n\nNo real frozen corpus delivered. Human Valid 2-hop Yield is unassessed. Inspect metrics.json/template-audit.json and source risks before continuing. Production does not start automatically.\n'
        summary+=f'\nScheduler: host-aware-1. Work counts: {canonical(report["work_counts"])}. Data complete: {report["data_complete"]}. Pilot accepted: false ({report["acceptance_status"]}). A completed invocation or exit code 0 is not pilot acceptance.\n'
        (folder/'summary.md').write_text(summary,encoding='utf-8')
        if pilot:
            self.controls.save_report(report)
        # The primary reports, manifests, summaries and SQLite publication have
        # all been serialized while the sampler was active. Final telemetry is
        # a separately declared flush, avoiding an infinite self-measurement loop.
        sampler.finish()
        performance['resources']=sampler.report()
        performance['resources'].update(includes_report_exports=True,
            measurement_scope='Acquisition, processing, audit generation and primary report exports; excludes final telemetry flush')
        performance['processing_seconds']=performance['end_to_end_seconds']
        performance['end_to_end_seconds']=time.perf_counter()-start
        performance['report_export_seconds']=performance['end_to_end_seconds']-performance['processing_seconds']
        if performance['production_eta'].get('status')=='estimate' and performance['processing_seconds']:
            ratio=performance['end_to_end_seconds']/performance['processing_seconds']
            performance['production_eta']['seconds']*=ratio
            performance['production_eta']['uncertainty_seconds']=[v*ratio for v in performance['production_eta']['uncertainty_seconds']]
        if baseline:
            report['scaling']['elapsed_ratio']=performance['end_to_end_seconds']/max(.001,baseline['performance']['end_to_end_seconds'])
            report['scaling']['elapsed_scope']='including_primary_report_exports'
        report['ended_at']=now()
        for parent in (folder,attempt_folder):
            self._atomic(parent/'metrics.json',report)
            self._atomic(parent/'performance.json',performance)
        (folder/'summary.md').write_text(summary+f'\nIncluding primary exports: {performance["end_to_end_seconds"]:.3f} seconds; sampled peak RSS: {performance["resources"]["peak_rss_bytes"]} bytes. Final telemetry flush excluded.\n',encoding='utf-8')
        if pilot:self.controls.save_report(report)
        return report

    def _restore_terminal_records(self,candidates,*,allow_unseen=False):
        """Read checked terminal results without executing their stage workers."""
        queue={r['page_id']:dict(r) for r in self.store.db.execute('SELECT * FROM candidate_work WHERE run_id=?',(self.run_id,))}
        if not allow_unseen and any(c['page_id'] not in queue for c in candidates):
            raise Incompatible('Deferred-only resume requires a complete candidate queue checkpoint')
        saved={r['page_id']:r for r in self.records(len(candidates))}
        restored={}
        for candidate in candidates:
            page=candidate['page_id'];row=queue.get(page)
            if row is None:continue
            if row['status']=='processed':
                if page not in saved:raise Incompatible('Completed process checkpoint is missing; deferred-only resume stopped')
                record=saved[page]
                try:
                    if hasattr(self.provider,'validate_cached_result'):self.provider.validate_cached_result(record)
                except QuarantinedIntegrity as error:
                    if not self.reviewed_quarantine:raise
                    record=self.reviewed_quarantine.candidate_record(self.store,page,error)
                    from .scheduling import CandidateScheduler
                    CandidateScheduler(self.store,self.run_id).save(record,invoked=False)
                restored[page]=record
            elif row['status']=='source_failed':
                record=json.loads(row['record_json'])
                guard=getattr(getattr(self.provider,'source',None),'integrity_guard',None)
                if guard:guard(SourceUnavailable(record.get('error','Terminal source failure'),code=record.get('error_code','source_unavailable'),details=record.get('error_details',{})))
                restored[page]=record
            elif row['status']=='quarantined_integrity':
                if not self.reviewed_quarantine:raise Incompatible('Reviewed quarantine policy required')
                restored[page]=self.reviewed_quarantine.restore_candidate(self.store,page,json.loads(row['record_json']))
            elif row['status']!='deferred':raise Incompatible('Unknown candidate queue state')
        return restored

    def _enrich_failure(self,partial):
        """Retain completed upstream diagnostics even for queue-only deferrals."""
        identity=partial['page_id']
        lead=self.store.db.execute('SELECT record_json FROM lead_results WHERE run_id=? AND page_id=?',(self.run_id,identity)).fetchone()
        acquired=self.store.db.execute("SELECT result_json FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(self.run_id,str(identity))).fetchone()
        if lead and acquired:
            raw=json.loads(acquired[0])
            partial.update({'extraction':json.loads(lead[0]),'revision_id':raw['ref']['revision_id'],'raw_hash':raw['local_sha256']})
        classification=self.store.db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=? ORDER BY rowid DESC LIMIT 1',(self.run_id,identity)).fetchone()
        if classification:
            partial['previous_classification' if self.config['classification'].get('dependency_policy') else 'classification']=json.loads(classification[0])
        if self.config['classification'].get('dependency_policy'):
            binding=self.store.db.execute('SELECT request_hash FROM article_manifests WHERE run_id=? AND page_id=?',(self.run_id,identity)).fetchone()
            if binding:partial['dependency_manifest']=self.store.manifest_for(self.run_id,identity,binding[0])

    @staticmethod
    def _preserve_latest(folder):
        """Preserve pre-upgrade aliases too; attempts and logs are never replaced."""
        import shutil
        latest=folder/'metrics.json'
        if not latest.exists():return
        previous=json.loads(latest.read_text(encoding='utf-8'))
        # Content identity is safe even for legacy reports without attempt IDs.
        destination=folder/'previous-latest'/digest(previous)
        destination.mkdir(parents=True,exist_ok=True)
        for path in folder.iterdir():
            if path.is_file() and path.suffix in ('.json','.md'):
                target=destination/path.name
                if not target.exists():shutil.copy2(path,target)

    @staticmethod
    def _atomic(path,value):
        temporary=path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(canonical(value)+'\n',encoding='utf-8')
        temporary.replace(path)

    def store_path(self):
        return Path(self.store.db.execute('PRAGMA database_list').fetchone()[2]).parent

    def pilot(self,size):
        self.controls.pilot(size)
        paused=self._prepare_host_health(size,'pilot')
        if paused is not None:return paused
        # Each invocation measures only its own work, including warm resume.
        self.executor=StageExecutor(self.store,self.run_id,client=getattr(self.provider,'client',None))
        return self._process(size,True)

    def _prepare_host_health(self,size,mode):
        self.host_health=None;self._health_baseline=None
        self._health_started_at=None;self._health_started_clock=None
        if hasattr(self.provider,'preflight_deferred'):
            self._health_baseline=Counter(getattr(getattr(self.provider,'client',None),'metrics',{}))
            self._health_started_at=now();self._health_started_clock=time.perf_counter()
            self.host_health=self.provider.preflight_deferred()
            self._health_seconds=time.perf_counter()-self._health_started_clock
            if not self.host_health['allowed']:return self._host_pause(size,mode)
        return None

    def _host_pause(self,size,mode='pilot'):
        """A pause receipt is not a new data-processing pilot report."""
        states=Counter({r[0]:r[1] for r in self.store.db.execute('SELECT status,count(*) FROM candidate_work WHERE run_id=? GROUP BY status',(self.run_id,))})
        prior=self.controls.report(size)
        attempt=str(uuid.uuid4())
        folder=self.reports/self.run_id/str(size)/'health-checks'/attempt
        folder.mkdir(parents=True,exist_ok=False)
        path=folder/'health-report.json'
        requests={k:v-self._health_baseline[k] for k,v in getattr(getattr(self.provider,'client',None),'metrics',{}).items() if v!=self._health_baseline[k]}
        report={'run_id':self.run_id,'attempt_id':attempt,'size':size,'snapshot':self.run['snapshot'],'config_hash':self.run['config_hash'],
            'started_at':self._health_started_at,'ended_at':now(),'paused':True,'completed':False,'data_complete':False,'pilot_accepted':False,
            'report_scope':'host_preflight_no_candidate_processing','previous_pilot_attempt_id':prior.get('attempt_id') if prior else None,
            'execution_mode':mode,
            'checkpoint_candidate_count':sum(states.values()),
            'work_counts':{'attempted':0,'processed':states['processed'],'deferred':states['deferred'],'source_failed':states['source_failed']},
            'host_health':self.host_health,'health_report_path':str(path.resolve()),
            'performance':performance_report({},self._health_seconds,requests,{}, {'available_measurements':False,'measurement_scope':'Preflight paused before pipeline resource sampling'})}
        self._atomic(path,report)
        self._atomic(folder.parent.parent/'host-health.json',report)
        self.store.event(self.run_id,'host_paused',{'attempt_id':attempt,'health_report_path':str(path.resolve()),'host_health':self.host_health})
        if self.progress:self.progress({'event':'host_paused','run_id':self.run_id,'host_health':self.host_health,'health_report_path':str(path.resolve())})
        return report

    def production(self,report):
        self.controls.production(report)
        target=max(self.config['initial_target'],len(self.store.candidates(self.run_id)))
        paused=self._prepare_host_health(target,'production')
        if paused is not None:return paused
        return self._process(target,False)

    def extend(self,additional):
        if not self.store.run(self.run_id)['production_report']:
            raise InvalidConfig('Extend cannot cross a pilot boundary or initiate unapproved production')
        if type(additional) is not int or additional<=0:
            raise InvalidConfig('Additional candidate count must be positive')
        return self.collect(len(self.store.candidates(self.run_id))+additional)
