"""Connect archived historical source acquisition to pure local pipeline stages."""
from dataclasses import asdict
import re
from vimfc.domain import RawRevision, SourceRevisionRef, SourceUnavailable, canonical, digest
from vimfc.source.revisions import RevisionSource
from vimfc.source.identities import HistoricalIdentities
from vimfc.source.wikidata import WikidataSource
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

    def discover(self,state,limit):
        self.client.stage='discovery_and_identity'
        return self.collector.discover(state,limit)

    def process(self,candidate):
        identity=candidate['page_id']
        self.client.stage='historical_acquisition'
        acquired=self.executor.execute('acquire',identity,{'snapshot':self.run['snapshot'],'page_id':identity},lambda:asdict(self.identities.raw_page(identity)))
        raw=RawRevision(**{**acquired,'ref':SourceRevisionRef(**acquired['ref'])})
        if raw.content_model!='wikitext':
            raise SourceUnavailable('Candidate is not historical wikitext')
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
        cls=self.executor.execute('classify',identity,classification_dep,classification)
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
