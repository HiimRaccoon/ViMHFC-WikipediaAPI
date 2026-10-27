"""Connect archived historical source acquisition to pure local pipeline stages."""
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import hashlib
import json
import re
from vimfc.domain import RawRevision, SourceRevisionRef, SourceUnavailable, canonical, digest, at_or_before,now
from vimfc.source.revisions import RevisionSource
from vimfc.source.identities import HistoricalIdentities
from vimfc.source.wikidata import WikidataSource
from vimfc.source.dependencies import DependencySource
from vimfc.source.identities import normalize_title
from .dependencies import article_identity,policy_for,verify_manifest,classify_manifest
from .classify import classify
from .collect import CandidateCollector
from .extract import extract_lead
from .quality import assess_quality
from .runner import StageExecutor


class HistoricalProvider:
    def __init__(self,store,run_id,client,cache,*,identities=None,wikidata=None,reviewed_quarantine=None):
        from .quarantine import require_review
        require_review(store,run_id,reviewed_quarantine)
        self.store,self.client=store,client
        self.reviewed_quarantine=reviewed_quarantine
        self.run=store.run(run_id)
        self.config=self.run['config']
        self.source=RevisionSource(client,store,cache,self.config['known_revision_batch'])
        self.source.reviewed_quarantine=reviewed_quarantine
        self.identities=identities or HistoricalIdentities(self.source,self.run['snapshot'])
        self.wikidata=wikidata or WikidataSource(self.source,self.run['snapshot'],self.config['classification'])
        self.collector=CandidateCollector(client,self.identities,self.config)
        self.executor=StageExecutor(store,run_id,client=client)
        self._prepared={}
        self.host_health=client.enable_wikidata_health(store) if hasattr(client,'enable_wikidata_health') else None

    def preflight_deferred(self):
        if not self.host_health:return {'allowed':True,'probe_performed':False,'reason':'fixture_client_without_host_health'}
        # Hint TTL belongs to viwiki; do not probe Wikidata if it has no due work.
        rows=self.store.db.execute("SELECT record_json,next_retry_at FROM candidate_work WHERE run_id=? AND status='deferred'",(self.run['pipeline_run_id'],))
        due=any(json.loads(r[0]).get('error_details',{}).get('site')=='wikidata' and
            (not r[1] or at_or_before(r[1],now())) for r in rows)
        state=self.host_health.snapshot()
        candidates=self.store.db.execute('SELECT count(*) FROM candidates WHERE run_id=?',(self.run['pipeline_run_id'],)).fetchone()[0]
        queued=self.store.db.execute('SELECT count(*) FROM candidate_work WHERE run_id=?',(self.run['pipeline_run_id'],)).fetchone()[0]
        # A fresh/extended run can inherit the same database-wide open circuit.
        # It needs a recovery probe too, even without old deferred queue rows.
        due=due or state['requires_probe'] and (candidates==0 or queued<candidates)
        if not due:return {**state,'allowed':True,'probe_performed':False,'reason':'no_due_wikidata_work'}
        return self.host_health.preflight()

    def host_guard(self):
        return self.host_health.snapshot() if self.host_health else {}

    def _raw(self,candidate):
        identity=candidate['page_id']
        self.client.stage='historical_acquisition'
        acquired=self.executor.execute('acquire',identity,{'snapshot':self.run['snapshot'],'page_id':identity},lambda:asdict(self.identities.raw_page(identity)))
        raw=RawRevision(**{**acquired,'ref':SourceRevisionRef(**acquired['ref'])})
        if raw.content_model!='wikitext':
            raise SourceUnavailable('Candidate is not historical wikitext')
        if self.config.get('discovery'):
            from vimfc.domain import Incompatible
            if normalize_title(raw.ref.title)!=normalize_title(candidate['title']):raise Incompatible('Sealed discovery canonical title changed')
            self._discovery_namespace(raw)
        return raw

    def _discovery_namespace(self,raw,*,allow_fetch=True):
        """Required intake scope, separate from qid-5 evidence or quality rules."""
        from vimfc.domain import Incompatible
        article=article_identity(raw);deps={'article':article,'snapshot':self.run['snapshot'],'policy':'historical-title-namespace-1'}
        if ':' not in raw.ref.title:
            return {'namespace':0,'article':article,'basis':'checked_historical_canonical_title_no_separator'}
        if not allow_fetch:
            result=self.store.result(self.run['pipeline_run_id'],raw.ref.page_id,'verify_namespace',digest(deps))
            if not result or result.get('namespace')!=0 or result.get('article')!=article:
                raise Incompatible('Missing namespace checkpoint; cache-only restore cannot acquire scope proof')
            return result
        def lookup():
            payload=self.client.query('viwiki',{'action':'query','prop':'revisions','revids':raw.ref.revision_id,'rvslots':'main','rvprop':'ids|timestamp'})
            pages=payload.get('query',{}).get('pages',[])
            if len(pages)!=1:raise Incompatible('Exact-revision namespace response is ambiguous')
            page=pages[0];revisions=page.get('revisions',[])
            if (page.get('pageid')!=raw.ref.page_id or len(revisions)!=1 or revisions[0].get('revid')!=raw.ref.revision_id
                or revisions[0].get('timestamp')!=raw.ref.revision_timestamp):raise Incompatible('Exact-revision namespace identity mismatch')
            if page.get('title')!=raw.ref.title:
                raise SourceUnavailable('Current title differs from pinned historical title; namespace review required',code='namespace_review_required')
            if page.get('ns')!=0:
                raise SourceUnavailable('Candidate canonical page is not main namespace',code='article_namespace_excluded')
            return {'namespace':0,'article':article,'basis':'selected_revision_identity_and_unchanged_canonical_title',
                'source_page_metadata_hash':digest(page)}
        return self.executor.execute('verify_namespace',raw.ref.page_id,deps,lookup)

    def discovery_scope_for_article(self,article):
        ref=SourceRevisionRef('viwiki',str(article['page_id']),article['page_id'],article['title'],article['revision_id'],article['revision_timestamp'],self.run['snapshot'])
        return self._discovery_namespace(RawRevision(ref,'','wikitext','',article['content_hash']),allow_fetch=False)

    def _pinned_qid(self,raw):
        identity=raw.ref.page_id;article=article_identity(raw)
        qid=self.store.item_hint(self.run['pipeline_run_id'],identity,article)
        if qid:return qid
        parent=self.run.get('reuse_run')
        if parent and self.store.run(parent)['snapshot']==self.run['snapshot']:
            old=self.store.result(parent,identity,'acquire',digest({'snapshot':self.run['snapshot'],'page_id':identity}))
            if old:
                previous=RawRevision(**{**old,'ref':SourceRevisionRef(**old['ref'])})
                if article_identity(previous)==article:
                    # A pinned parent manifest is a scoped hint source; avoid
                    # decoding the parent's repeated full Wikidata payload.
                    bound=self.store.db.execute('SELECT request_hash FROM article_manifests WHERE run_id=? AND page_id=?',(parent,identity)).fetchone()
                    if bound:
                        inherited=self.store.manifest_for(parent,identity,bound[0])
                        if inherited['article']==article:qid=inherited['root_qid']
                    rows=[] if qid else self.store.db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=?',(parent,identity))
                    matches=set()
                    for row in rows:
                        cls=json.loads(row[0])
                        for q,item in cls.get('items',{}).items():
                            title=(item['entity'].get('sitelinks') or {}).get('viwiki',{}).get('title','')
                            if normalize_title(title)==normalize_title(raw.ref.title):matches.add(q)
                    if len(matches)==1:qid=matches.pop()
        if not qid:
            # Preserve old negative records, but do not reuse them as permanent
            # evidence. Positive hints remain pinned and historically validated.
            old=self.store.result(self.run['pipeline_run_id'],identity,'item_hint',digest({'article':article}))
            if old:qid=old.get('qid')
        if not qid:
            def lookup():
                # Count actual negative observations, not worker claims or HTTP
                # maxlag attempts. Keep the old checkpoint key/TTL compatible.
                # Acquisition gate only: never a proof of historical QID absence.
                negative=self.store.db.execute("SELECT count(*) FROM run_events WHERE run_id=? AND stage='stage_deferred' AND json_extract(record_json,'$.identity')=? AND json_extract(record_json,'$.stage')='item_hint_refresh' AND json_extract(record_json,'$.code')='missing_item_hint'",(self.run['pipeline_run_id'],str(identity))).fetchone()[0]
                if negative>=2:
                    raise SourceUnavailable('Automatic negative-hint budget exhausted; historical association review required',
                        code='association_review_required',details={'site':'viwiki','page_id':identity,'policy':'negative-hint-2',
                            'negative_observations':negative,'absence_proven':False,'automatic_refresh_allowed':False})
                hint=self.client.query('viwiki',{'action':'query','prop':'pageprops','pageids':identity,'ppprop':'wikibase_item'})
                pages=hint.get('query',{}).get('pages',[])
                found=pages[0].get('pageprops',{}).get('wikibase_item') if len(pages)==1 else None
                if not found:
                    deadline=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat().replace('+00:00','Z')
                    raise SourceUnavailable('No item association hint; refresh after negative-hint TTL',retryable=True,
                        code='missing_item_hint',retry_at=deadline,details={'site':'viwiki','page_id':identity,'negative_hint':True})
                if not re.fullmatch(r'Q[1-9][0-9]*',found):
                    raise SourceUnavailable('Invalid item hint',code='invalid_item_hint')
                return {'qid':found}
            qid=self.executor.execute('item_hint_refresh',identity,{'article':article,'policy':'negative-hint-1'},lookup)['qid']
        # This is a pinned hint, never evidence of historical article association.
        # Pure classification still requires the selected historical sitelink.
        self.store.pin_item_hint(self.run['pipeline_run_id'],identity,article,qid)
        return qid

    def process_dependencies(self,candidate):
        if not self.config['classification'].get('dependency_policy'):return {}
        if self.config['classification']['dependency_policy']['version']=='primary-first-1':self._prepared.clear()
        raw=self._raw(candidate);article=article_identity(raw);qid=self._pinned_qid(raw)
        if self.reviewed_quarantine:self.reviewed_quarantine.check_cached_selection(self.source,qid,self.run['snapshot'])
        rules=self.config['classification'];policy=policy_for(rules)
        request=digest({'article':article,'root_qid':qid,'policy':policy,'snapshot':self.run['snapshot']})
        identity=candidate['page_id'];run=self.run['pipeline_run_id']
        manifest=self.store.manifest_for(run,identity,request)
        source=DependencySource(self.source,self.run['snapshot'],rules)
        self.client.stage='historical_classification'
        if manifest is None:
            parent=self.run.get('reuse_run')
            if parent:
                row=self.store.db.execute('SELECT request_hash FROM article_manifests WHERE run_id=? AND page_id=?',(parent,identity)).fetchone()
                if row and row[0]==request:
                    previous=self.store.manifest_for(parent,identity,request)
                    incomplete_acquisition=previous['format']=='vimfc-primary-first-1' and any(f.get('source_code') for f in previous['frontier'])
                    if not incomplete_acquisition:manifest=previous
            if manifest is None:
                manifest=self.executor.execute('acquire_manifest',identity,{'request_hash':request},lambda:source.acquire(article,qid))
            verify_manifest(manifest,rules,article)
            if self.reviewed_quarantine:self.reviewed_quarantine.check_manifest(manifest)
            self.store.publish_manifest(run,identity,request,manifest)
        verify_manifest(manifest,rules,article)
        if self.reviewed_quarantine:self.reviewed_quarantine.check_manifest(manifest)
        # Validate pinned artifacts BEFORE either outer process or inner classify
        # checkpoint lookup. Cache warm-up never alters this published inventory.
        items=self.executor.measure('materialize_manifest',lambda:source.materialize(manifest))
        self._prepared[identity]={'raw':raw,'manifest':manifest,'items':items}
        result={'manifest_id':manifest['manifest_id'],'article_hash':digest(article)}
        if self.config.get('discovery'):result['discovery_scope_id']=digest(self.discovery_scope_for_article(article))
        return result

    def classification_dependencies(self,prepared):
        return {'snapshot':self.run['snapshot'],'article':article_identity(prepared['raw']),
            'rules':self.config['classification'],'manifest_id':prepared['manifest']['manifest_id']}

    def process_prepared(self,candidate,dependencies):
        if self.config['classification'].get('dependency_policy',{}).get('version')!='primary-first-1':return self.process(candidate)
        value=self._prepared.get(candidate['page_id'])
        expected={'manifest_id':value['manifest']['manifest_id'],'article_hash':digest(article_identity(value['raw']))} if value else None
        if value and self.config.get('discovery'):
            expected['discovery_scope_id']=digest(self.discovery_scope_for_article(article_identity(value['raw'])))
        if not value or dependencies!=expected:
            from vimfc.domain import Incompatible
            raise Incompatible('Prepared manifest input does not match process fingerprint')
        return self.process(candidate,_prepared_manifest=dependencies['manifest_id'])

    def release_prepared(self,candidate):
        if self.config['classification'].get('dependency_policy',{}).get('version')=='primary-first-1':self._prepared.pop(candidate['page_id'],None)

    def validate_cached_result(self,record):
        """Validate fixed input artifacts for deferred-only aggregation, no API."""
        from vimfc.domain import Incompatible
        manifest=record.get('classification',{}).get('dependency_manifest')
        if not manifest:raise Incompatible('Deferred-only reuse requires a pinned dependency manifest')
        binding=self.store.db.execute('SELECT request_hash FROM article_manifests WHERE run_id=? AND page_id=?',
            (self.run['pipeline_run_id'],record['page_id'])).fetchone()
        if not binding or self.store.manifest_for(self.run['pipeline_run_id'],record['page_id'],binding[0])!=manifest:
            raise Incompatible('Completed record does not match its frozen manifest')
        verify_manifest(manifest,self.config['classification'])
        expected={k:record[k] for k in ('page_id','title','revision_id','revision_timestamp')}
        expected.update(site='viwiki',content_hash=record['raw_hash'])
        if manifest['article']!=expected or manifest['snapshot']!=self.run['snapshot'] or record['snapshot']!=self.run['snapshot']:
            raise Incompatible('Completed article identity/snapshot does not match the frozen input')
        if self.config.get('discovery'):
            if record.get('discovery_source_scope')!=self.discovery_scope_for_article(expected):
                raise Incompatible('Completed discovery record differs from required namespace scope input')
        try:
            raw=self.source.cache.get(record['cache_ref']);encoded=raw['content'].encode('utf-8')
            if (raw['page_id']!=record['page_id'] or raw['timestamp']!=record['revision_timestamp'] or raw['content_model']!=record['content_model']
                or hashlib.sha256(encoded).hexdigest()!=record['raw_hash']
                or raw['local_sha256']!=record['raw_hash'] or hashlib.sha1(encoded).hexdigest()!=raw['source_sha1']):
                raise ValueError('Pinned Wikipedia artifact mismatch')
            DependencySource(self.source,self.run['snapshot'],self.config['classification']).materialize(manifest,allow_fetch=False)
        except (ValueError,KeyError,TypeError,OSError,SourceUnavailable) as error:
            raise Incompatible('Deferred-only cached input validation failed; no source recovery was attempted') from error

    def discover(self,state,limit):
        if self.config.get('discovery'):
            from vimfc.domain import Incompatible
            raise Incompatible('Versioned discovery uses a sealed cohort; no fallback to live legacy collector')
        self.client.stage='discovery_and_identity'
        return self.collector.discover(state,limit)

    def process(self,candidate,*,_prepared_manifest=None):
        identity=candidate['page_id']
        manifested=bool(self.config['classification'].get('dependency_policy'))
        if manifested:
            if _prepared_manifest is None:self.process_dependencies(candidate)
            raw=self._prepared[identity]['raw']
        else:raw=self._raw(candidate)
        extraction_dep={'raw_hash':raw.local_sha256,'rules':self.config['extraction']}
        extraction=self.executor.execute('extract',identity,extraction_dep,lambda:asdict(extract_lead(raw,self.config['extraction'])))
        self.store.db.execute('INSERT OR IGNORE INTO lead_results VALUES (?,?,?,?)',(self.run['pipeline_run_id'],identity,digest(extraction_dep),canonical(extraction)))
        self.client.stage='historical_classification'

        def classification():
            terminal='unresolved' if self.config['classification'].get('terminal_unresolved') else 'pending'
            try:
                qid=self.wikidata.cached_qid(raw.ref.title)
                if not qid:
                    # Current pageprops is only a cached hint; historical sitelink
                    # validation below remains mandatory for every association.
                    def lookup_hint():
                        hint=self.client.query('viwiki',{'action':'query','prop':'pageprops','pageids':identity,'ppprop':'wikibase_item'})
                        pages=hint.get('query',{}).get('pages',[])
                        return {'qid':pages[0].get('pageprops',{}).get('wikibase_item') if len(pages)==1 else None}
                    qid=self.executor.execute('item_hint',identity,{'article_ref':asdict(raw.ref)},lookup_hint)['qid']
                if not qid:
                    return {'outcome':terminal,'primary_entity_type':None,'subtypes':[],'provenance':[],'items':{},'reason':'missing_item_hint','retryable':False}
                items=self.wikidata.closure(qid)
            except SourceUnavailable as error:
                return {'outcome':'pending' if error.retryable else terminal,'primary_entity_type':None,'subtypes':[],'provenance':[],'items':error.partial_items or {},'reason':str(error),
                    'retryable':error.retryable,'retry_at':error.retry_at,'error_code':error.code,'error_details':error.details}
            result=asdict(classify(raw.ref.title,qid,items,self.config['classification'],self.run['snapshot']))
            result['items']=items
            result['closure_summary']=getattr(self.wikidata,'last_traversal',{})
            return result
        classification_dep={'snapshot':self.run['snapshot'],'article_ref':asdict(raw.ref),'rules':self.config['classification']}
        work=classification
        if manifested:
            prepared=self._prepared[identity];manifest=prepared['manifest']
            classification_dep=self.classification_dependencies(prepared)
            def work():
                result=asdict(classify_manifest(manifest,prepared['items'],self.config['classification']))
                return {**result,'items':prepared['items'],'manifest_id':manifest['manifest_id'],'dependency_manifest':manifest,
                    'closure_summary':{'policy':manifest['policy'],'visited_count':len(manifest['nodes']),'frontier':manifest['frontier'],'complete':manifest['complete']}}
        cls=self.executor.execute('classify',identity,classification_dep,work)
        self.store.db.execute('INSERT INTO classification_results VALUES (?,?,?,?) ON CONFLICT(run_id,page_id,fingerprint) DO UPDATE SET record_json=excluded.record_json',(self.run['pipeline_run_id'],identity,digest(classification_dep),canonical(cls)))

        def resolve_links():
            links=[]
            for link in extraction['links']:
                target=self.identities.resolve(link['target_title'])
                links.append({**link,'target':{k:v for k,v in target.items() if k!='content'}})
            return links
        kinds=[]
        if re.match(r'^\s*#(?:redirect|đổi)',raw.content,re.I): kinds.append('redirect')
        if re.search(r'__DISAMBIG__|\{\{\s*(?:disambig|disambiguation|định hướng|trang định hướng)(?:\s*[|}])',raw.content,re.I): kinds.append('disambiguation')
        if raw.ref.title.casefold().startswith(('danh sách ','list of ')): kinds.append('list')
        quality=self.executor.execute('quality',identity,{'extraction':digest(extraction),'classification':digest(cls),'kinds':kinds,'rules':self.config['extraction']},lambda:assess_quality(extraction,cls,kinds,self.config['extraction']))
        graph_status='deferred_ineligible'
        if quality['eligible']:
            self.client.stage='lead_identity_dependencies'
            links=self.executor.execute('resolve_links',identity,{'snapshot':self.run['snapshot'],'extraction':digest(extraction),'identity_version':'moves-1'},resolve_links)
            extraction={**extraction,'links':links}
            graph_status='resolved'
        result={'page_id':identity,'title':raw.ref.title,'revision_id':raw.ref.revision_id,'revision_timestamp':raw.ref.revision_timestamp,'snapshot':self.run['snapshot'],
            'source_url':f'https://vi.wikipedia.org/w/index.php?oldid={raw.ref.revision_id}','raw_hash':raw.local_sha256,'cache_ref':raw.cache_ref,'content_model':raw.content_model,
            'extraction':extraction,'classification':cls,'quality':quality,'article_kinds':kinds,'graph_status':graph_status,
            'retryable':cls.get('retryable',False),'retry_at':cls.get('retry_at'),'reason':cls.get('reason'),
            'status':'pending_source' if cls['outcome']=='pending' else 'unresolved_classification' if cls['outcome']=='unresolved' else 'completed'}
        if self.config.get('discovery'):result['discovery_source_scope']=self.discovery_scope_for_article(article_identity(raw))
        return result
