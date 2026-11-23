"""One-writer FAST-20K campaign. Indexed work, finite HTTP budget, optional Qwen."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

import httpx

from vimfc.domain import Incompatible, SourceUnavailable, canonical, digest, now
from vimfc.source.dump_download import exclusive
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from .collection_bundle import read_bundle
from .discovery_policy import SNAPSHOT
from .seed_only import plan_seed_cohort, seed_policy
from .provider import HistoricalProvider
from .incremental_collection import _checked_acquisition, _publish_json, failure_kind, CollectionBudget
from .first_paragraph import extract_first_paragraph, VERSION
from .relaxed_pilot import relaxed_rules
from .relaxed_archive import CheckedArchive
from .quality import assess_quality


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_profile(profile, *, fixture=False):
    for key in ('target','max_candidates','request_limit','batch_size','checkpoint_every_articles'):
        if type(profile.get(key)) is not int or profile[key]<=0:
            raise Incompatible('FAST-20K positive integer required: '+key)
    interval=profile['min_interval_seconds']
    if type(interval) not in (int,float) or not math.isfinite(interval):
        raise Incompatible('FAST-20K pacing must be finite')
    if (profile['version'] != 'fast20k-1' or profile['snapshot'] != SNAPSHOT
            or profile['extraction_policy'] != VERSION or profile['quality_policy'] != 'quality-relaxed-1'
            or profile['edge_policy'] != 'annotation-edge-1' or profile['ranking_policy'] != 'rank-annotation-1'
            or not 0 < profile['max_candidates'] <= 100000 or not 0 < profile['target'] <= 20000
            or profile['request_limit'] <= 0 or profile['min_interval_seconds'] < 1
            or (not fixture and (profile['target'] != 20000 or profile['max_candidates'] != 100000))):
        raise Incompatible('FAST-20K profile differs from authorized bounds/policies')


def prepare(bundle, output, profile, *, fixture=False):
    validate_profile(profile, fixture=fixture)
    output, bundle = Path(output).resolve(), Path(bundle).resolve()
    if output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output):
        raise Incompatible('FAST-20K needs a new workspace separate from the immutable bundle')
    manifest, source, resolver = read_bundle(bundle)
    inventory = load(bundle/'inventory.json')
    if inventory['inventory_id'] != manifest['inventory_id'] or source['snapshot'] != SNAPSHOT:
        raise Incompatible('FAST-20K inventory/snapshot binding differs')
    # Read source/config only through owned copies. No old SQLite sidecar is opened writable.
    output.mkdir(parents=True)
    shutil.copyfile(source['database'], output/'source-read.sqlite')
    shutil.copyfile(bundle/'registry.sqlite', output/'registry-read.sqlite')
    shutil.copytree(source['raw_cache'], output/'raw-cache')
    source = {**source, 'database': str(output/'source-read.sqlite'), 'raw_cache': str(output/'raw-cache')}
    with CheckedArchive(source, output/'registry-read.sqlite', artifact_resolver=resolver) as archive:
        cfg = deepcopy(archive.config)
        plan = plan_seed_cohort(inventory, seed_policy(cfg['quotas']), profile['max_candidates'])
        # Preserve seed-only ordering within each partition, but reuse available raw first.
        cached={int(r[0]) for r in archive.db.execute(
            "SELECT identity FROM snapshot_revision_refs WHERE site='viwiki' AND snapshot=?",(SNAPSHOT,)) if r[0].isdecimal()}
        plan['candidates'].sort(key=lambda c:c['page_id'] not in cached)
        plan['cohort_hash']=digest(plan['candidates'])
        plan['ordering']='cached-historical-reference-first-1'
        plan['manifest_id']=digest({k:v for k,v in plan.items() if k!='manifest_id'})
        cfg.update(version='fast20k-1', fast20k=deepcopy(profile))
        cfg['extraction']['version'] = VERSION
        cfg['discovery'] = dict(version='seed-only-2', inventory_id=inventory['inventory_id'],
            manifest_id=plan['manifest_id'], cohort_hash=plan['cohort_hash'], namespace_policy='historical-title-namespace-1', variant='seed-only')
        with PipelineStore(output/'pipeline.sqlite') as store:
            run = store.create(SNAPSHOT, cfg)
            with store.transaction():
                store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',
                    [(run,c['page_id'],i,canonical(c)) for i,c in enumerate(plan['candidates'])])
                # Raw lookup metadata is compatible across extraction versions; derived stages are not copied.
                for row in archive.db.execute("SELECT * FROM source_revisions WHERE site='viwiki'"):
                    store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)',tuple(row))
                for row in archive.db.execute("SELECT * FROM snapshot_revision_refs WHERE site='viwiki' AND snapshot=?",(SNAPSHOT,)):
                    store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',tuple(row))
                for row in archive.db.execute('SELECT * FROM identity_resolutions WHERE snapshot=?',(SNAPSHOT,)):
                    store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)',tuple(row))
                for row in archive.db.execute("SELECT identity,dependency_hash,result_json,result_hash FROM stage_results WHERE stage='verify_namespace' AND status='completed'"):
                    result=json.loads(row[2])
                    if digest(result)!=row[3]:raise Incompatible('Archived namespace checkpoint checksum differs')
                    existing=store.result(run,row[0],'verify_namespace',row[1])
                    if existing is not None and existing!=result:raise Incompatible('Conflicting archived namespace proof')
                    store.complete(run,row[0],'verify_namespace',row[1],result)
            store.db.executescript('''
                CREATE TABLE fast_work(page_id INTEGER PRIMARY KEY, ordinal INTEGER UNIQUE NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending', tries INTEGER NOT NULL DEFAULT 0,
                  retry_at TEXT, eligible INTEGER NOT NULL DEFAULT 0, record_json TEXT, record_hash TEXT, error_json TEXT);
                CREATE INDEX fast_pending ON fast_work(status,ordinal);
                CREATE INDEX fast_retry ON fast_work(status,retry_at,ordinal);
                CREATE INDEX fast_eligible ON fast_work(eligible);
                CREATE TABLE fast_control(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE fast_qwen(page_id INTEGER PRIMARY KEY,status TEXT NOT NULL DEFAULT 'pending',
                  record_json TEXT,raw_json TEXT);
            ''')
            with store.transaction():
                store.db.executemany('INSERT INTO fast_work(page_id,ordinal) VALUES (?,?)',[(c['page_id'],i) for i,c in enumerate(plan['candidates'])])
            context=dict(database='pipeline.sqlite',raw_cache='raw-cache',run_id=run,snapshot=SNAPSHOT,
                         config_hash=digest(cfg),cohort_hash=plan['cohort_hash'])
            header=dict(format='fast20k-workspace-1',context=context,bundle_id=manifest['bundle_id'],
                inventory_id=inventory['inventory_id'],profile=profile,
                source_context={k:source[k] for k in ('run_id','snapshot','config_hash','cohort_hash')},
                candidate_count=len(plan['candidates']),selection_manifest_id=plan['manifest_id'],fixture=fixture)
            header['binding_id']=digest(header)
            store.db.executemany('INSERT INTO fast_control VALUES (?,?)', [('binding',canonical(header)),('attempts','0'),('last_request','0')])
            _publish_json(output/'workspace.json',header)
    return header


class CampaignTransport(httpx.BaseTransport):
    """Final HTTPX boundary for this separately authorized acquisition variant."""
    def __init__(self, store, durable, profile, *, delegate=None, clock=time.time, sleep=time.sleep, authorized=False):
        self.store,self.durable,self.profile=store,Path(durable),profile
        self.delegate=delegate if delegate is not None else httpx.HTTPTransport(retries=0,trust_env=False)
        self.clock,self.sleep=clock,sleep
        self.authorized=authorized

    def restore_floor(self):
        local=dict(self.store.db.execute("SELECT key,value FROM fast_control WHERE key IN ('attempts','last_request')"))
        binding=self.store.db.execute("SELECT value FROM fast_control WHERE key='binding'").fetchone()[0]
        expected=digest(json.loads(binding))
        path=self.durable/'http-budget.json'
        previous=load(path) if path.exists() else dict(binding=expected,attempts=0,last_request=0)
        if (previous['binding']!=expected or type(previous['attempts']) is not int or previous['attempts']<0
                or not math.isfinite(previous['last_request']) or previous['last_request']<0):
            raise Incompatible('FAST-20K persistent budget binding/counter differs')
        attempts=max(int(local['attempts']),previous['attempts'])
        last=max(float(local['last_request']),previous['last_request'])
        with self.store.transaction():
            self.store.db.execute("UPDATE fast_control SET value=? WHERE key='attempts'",(str(attempts),))
            self.store.db.execute("UPDATE fast_control SET value=? WHERE key='last_request'",(str(last),))
        return expected,attempts,last

    def handle_request(self, request):
        from .budget_transport import final_request
        if type(self.delegate) is not httpx.MockTransport and (not self.authorized or os.environ.get('VIMFC_OFFLINE','').casefold() in ('1','true','yes')):
            raise SourceUnavailable('FAST-20K source network disabled',code='offline')
        pin=final_request(request)  # GET/HTTPS/exact viwiki endpoint, maxlag and Host pin.
        budget_path=self.durable/'http-budget.json'
        expected,attempts,last=self.restore_floor()
        if attempts>=self.profile['request_limit']: raise CollectionBudget('FAST-20K HTTP request budget exhausted')
        delay=max(0,last+self.profile['min_interval_seconds']-self.clock())
        if delay>5: raise Incompatible('FAST-20K acquisition clock moved backwards')
        if delay:self.sleep(delay)
        stamp=self.clock()
        if stamp<last+self.profile['min_interval_seconds']: raise Incompatible('FAST-20K pacing deadline not reached')
        charged=dict(binding=expected,attempts=attempts+1,last_request=stamp,
                     request_id=uuid.uuid4().hex,request_sha256=digest(pin))
        # Persistent conservative receipt precedes I/O; restoring an older workspace cannot refund it.
        _publish_json(budget_path,charged)
        with self.store.transaction():
            self.store.db.execute("UPDATE fast_control SET value=? WHERE key='attempts'",(str(attempts+1),))
            self.store.db.execute("UPDATE fast_control SET value=? WHERE key='last_request'",(str(stamp),))
        return self.delegate.handle_request(request)

    def close(self): self.delegate.close()


def progress(store, header, reason=None):
    states=dict(store.db.execute('SELECT status,count(*) FROM fast_work GROUP BY status'))
    eligible=store.db.execute('SELECT count(*) FROM fast_work WHERE eligible=1').fetchone()[0]
    return dict(format='fast20k-progress-1',run_id=header['context']['run_id'],snapshot=SNAPSHOT,
        binding_id=header['binding_id'],target=header['profile']['target'],candidate_limit=header['candidate_count'],
        eligible_articles=eligible,states=states,stop_reason=reason,
        physical_requests=int(store.db.execute("SELECT value FROM fast_control WHERE key='attempts'").fetchone()[0]),
        qwen_states=dict(store.db.execute('SELECT status,count(*) FROM fast_qwen GROUP BY status')),
        eligible_target_reached=eligible>=header['profile']['target'],corpus_complete=False,updated_at=now())


def checkpoint(store, workspace, durable, header, reason=None):
    """Stopped main writer; closed backup DB and complete immutable raw set on persistent storage."""
    workspace,durable=Path(workspace),Path(durable)
    durable.mkdir(parents=True,exist_ok=True)
    snapshot=durable/('checkpoint-'+uuid.uuid4().hex)
    snapshot.mkdir()
    with closing(sqlite3.connect(snapshot/'pipeline.sqlite')) as backup:
        store.db.backup(backup)
        backup.execute('PRAGMA journal_mode=DELETE')
    # Copy only missing content-addressed objects. Existing destination bytes must agree.
    target=durable/'raw-cache';target.mkdir(exist_ok=True)
    for file in (workspace/'raw-cache').rglob('*.gz'):
        dest=target/file.relative_to(workspace/'raw-cache')
        if dest.exists():
            if dest.stat().st_size!=file.stat().st_size:raise Incompatible('FAST-20K persistent raw cache differs')
        else:
            dest.parent.mkdir(parents=True,exist_ok=True)
            temporary=dest.with_suffix('.pending')
            shutil.copyfile(file,temporary);temporary.replace(dest)
    _publish_json(snapshot/'workspace.json',header)
    with (snapshot/'pipeline.sqlite').open('rb') as f:db_hash=hashlib.file_digest(f,'sha256').hexdigest()
    receipt=dict(directory=snapshot.name,db_sha256=db_hash,binding_id=header['binding_id'],progress=progress(store,header,reason))
    try:
        from .fast20k_corpus import pool_report
        report=pool_report(workspace,header)
        _publish_json(durable/'coverage.json',report)
        receipt['progress']['coverage']=dict(path='coverage.json',sha256=digest(report))
    except Exception as error:
        # Coverage cannot prevent saving acquired work; final publication still validates strictly.
        receipt['progress']['coverage_error']=dict(type=type(error).__name__,message=str(error))
    _publish_json(snapshot/'receipt.json',receipt)
    _publish_json(durable/'latest.json',receipt)
    _publish_json(durable/'progress.json',receipt['progress'])
    _publish_json(workspace/'progress.json',receipt['progress'])
    # Retain latest and two preceding owned DB generations, not unbounded full copies.
    generations=sorted((p for p in durable.glob('checkpoint-*') if p.is_dir()),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for old in generations[3:]:
        if old.resolve().parent!=durable.resolve() or len(old.name)!=43:
            raise Incompatible('Unsafe FAST-20K checkpoint retention path')
        shutil.rmtree(old)
    return receipt


def restore(bundle,workspace,durable,profile):
    """Restore a complete stopped-writer snapshot into a NEW local directory only."""
    workspace,durable=Path(workspace).resolve(),Path(durable).resolve()
    if workspace.exists():raise Incompatible('FAST-20K restore output must be new')
    receipt=load(durable/'latest.json')
    name=receipt['directory']
    if not name.startswith('checkpoint-') or len(name)!=43 or Path(name).name!=name:
        raise Incompatible('Invalid FAST-20K checkpoint path')
    source=durable/name
    with (source/'pipeline.sqlite').open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=receipt['db_sha256']:raise Incompatible('FAST-20K checkpoint checksum differs')
    header=load(source/'workspace.json')
    if header['binding_id']!=receipt['binding_id'] or header['profile']!=profile:
        raise Incompatible('FAST-20K restore binding/config differs')
    manifest,context,_=read_bundle(bundle)
    if manifest['bundle_id']!=header['bundle_id']:raise Incompatible('FAST-20K restore bundle differs')
    workspace.mkdir(parents=True)
    shutil.copyfile(source/'pipeline.sqlite',workspace/'pipeline.sqlite')
    shutil.copyfile(source/'workspace.json',workspace/'workspace.json')
    shutil.copyfile(context['database'],workspace/'source-read.sqlite')
    shutil.copyfile(Path(bundle)/'registry.sqlite',workspace/'registry-read.sqlite')
    shutil.copytree(durable/'raw-cache',workspace/'raw-cache')
    return header


def run(bundle,workspace,durable,profile,*,allow_network=False,client_factory=None,qwen=None,
        fixture=False,stop_requested=lambda:False,on_checkpoint=None):
    validate_profile(profile,fixture=fixture)
    workspace,durable=Path(workspace).resolve(),Path(durable).resolve()
    if workspace==durable or workspace.is_relative_to(durable) or durable.is_relative_to(workspace):
        raise Incompatible('Use separate node-local workspace and persistent checkpoint root')
    if not allow_network and client_factory is None:
        os.environ['VIMFC_OFFLINE']='1'
    durable.mkdir(parents=True,exist_ok=True)
    with exclusive(durable/'.campaign.lock'):
        if not (workspace/'workspace.json').exists():
            if (durable/'latest.json').exists():restore(bundle,workspace,durable,profile)
            else:prepare(bundle,workspace,profile,fixture=fixture)
        header=load(workspace/'workspace.json')
        if (header['binding_id']!=digest({k:v for k,v in header.items() if k!='binding_id'})
                or header['profile']!=profile or header['fixture']!=fixture):
            raise Incompatible('FAST-20K checkpoint/configuration binding differs')
        context={**header['context'],'database':str(workspace/'pipeline.sqlite'),'raw_cache':str(workspace/'raw-cache')}
        _,source,resolver=read_bundle(bundle)
        if header['bundle_id']!=load(Path(bundle)/'bundle.json')['bundle_id']:raise Incompatible('FAST-20K bundle identity differs')
        source={**source,'database':str(workspace/'source-read.sqlite'),'raw_cache':str(workspace/'raw-cache')}
        reason=None;count=0;future=None;pool=ThreadPoolExecutor(max_workers=1) if qwen else None
        with PipelineStore(context['database']) as store,CheckedArchive(source,workspace/'registry-read.sqlite',artifact_resolver=resolver) as archive:
            if canonical(header)!=store.db.execute("SELECT value FROM fast_control WHERE key='binding'").fetchone()[0]:
                raise Incompatible('FAST-20K database binding differs')
            store.require_config(context['run_id'],store.run(context['run_id'])['config'])
            cfg=store.run(context['run_id'])['config']
            fatal=store.db.execute("SELECT value FROM fast_control WHERE key='fatal'").fetchone()
            if fatal:raise Incompatible('FAST-20K previous integrity failure requires review: '+fatal[0])
            transport=CampaignTransport(store,durable,profile,authorized=allow_network)
            transport.restore_floor()
            factory=client_factory or (lambda:MediaWikiClient(cfg['contact'],user_agent=cfg['user_agent'],transport=transport,attempts=1))
            def finish_qwen(wait=False):
                nonlocal future
                if future and (wait or future[1].done()):
                    page,task=future
                    try:suggestion,raw_response=task.result()
                    except Exception as error:
                        from .qwen_fallback import PROMPT_VERSION,PARAMETERS
                        suggestion=dict(classification_status='failed',suggested_primary_entity_type=None,
                            classification_source='qwen_suggestion',model_id=profile['qwen']['model_id'],
                            model_revision=profile['qwen']['model_revision'],prompt_version=PROMPT_VERSION,
                            inference_parameters=PARAMETERS,
                            error_type=type(error).__name__,error=str(error),uncertainty=True)
                        raw_response=None
                    suggestion['raw_response_reference']=dict(table='fast_qwen',page_id=page,sha256=digest(raw_response))
                    store.db.execute('UPDATE fast_qwen SET status=?,record_json=?,raw_json=? WHERE page_id=?',
                        (suggestion['classification_status'],canonical(suggestion),canonical(raw_response),page));future=None
            def start_qwen():
                nonlocal future
                if not qwen or future is not None:return
                pending=store.db.execute("SELECT q.page_id,w.record_json FROM fast_qwen q JOIN fast_work w USING(page_id) WHERE q.status='pending' ORDER BY w.ordinal LIMIT 1").fetchone()
                if pending:
                    c=json.loads(store.db.execute('SELECT record_json FROM candidates WHERE run_id=? AND canonical_page_id=?',(context['run_id'],pending[0])).fetchone()[0])
                    future=(pending[0],pool.submit(qwen.classify,json.loads(pending[1]),c.get('expected_strata',[])))
            try:
                with factory() as client:
                    # An explicit custom client is permitted only for offline fixtures.
                    if client_factory is not None and (not fixture or type(client.client._transport) is not httpx.MockTransport):
                        raise Incompatible('FAST-20K custom clients are offline test fixtures only')
                    if client_factory is not None:client.client._transport=CampaignTransport(store,durable,profile,delegate=client.client._transport)
                    provider=HistoricalProvider(store,context['run_id'],client,RawCache(context['raw_cache']),classification_required=False)
                    provider.source.integrity_registry=archive.registry
                    consecutive_failures=0
                    while True:
                        finish_qwen()
                        start_qwen()
                        eligible=store.db.execute('SELECT count(*) FROM fast_work WHERE eligible=1').fetchone()[0]
                        if eligible>=profile['target']:reason='TARGET_REACHED';break
                        if stop_requested():reason='CHECKPOINT_STOP';break
                        cooldown=store.db.execute("SELECT retry_at FROM api_cooldowns WHERE site='viwiki'").fetchone()
                        if cooldown and cooldown[0]>now():
                            # No request or retry loop during a host cooldown. Pending work is retained.
                            finish_qwen(True);checkpoint(store,workspace,durable,header,'SOURCE_COOLDOWN')
                            seconds=(datetime.fromisoformat(cooldown[0].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
                            time.sleep(min(30,max(0,seconds)))
                            continue
                        row=store.db.execute("SELECT page_id,tries FROM fast_work WHERE status='pending' ORDER BY ordinal LIMIT 1").fetchone()
                        if row is None:
                            row=store.db.execute("SELECT page_id,tries FROM fast_work WHERE status='deferred' AND retry_at<=? AND tries<3 ORDER BY ordinal LIMIT 1",(now(),)).fetchone()
                        if row is None:
                            deferred=store.db.execute("SELECT min(retry_at) FROM fast_work WHERE status='deferred' AND tries<3").fetchone()[0]
                            reason='SOURCE_COOLDOWN' if deferred else 'CANDIDATE_EXHAUSTED';break
                        page,tries=row
                        candidate=json.loads(store.db.execute('SELECT record_json FROM candidates WHERE run_id=? AND canonical_page_id=?',(context['run_id'],page)).fetchone()[0])
                        store.db.execute('UPDATE fast_work SET tries=tries+1 WHERE page_id=?',(page,))
                        try:
                            _checked_acquisition(provider,candidate)
                            raw=provider._acquire_raw(candidate);ref=raw.ref
                            article=dict(site='viwiki',page_id=page,title=ref.title,revision_id=ref.revision_id,
                                revision_timestamp=ref.revision_timestamp,content_hash=raw.local_sha256)
                            scope=provider._discovery_namespace(raw)
                            proof=dict(article=article,snapshot=SNAPSHOT,cache_ref=raw.cache_ref,
                                namespace_scope=scope,source_sha1=raw.source_sha1,policy='relaxed-source-1')
                            proof['manifest_id']=digest(proof)
                            extraction=asdict(extract_first_paragraph(raw.content,cfg['extraction']))
                            cls=dict(outcome='unclassified',primary_entity_type=None,subtypes=[],reason='classification_optional_collection')
                            # Keep archived classification separate unless source identity and checkpoint hash agree.
                            old=archive.db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='process' AND status='completed'",(source['run_id'],str(page))).fetchone()
                            if old:
                                previous=json.loads(old[0])
                                if digest(previous)!=old[1]:raise Incompatible('FAST-20K archived metadata checksum mismatch')
                                if all(previous.get(k)==v for k,v in dict(page_id=page,revision_id=ref.revision_id,raw_hash=raw.local_sha256,snapshot=SNAPSHOT).items()):
                                    old_cls=previous['classification']
                                    # Archived metadata is explicitly separate from independently replayed proof.
                                    cls={k:deepcopy(old_cls[k]) for k in ('outcome','primary_entity_type','subtypes','reason') if k in old_cls}
                                    cls.update(classification_source='archived_project_metadata',
                                        historical_proof_replayed=False,source_record_hash=old[1],
                                        source_policy=archive.config['classification']['version'])
                            kinds=archive.article_kinds(raw.content,ref.title)
                            record=dict(page_id=page,title=ref.title,revision_id=ref.revision_id,revision_timestamp=ref.revision_timestamp,
                                source_url=f'https://vi.wikipedia.org/w/index.php?oldid={ref.revision_id}',snapshot=SNAPSHOT,
                                raw_hash=raw.local_sha256,source_validation=proof,article_kinds=kinds,
                                extraction=extraction,classification=cls,origin=candidate.get('discovery_origins',[]),
                                run_id=context['run_id'],config_hash=context['config_hash'])
                            record['quality']=assess_quality(extraction,cls,kinds,relaxed_rules(cfg))
                            consecutive_failures=0
                            with store.transaction():
                                store.db.execute("UPDATE fast_work SET status='processed',eligible=?,record_json=?,record_hash=?,error_json=NULL WHERE page_id=?",
                                    (int(record['quality']['eligible']),canonical(record),digest(record),page))
                                if record['quality']['eligible'] and not cls.get('primary_entity_type'):
                                    store.db.execute('INSERT OR IGNORE INTO fast_qwen(page_id) VALUES (?)',(page,))
                        except SourceUnavailable as error:
                            kind=failure_kind(error)
                            if kind=='integrity_fatal':raise
                            status='deferred' if error.retryable else 'missing' if kind=='missing' else 'unsupported'
                            retry_at=error.retry_at
                            if error.retryable and not retry_at:
                                from datetime import timedelta
                                retry_at=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat().replace('+00:00','Z')
                            store.db.execute('UPDATE fast_work SET status=?,retry_at=?,error_json=? WHERE page_id=?',
                                (status,retry_at,canonical(dict(type=type(error).__name__,message=str(error),code=error.code)),page))
                            if error.retryable:
                                consecutive_failures+=1
                                if consecutive_failures>=3:reason='SOURCE_COOLDOWN';break
                                continue
                            if kind not in ('missing','unsupported'):
                                reason='OPERATIONAL_ERROR'
                                _publish_json(durable/'last-error.json',dict(type=type(error).__name__,code=error.code,message=str(error)))
                                break
                        count+=1
                        if count%profile['checkpoint_every_articles']==0:
                            finish_qwen(True);receipt=checkpoint(store,workspace,durable,header)
                            if on_checkpoint:on_checkpoint(receipt)
            except CollectionBudget:
                reason='REQUEST_BUDGET_EXHAUSTED'
            except KeyboardInterrupt:
                reason='CHECKPOINT_STOP'
            except Exception as error:
                reason='INTEGRITY_FAILURE' if failure_kind(error)=='integrity_fatal' else 'OPERATIONAL_ERROR'
                if reason=='INTEGRITY_FAILURE':store.db.execute("INSERT OR REPLACE INTO fast_control VALUES ('fatal',?)",(str(error),))
                _publish_json(durable/'last-error.json',dict(type=type(error).__name__,message=str(error),stop_reason=reason,at=now()))
            finally:
                try:finish_qwen(True)
                finally:
                    if pool:pool.shutdown(wait=True,cancel_futures=True)
                receipt=checkpoint(store,workspace,durable,header,reason)
            return receipt['progress']
