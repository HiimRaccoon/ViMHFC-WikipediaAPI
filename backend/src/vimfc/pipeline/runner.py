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


class StageExecutor:
    def __init__(self,store,run_id):
        self.store,self.run_id=store,run_id
        self.run=store.run(run_id)
        self.metrics=defaultdict(lambda:{'active_seconds':0,'new':0,'reused':0,'failed':0})

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
                raise SourceUnavailable('Wait for the recorded retry time; use pipeline retry when eligible',retryable=True,code='stage_cooldown',retry_at=row['next_retry_at'])
            if row and row['lease_until'] and row['lease_until']>stamp:
                raise SourceUnavailable('Stage worker lease is still active',retryable=True)
            if row and row['status']=='failed' and not row['next_retry_at']:
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
            self.store.db.execute("UPDATE stage_results SET status='failed',lease_until=NULL,last_error=?,next_retry_at=? WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=? AND lease_token=? AND status='pending'",(str(error),retry,*key,token))
            metric['failed']+=1
            raise
        finally:
            metric['active_seconds']+=time.perf_counter()-start
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
            if not report or not report['completed']:
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
        if report.get('size')!=10000 or not report.get('completed') or report.get('critical_defects'):
            raise InvalidConfig('Final completed 10000 pilot with no critical defects is required')
        archived=self.report(10000)
        if not archived or digest(archived)!=digest(report):
            raise InvalidConfig('Reviewed report differs from the archived final pilot')
        for size in (100,1000):
            prior=self.report(size)
            if not prior or not prior['completed'] or prior['config_hash']!=run['config_hash']:
                raise InvalidConfig('Required final-rule staged reports are missing')
        self.store.db.execute('UPDATE pipeline_runs SET production_report=?,updated_at=? WHERE pipeline_run_id=?',(digest(report),now(),self.run_id))


class Builder:
    def __init__(self,store,run_id,provider,reports='reports/pilots',*,progress=None):
        self.store,self.run_id,self.provider=store,run_id,provider
        self.run=store.run(run_id)
        self.config=self.run['config']
        self.executor=StageExecutor(store,run_id)
        self.controls=PilotControls(store,run_id)
        self.reports=Path(reports)
        self.progress=progress

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
            result=self.store.result(self.run_id,candidate['page_id'],'process',digest(deps))
            if result:
                output.append(result)
        return output

    def _process(self,target,pilot):
        started=now()
        start=time.perf_counter()
        records=[]
        progress=ProgressWindow(target)
        progress.observe(start,0)
        request_start=Counter(getattr(getattr(self.provider,'client',None),'metrics',{}))
        source=getattr(self.provider,'source',None)
        cache_start={'hits':getattr(source,'cache_hits',0),'lookups':getattr(source,'cache_lookups',0)}
        source_metrics_start=Counter(getattr(source,'metrics',{}))
        hierarchy_metrics_start=Counter(getattr(getattr(self.provider,'wikidata',None),'metrics',{}))
        provider_executor=getattr(self.provider,'executor',None)
        if provider_executor:
            provider_executor.metrics.clear()
        with ResourceSampler(self.store_path(),self.config.get('sample_interval_seconds',1)) as sampler:
            mark=time.perf_counter()
            candidates=self.collect(target)
            discovery_seconds=time.perf_counter()-mark
            if self.progress:
                self.progress({'event':'cohort_ready','run_id':self.run_id,'candidates':len(candidates),'snapshot':self.run['snapshot']})
            for candidate in candidates:
                deps={'snapshot':self.run['snapshot'],'config':self.run['config_hash'],'candidate':candidate}
                prepared={}
                try:
                    if hasattr(self.provider,'process_dependencies'):
                        prepared=self.provider.process_dependencies(candidate)
                        if prepared:deps['input']=prepared
                    result=self.executor.execute('process',candidate['page_id'],deps,
                        lambda c=candidate:self.provider.process_prepared(c,prepared) if prepared and hasattr(self.provider,'process_prepared') else self.provider.process(c))
                    records.append(result)
                except SourceUnavailable as error:
                    partial={'page_id':candidate['page_id'],'status':'source_failed','error':str(error),'retryable':error.retryable,'retry_at':error.retry_at,'error_code':error.code,'error_details':error.details}
                    lead=self.store.db.execute('SELECT record_json FROM lead_results WHERE run_id=? AND page_id=?',(self.run_id,candidate['page_id'])).fetchone()
                    acquired=self.store.db.execute("SELECT result_json FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(self.run_id,str(candidate['page_id']))).fetchone()
                    if lead and acquired:
                        raw=json.loads(acquired[0])
                        partial.update({'extraction':json.loads(lead[0]),'revision_id':raw['ref']['revision_id'],'raw_hash':raw['local_sha256']})
                    classification=self.store.db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=? ORDER BY rowid DESC LIMIT 1',(self.run_id,candidate['page_id'])).fetchone()
                    if classification:
                        partial['previous_classification' if self.config['classification'].get('dependency_policy') else 'classification']=json.loads(classification[0])
                    if self.config['classification'].get('dependency_policy'):
                        binding=self.store.db.execute('SELECT request_hash FROM article_manifests WHERE run_id=? AND page_id=?',(self.run_id,candidate['page_id'])).fetchone()
                        if binding:partial['dependency_manifest']=self.store.manifest_for(self.run_id,candidate['page_id'],binding[0])
                    records.append(partial)
                finally:
                    if hasattr(self.provider,'release_prepared'):self.provider.release_prepared(candidate)
                self.store.event(self.run_id,'progress',{**progress.observe(time.perf_counter(),len(records)),'cache_reused':self.executor.metrics['process']['reused']})
                if self.progress:
                    self.progress({'event':'candidate_processed','run_id':self.run_id,'page_id':candidate['page_id'],'processed':len(records),'target':target,
                        'status':records[-1].get('status'),'classification':records[-1].get('classification',{}).get('outcome')})
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
                self.store.db.execute('UPDATE pipeline_runs SET status=?,updated_at=? WHERE pipeline_run_id=?',('selecting' if selection.shortage else 'ready_to_freeze',now(),self.run_id))
        metrics=dict(self.executor.metrics)
        if hasattr(self.provider,'executor'):
            metrics.update(self.provider.executor.metrics)
        for stage in ('acquire','extract','classify','resolve_links','quality'):
            metrics.setdefault(stage,{'active_seconds':0,'new':0,'reused':0,'failed':0,'executed':False})
        metrics.update({'discovery':{'active_seconds':discovery_seconds,'new':0,'reused':0},'pairs':{'active_seconds':pair_seconds,'new':0,'reused':0},'selection':{'active_seconds':selection_seconds,'new':0,'reused':0}})
        physical={k:v-request_start[k] for k,v in getattr(getattr(self.provider,'client',None),'metrics',{}).items() if v!=request_start[k]}
        backoff=sum(v for k,v in physical.items() if k.endswith(':backoff_seconds'))
        for name,metric in metrics.items():
            metric['wall_seconds']=metric['active_seconds']
            source_stage={'acquire':'historical_acquisition','classify':'historical_classification','resolve_links':'lead_identity_dependencies','discovery':'discovery_and_identity'}.get(name)
            metric['backoff_seconds']=backoff if name=='process' else sum(v for k,v in physical.items() if source_stage and k.endswith(':'+source_stage+':backoff_seconds'))
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
        attempt_folder=folder/'attempts'/report['attempt_id']
        attempt_folder.mkdir(parents=True)
        for filename,value in (('metrics.json',report),('template-audit.json',report['template_audit']),('classification-audit.json',report['classification_audit']),('performance.json',performance),('pair-audit.json',report['pair_audit'])):
            self._atomic(attempt_folder/filename,value)
        for name in ('dependency_manifest_audit','fingerprint_audit','subtype_audit'):
            filename=name.replace('_','-')+'.json'
            self._atomic(folder/filename,report[name])
            self._atomic(attempt_folder/filename,report[name])
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
        self._atomic(folder/'summary.json',{k:report[k] for k in ('run_id','size','config_hash','snapshot','cohort_hash','source_failure_count','research_corpus_delivered')})
        stage_lines='\n'.join(f'| {name} | {v["wall_seconds"]:.3f} | {v["active_seconds"]:.3f} | {v.get("new",0)} | {v.get("reused",0)} | {v.get("new_articles_per_minute")} |' for name,v in performance['stages'].items())
        summary=f'# Bounded {target} candidate assessment\n\nRun: {self.run_id}; snapshot: {self.run["snapshot"]}; config: {self.run["config_hash"]}.\n\nEligible: {len(eligible)}; unresolved extraction: {report["template_audit"]["unresolved"]}; source failures: {report["source_failure_count"]}.\n\n| Stage | Wall seconds | Active seconds | New | Reused | New articles/minute |\n|---|---:|---:|---:|---:|---:|\n{stage_lines}\n\nEnd-to-end seconds: {performance["end_to_end_seconds"]:.3f}. Stage times overlap and must not be summed. Queue time is zero for this serial scheduler; API backoff is separate.\n\nRequests/rates: {canonical(performance["request_rates"])}. Raw cache: {canonical(performance["cache"])}.\n\nPeak RSS bytes: {performance["resources"]["peak_rss_bytes"]}; disk growth bytes: {performance["resources"]["disk_growth_bytes"]}. Production ETA: {canonical(performance["production_eta"])}.\n\nNo real frozen corpus delivered. Human Valid 2-hop Yield is unassessed. Inspect metrics.json/template-audit.json and source risks before continuing. Production does not start automatically.\n'
        (folder/'summary.md').write_text(summary,encoding='utf-8')
        if pilot:
            self.controls.save_report(report)
        return report

    @staticmethod
    def _atomic(path,value):
        temporary=path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(canonical(value)+'\n',encoding='utf-8')
        temporary.replace(path)

    def store_path(self):
        return Path(self.store.db.execute('PRAGMA database_list').fetchone()[2]).parent

    def pilot(self,size):
        self.controls.pilot(size)
        # Each invocation measures only its own work, including warm resume.
        self.executor=StageExecutor(self.store,self.run_id)
        return self._process(size,True)

    def production(self,report):
        self.controls.production(report)
        return self._process(max(self.config['initial_target'],len(self.store.candidates(self.run_id))),False)

    def extend(self,additional):
        if not self.store.run(self.run_id)['production_report']:
            raise InvalidConfig('Extend cannot cross a pilot boundary or initiate unapproved production')
        if type(additional) is not int or additional<=0:
            raise InvalidConfig('Additional candidate count must be positive')
        return self.collect(len(self.store.candidates(self.run_id))+additional)
