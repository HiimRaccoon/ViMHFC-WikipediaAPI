from dataclasses import asdict
import hashlib,json
import httpx
import pytest
from vimfc.domain import SourceRevisionRef,canonical
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.source.wikidata import WikidataSource
from vimfc.pipeline.classify import classify
from tests.unit.test_classify import item,SNAP


def seed(store,cache,qid,value,snapshot=SNAP):
    content=canonical(value['entity'])
    ref=SourceRevisionRef('wikidata',qid,int(qid[1:])+1000,qid,value['revision_id'],value['revision_timestamp'],snapshot)
    obj={'page_id':ref.page_id,'timestamp':ref.revision_timestamp,'content':content,'content_model':'wikibase-item',
        'source_sha1':hashlib.sha1(content.encode()).hexdigest(),'local_sha256':hashlib.sha256(content.encode()).hexdigest()}
    raw_ref=cache.put('wikidata',ref.revision_id,obj)
    store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',('wikidata',qid,snapshot,canonical(ref)))
    store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)',('wikidata',ref.revision_id,canonical({'cache_ref':raw_ref})))


def test_source_completes_deep_cached_graph_without_network_and_memoizes(config,tmp_path):
    values={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),
        'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q901','Q2']}),'Q901':item('Q901')}
    rules={**config['classification'],'cached_closure_completion':True,'terminal_unresolved':True,'max_depth':1,'max_cached_nodes':100}
    calls=[]
    def denied(request):
        calls.append(request)
        pytest.fail('All required historical nodes are already cached')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        for qid,value in values.items(): seed(store,cache,qid,value)
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,rules)
        assert wd.cached_qid('A')=='Q1'
        closure=wd.closure('Q1')
        assert set(closure)==set(values)
        result=classify('A','Q1',closure,rules,SNAP)
        assert result.outcome=='completed' and result.subtypes[0]['subtype_id']=='Q901'
        first_hits=wd.metrics['hierarchy_memory_hits']
        assert wd.closure('Q1')==closure and wd.metrics['hierarchy_memory_hits']>first_hits
        assert wd.last_traversal['max_depth']>rules['max_depth']
        assert not calls


def test_source_does_not_fetch_uncached_frontier_beyond_network_bound(config,tmp_path):
    values={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q3']})}
    rules={**config['classification'],'cached_closure_completion':True,'terminal_unresolved':True,'max_depth':1,'max_cached_nodes':100}
    def denied(request): pytest.fail('Q3 is beyond the network acquisition bound')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        for qid,value in values.items(): seed(store,cache,qid,value)
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,rules)
        closure=wd.closure('Q1')
        result=classify('A','Q1',closure,rules,SNAP)
        assert result.outcome=='unresolved' and result.reason=='missing_or_invalid_historical_dependency'
        assert wd.last_traversal['uncached_frontier']==['Q3']


def test_cached_items_from_another_snapshot_are_not_used(config,tmp_path):
    rules={**config['classification'],'cached_closure_completion':True,'terminal_unresolved':True,'max_depth':1,'max_cached_nodes':100}
    values={'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q3']})}
    def denied(request): pytest.fail('No source call is needed within the network bound')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        for qid,value in values.items(): seed(store,cache,qid,value)
        seed(store,cache,'Q3',item('Q3'),'2026-10-15T00:00:00Z')
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,rules)
        closure=wd.closure('Q1')
        assert 'Q3' not in closure
        assert classify('A','Q1',closure,rules,SNAP).outcome=='unresolved'


def test_node_budget_stops_materialization_and_keeps_classification_unresolved(config,tmp_path):
    rules={**config['classification'],'max_cached_nodes':1}
    values={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}
    def denied(request): pytest.fail('Cache-only budget test')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        for qid,value in values.items(): seed(store,cache,qid,value)
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,rules)
        closure=wd.closure('Q1')
        assert len(closure)==1 and wd.last_traversal['node_budget_exhausted']
        assert classify('A','Q1',closure,rules,SNAP).reason=='hierarchy_node_budget_exhausted'


def test_invalid_snapshot_index_is_never_published_partially(config,tmp_path):
    from vimfc.domain import SourceUnavailable
    def denied(request): pytest.fail('Invalid cache must not initiate source calls')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',title='A'))
        future=item('Q2');future['revision_timestamp']='2026-10-15T00:00:00Z'
        seed(store,cache,'Q2',future)
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,config['classification'])
        for _ in range(2):
            with pytest.raises(SourceUnavailable,match='historical association cache'): wd.cached_qid('A')


@pytest.mark.parametrize('after_index',[False,True])
def test_corrupt_cached_dependency_is_a_typed_unresolved_source_failure(config,tmp_path,after_index):
    from vimfc.domain import SourceUnavailable
    def denied(request): pytest.fail('Corrupt historical cache must never fall back to live data')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',{'P31':['Q5']},'A'))
        seed(store,cache,'Q5',item('Q5'))
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,config['classification'])
        if after_index: assert wd.cached_qid('A')=='Q1'
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=?',(105,)).fetchone()
        ref=json.loads(row[0])['cache_ref']
        (cache.root/ref['path']).write_bytes(b'corrupt fixture')
        with pytest.raises(SourceUnavailable) as error: wd.closure('Q1')
        assert error.value.code=='invalid_historical_cache' and not error.value.retryable
        assert error.value.details['qid']=='Q5'


def test_invalid_reference_metadata_is_reported_as_a_typed_cache_failure(config,tmp_path):
    from vimfc.domain import SourceUnavailable
    def denied(request): pytest.fail('Malformed metadata must not fall back to live data')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',title='A'))
        store.db.execute("UPDATE source_revisions SET record_json='{}'")
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,config['classification'])
        with pytest.raises(SourceUnavailable) as error: wd.cached_qid('A')
        assert error.value.code=='invalid_historical_cache'


@pytest.mark.parametrize('field,value',[('site','viwiki'),('identity','Q999')])
def test_snapshot_reference_identity_must_match_its_cache_key(config,tmp_path,field,value):
    from vimfc.domain import SourceUnavailable
    def denied(request): pytest.fail('Invalid metadata must not initiate requests')
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',title='A'))
        row=store.db.execute('SELECT record_json FROM snapshot_revision_refs').fetchone()
        metadata=json.loads(row[0]);metadata[field]=value
        store.db.execute('UPDATE snapshot_revision_refs SET record_json=?',(canonical(metadata),))
        wd=WikidataSource(RevisionSource(client,store,cache),SNAP,config['classification'])
        with pytest.raises(SourceUnavailable) as error: wd.cached_qid('A')
        assert error.value.code=='invalid_historical_cache'
