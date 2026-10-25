import hashlib
import json
from pathlib import Path
import httpx
from vimfc.domain import RawRevision,SourceRevisionRef
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.pipeline.provider import HistoricalProvider
from tests.unit.test_classify import item


def test_historical_provider_wires_all_stages_and_persists_provenance(tmp_path,config):
    # Archived provider contract. qid-4 has its own real-source manifest tests.
    config['classification'].pop('dependency_policy',None)
    config['classification'].update(version='qid-3',cached_closure_completion=True)
    snapshot='2026-10-14T00:00:00Z'
    raw=Path('tests/fixtures/lead.wiki').read_text(encoding='utf-8')
    class Identities:
        def raw_page(self,page_id):
            ref=SourceRevisionRef('viwiki',str(page_id),page_id,'A',10,'2026-10-13T00:00:00Z',snapshot)
            return RawRevision(ref,raw,'wikitext',hashlib.sha1(raw.encode()).hexdigest(),hashlib.sha256(raw.encode()).hexdigest())
        def resolve(self,title):
            return {'status':'resolved','page_id':2,'title':title,'revision_id':11,'revision_timestamp':'2026-10-13T00:00:00Z','snapshot':snapshot,'chain':[],'proof':{'kind':'controlled_fixture'}}
    class Wikidata:
        def cached_qid(self,title): return None
        def closure(self,qid): return json.loads(Path('tests/fixtures/wikidata.json').read_text(encoding='utf-8'))
    def serve(request):
        assert dict(request.url.params)['prop']=='pageprops'
        return httpx.Response(200,json={'query':{'pages':[{'pageid':1,'title':'A','pageprops':{'wikibase_item':'Q1'}}]}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        run=store.create(snapshot,config)
        provider=HistoricalProvider(store,run,client,RawCache(tmp_path/'raw'),identities=Identities(),wikidata=Wikidata())
        result=provider.process({'page_id':1,'title':'A'})
        assert result['quality']['eligible']
        assert result['classification']['subtypes'][0]['subtype_id']=='Q901'
        assert result['extraction']['links'][0]['target']['snapshot']==snapshot
        assert store.db.execute('SELECT count(*) FROM lead_results').fetchone()[0]==1
        assert store.db.execute('SELECT count(*) FROM classification_results').fetchone()[0]==1
        assert provider.process({'page_id':1,'title':'A'})==result
