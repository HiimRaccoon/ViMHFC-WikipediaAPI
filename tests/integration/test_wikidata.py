import hashlib
import json
import httpx
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.source.wikidata import WikidataSource
from vimfc.pipeline.classify import classify
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache


def test_historical_hierarchy_shared_dependency_cache_and_sitelink(tmp_path,config):
    from tests.unit.test_classify import item
    items={'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q5']}),'Q5':item('Q5')}
    requests=[]
    def serve(request):
        p=dict(request.url.params)
        requests.append(p)
        if 'titles' in p:
            qid=p['titles']
        else:
            qid=next(k for k,v in items.items() if str(v['revision_id'])==p['revids'])
        archived=items[qid]
        content=json.dumps(archived['entity'])
        revision={'revid':archived['revision_id'],'timestamp':archived['revision_timestamp'],'sha1':hashlib.sha1(content.encode()).hexdigest(),
            'slots':{'main':{'contentmodel':'wikibase-item','content':content}}}
        return httpx.Response(200,json={'query':{'pages':[{'pageid':int(qid[1:]),'title':qid,'revisions':[revision]}]}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        wikidata=WikidataSource(source,'2026-10-14T00:00:00Z',config['classification'])
        closure=wikidata.closure('Q1')
        assert len(requests)==6
        wikidata.closure('Q2')
        assert len(requests)==6
        result=classify('A','Q1',closure,config['classification'],'2026-10-14T00:00:00Z')
        assert result.primary_entity_type=='Person'
        assert result.provenance[0]['properties']==['P31','P279']
