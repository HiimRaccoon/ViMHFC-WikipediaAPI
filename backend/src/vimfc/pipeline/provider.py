"""Connect archived historical source acquisition to pure local pipeline stages."""
from dataclasses import asdict
import json
import re
from vimfc.domain import RawRevision, SourceRevisionRef, SourceUnavailable, canonical, digest
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
    def __init__(self,store,run_id,client,cache,*,identities=None,wikidata=None):
        self.store,self.client=store,client
        self.run=store.run(run_id)
        self.config=self.run['config']
        self.source=RevisionSource(client,store,cache,self.config['known_revision_batch'])
        self.identities=identities or HistoricalIdentities(self.source,self.run['snapshot'])
        self.wikidata=wikidata or WikidataSource(self.source,self.run['snapshot'],self.config['classification'])
        self.collector=CandidateCollector(client,self.identities,self.config)
        self.executor=StageExecutor(store,run_id)
        self._prepared={}

    def _raw(self,candidate):
        identity=candidate['page_id']
        self.client.stage='historical_acquisition'
        acquired=self.executor.execute('acquire',identity,{'snapshot':self.run['snapshot'],'page_id':identity},lambda:asdict(self.identities.raw_page(identity)))
        raw=RawRevision(**{**acquired,'ref':SourceRevisionRef(**acquired['ref'])})
        if raw.content_model!='wikitext':
            raise SourceUnavailable('Candidate is not historical wikitext')
        return raw

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
            def lookup():
                hint=self.client.query('viwiki',{'action':'query','prop':'pageprops','pageids':identity,'ppprop':'wikibase_item'})
                pages=hint.get('query',{}).get('pages',[])
                return {'qid':pages[0].get('pageprops',{}).get('wikibase_item') if len(pages)==1 else None}
            qid=self.executor.execute('item_hint',identity,{'article':article},lookup)['qid']
        if not qid:
            raise SourceUnavailable('No item association hint; no classification input published',code='missing_item_hint')
        # This is a pinned hint, never evidence of historical article association.
        # Pure classification still requires the selected historical sitelink.
        self.store.pin_item_hint(self.run['pipeline_run_id'],identity,article,qid)
        return qid

    def process_dependencies(self,candidate):
        if not self.config['classification'].get('dependency_policy'):return {}
        if self.config['classification']['dependency_policy']['version']=='primary-first-1':self._prepared.clear()
        raw=self._raw(candidate);article=article_identity(raw);qid=self._pinned_qid(raw)
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
            self.store.publish_manifest(run,identity,request,manifest)
        verify_manifest(manifest,rules,article)
        # Validate pinned artifacts BEFORE either outer process or inner classify
        # checkpoint lookup. Cache warm-up never alters this published inventory.
        items=source.materialize(manifest)
        self._prepared[identity]={'raw':raw,'manifest':manifest,'items':items}
        return {'manifest_id':manifest['manifest_id'],'article_hash':digest(article)}

    def classification_dependencies(self,prepared):
        return {'snapshot':self.run['snapshot'],'article':article_identity(prepared['raw']),
            'rules':self.config['classification'],'manifest_id':prepared['manifest']['manifest_id']}

    def process_prepared(self,candidate,dependencies):
        if self.config['classification'].get('dependency_policy',{}).get('version')!='primary-first-1':return self.process(candidate)
        value=self._prepared.get(candidate['page_id'])
        if not value or dependencies!={'manifest_id':value['manifest']['manifest_id'],'article_hash':digest(article_identity(value['raw']))}:
            from vimfc.domain import Incompatible
            raise Incompatible('Prepared manifest input does not match process fingerprint')
        return self.process(candidate,_prepared_manifest=dependencies['manifest_id'])

    def release_prepared(self,candidate):
        if self.config['classification'].get('dependency_policy',{}).get('version')=='primary-first-1':self._prepared.pop(candidate['page_id'],None)

    def discover(self,state,limit):
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
        return {'page_id':identity,'title':raw.ref.title,'revision_id':raw.ref.revision_id,'revision_timestamp':raw.ref.revision_timestamp,'snapshot':self.run['snapshot'],
            'source_url':f'https://vi.wikipedia.org/w/index.php?oldid={raw.ref.revision_id}','raw_hash':raw.local_sha256,'cache_ref':raw.cache_ref,'content_model':raw.content_model,
            'extraction':extraction,'classification':cls,'quality':quality,'article_kinds':kinds,'graph_status':graph_status,
            'retryable':cls.get('retryable',False),'retry_at':cls.get('retry_at'),'reason':cls.get('reason'),
            'status':'pending_source' if cls['outcome']=='pending' else 'unresolved_classification' if cls['outcome']=='unresolved' else 'completed'}
