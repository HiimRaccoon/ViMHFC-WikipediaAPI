import hashlib
import json
import httpx
import pytest
from vimfc.domain import SourceUnavailable
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.source.wikidata import WikidataSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.unit.test_classify import item,SNAP


def test_failed_qid_lookup_is_deferred_until_retry_deadline_and_can_resume(tmp_path):
    calls=[]
    healthy=False
    def serve(request):
        calls.append(1)
        if not healthy:
            return httpx.Response(200,json={'error':{'code':'maxlag','info':'lagged'}},headers={'Retry-After':'9'})
        return httpx.Response(200,json={'query':{'pages':[{'pageid':1,'title':'Q1','revisions':[{'revid':10,'timestamp':'2026-10-13T00:00:00Z'}]}]}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve),sleep=lambda _:None) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable) as failure:
            source.select_revision('wikidata','Q1',SNAP)
        assert failure.value.retryable and failure.value.retry_at
        assert failure.value.code=='maxlag'
        assert len(calls)==5
        with pytest.raises(SourceUnavailable): source.select_revision('wikidata','Q1',SNAP)
        assert len(calls)==5
        # Persisted cache also protects a fresh source/client instance.
        with MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve),sleep=lambda _:None) as second:
            fresh=RevisionSource(second,store,RawCache(tmp_path/'raw'))
            with pytest.raises(SourceUnavailable): fresh.select_revision('wikidata','Q1',SNAP)
            assert len(calls)==5
            store.db.execute("UPDATE source_failures SET retry_at='2000-01-01T00:00:00Z'")
            store.db.execute("UPDATE api_cooldowns SET retry_at='2000-01-01T00:00:00Z'")
            healthy=True
            assert fresh.select_revision('wikidata','Q1',SNAP).revision_id==10
            assert len(calls)==6


def test_wikidata_bfs_batches_siblings_and_stops_only_known_root_branches(tmp_path,config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901','Q33999']},'A'),
           'Q5':item('Q5',{'P279':['Q999']}),'Q901':item('Q901',{'P279':['Q998']}),'Q33999':item('Q33999',{'P279':['Q997']})}
    batches=[]
    def serve(request):
        p=dict(request.url.params)
        if 'titles' in p:
            assert p['titles'] in items
            ids=[p['titles']]
        else:
            revids=[int(v) for v in p['revids'].split('|')]
            batches.append(len(revids))
            ids=[qid for qid,a in items.items() if a['revision_id'] in revids]
        pages=[]
        for qid in ids:
            a=items[qid]; content=json.dumps(a['entity'])
            pages.append({'pageid':int(qid[1:]),'title':qid,'revisions':[{'revid':a['revision_id'],'timestamp':a['revision_timestamp'],
                'sha1':hashlib.sha1(content.encode()).hexdigest(),'slots':{'main':{'contentmodel':'wikibase-item','content':content}}}]})
        return httpx.Response(200,json={'query':{'pages':pages}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        wikidata=WikidataSource(source,SNAP,config['classification'])
        closure=wikidata.closure('Q1')
        assert set(closure)==set(items)
        assert batches==[1,3]
        assert wikidata.closure('Q1')==closure
        assert batches==[1,3]


def test_canonical_page_id_alias_reuses_verified_title_lookup(tmp_path):
    calls=[]
    def serve(request):
        calls.append(1)
        return httpx.Response(200,json={'curtimestamp':'2026-10-25T04:00:00Z','query':{'pages':[{'pageid':1,'title':'A','revisions':[{'revid':10,'timestamp':'2026-10-13T00:00:00Z'}]}]}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        title=source.select_revision('viwiki','A',SNAP)
        identity=source.select_revision('viwiki','1',SNAP)
        assert title.revision_id==identity.revision_id and title.lookup_timestamp==identity.lookup_timestamp
        assert len(calls)==1


def test_real_transport_is_blocked_when_offline_environment_is_set(monkeypatch):
    monkeypatch.setenv('VIMFC_OFFLINE','1')
    with MediaWikiClient('fixture@example.org') as client:
        monkeypatch.setattr(client.client,'get',lambda *a,**k:pytest.fail('Offline guard must prevent transport invocation'))
        with pytest.raises(SourceUnavailable,match='offline'):
            client.query('viwiki',{'action':'query'})
def test_host_retry_after_survives_restart_for_a_different_qid(tmp_path):
    import httpx
    import pytest
    from vimfc.domain import SourceUnavailable
    from vimfc.storage.pipeline_store import PipelineStore
    from vimfc.storage.raw_cache import RawCache
    from vimfc.source.revisions import RevisionSource
    from vimfc.source.mediawiki import MediaWikiClient
    calls=[]
    def serve(request):
        calls.append(request)
        return httpx.Response(503,headers={'Retry-After':'120'})
    with PipelineStore(tmp_path/'db') as store:
        for qid in ('Q1','Q2'):
            with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(serve),attempts=1) as client:
                source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
                with pytest.raises(SourceUnavailable): source.select_revision('wikidata',qid,'2026-10-25T03:00:00Z')
        assert len(calls)==1


def test_permanent_unavailable_content_is_cached_for_shared_dependencies(tmp_path):
    from vimfc.domain import SourceRevisionRef
    calls=[]
    def serve(request):
        calls.append(request)
        return httpx.Response(200,json={'query':{'pages':[]}})
    ref=SourceRevisionRef('wikidata','Q1',1,'Q1',10,'2026-10-13T00:00:00Z',SNAP,None)
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        for _ in range(2):
            with pytest.raises(SourceUnavailable): source.fetch_revisions('wikidata',[ref])
        assert len(calls)==1


def test_one_hidden_content_does_not_poison_valid_batch_siblings(tmp_path):
    from vimfc.domain import SourceRevisionRef
    calls=[]
    content='{"id":"Q2","claims":[],"sitelinks":[]}'
    def serve(request):
        calls.append(request)
        return httpx.Response(200,json={'query':{'pages':[
            {'pageid':1,'revisions':[{'revid':10,'timestamp':'2026-10-13T00:00:00Z','slots':{'main':{}}}]},
            {'pageid':2,'revisions':[{'revid':20,'timestamp':'2026-10-13T00:00:00Z','sha1':hashlib.sha1(content.encode()).hexdigest(),'slots':{'main':{'content':content,'contentmodel':'wikibase-item'}}}]}]}})
    refs=[SourceRevisionRef('wikidata',f'Q{i}',i,f'Q{i}',i*10,'2026-10-13T00:00:00Z',SNAP,None) for i in (1,2)]
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        source=RevisionSource(client,store,RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable): source.fetch_revisions('wikidata',refs)
        assert source.fetch_revisions('wikidata',[refs[1]])[0].content==content
        with pytest.raises(SourceUnavailable): source.fetch_revisions('wikidata',[refs[0]])
        assert len(calls)==1
