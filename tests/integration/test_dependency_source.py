from copy import deepcopy
import hashlib
import json
import pytest
from vimfc.domain import SourceUnavailable
from vimfc.pipeline.dependencies import classify_manifest
from vimfc.source.dependencies import DependencySource
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.unit.test_dependency_manifest import rules,article
from tests.unit.test_classify import item,SNAP
from tests.integration.test_cached_hierarchy_source import seed


class NoNetwork:
    def query(self,*args): pytest.fail('Unexpected source lookup')


def test_acquisition_scopes_cache_and_ignores_external_deep_warmup(config,tmp_path):
    r=rules(config,max_depth=1)
    values={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q901']}),'Q901':item('Q901')}
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q in ('Q1','Q5','Q2'): seed(store,cache,q,values[q])
        dep=DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,r)
        a=dep.acquire(article(),'Q1')
        seed(store,cache,'Q901',values['Q901'])
        bad=item('Q999');bad['revision_timestamp']='2026-10-15T00:00:00Z'
        seed(store,cache,'Q999',bad)
        b=DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,r).acquire(article(),'Q1')
        assert a==b and a['frontier'][0]['reason']=='dependency_policy_limit'
        assert classify_manifest(a,dep.materialize(a,allow_fetch=False),r).outcome=='unresolved'


def test_manifest_missing_blob_is_explicit_input_failure(config,tmp_path):
    r=rules(config)
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}.items(): seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,r)
        m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=105').fetchone()
        path=cache.root/json.loads(row[0])['cache_ref']['path']
        assert path.resolve().is_relative_to(tmp_path.resolve())
        path.unlink()
        with pytest.raises(SourceUnavailable) as error: dep.materialize(m,allow_fetch=False)
        assert error.value.code=='dependency_input_missing'


def test_invalid_relevant_cache_is_not_published_as_a_smaller_manifest(config,tmp_path):
    r=rules(config)
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',{'P31':['Q5']},'A'));seed(store,cache,'Q5',item('Q5'))
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=105').fetchone()
        (cache.root/json.loads(row[0])['cache_ref']['path']).write_bytes(b'corrupt fixture')
        with pytest.raises(SourceUnavailable) as error: DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,r).acquire(article(),'Q1')
        assert error.value.code=='invalid_historical_cache'


def test_cold_materialization_uses_exact_pinned_revisions_and_batches_frontier(config,tmp_path):
    import httpx
    from vimfc.source.mediawiki import MediaWikiClient
    r=rules(config);values={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    calls=[];objects={}
    def serve(request):
        params=dict(request.url.params);assert 'revids' in params and 'rvstart' not in params
        ids=list(map(int,params['revids'].split('|')));calls.append(ids)
        return httpx.Response(200,json={'query':{'pages':[{'pageid':objects[i]['page_id'],'revisions':[{'revid':i,'timestamp':objects[i]['timestamp'],
            'sha1':objects[i]['source_sha1'],'slots':{'main':{'content':objects[i]['content'],'contentmodel':'wikibase-item'}}}]} for i in ids]}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(serve)) as client:
        cache=RawCache(tmp_path/'raw')
        for q,x in values.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(client,store,cache),SNAP,r)
        warm=dep.acquire(article(),'Q1');expected=classify_manifest(warm,dep.materialize(warm),r)
        def evict_fixture_blobs():
            for row in store.db.execute('SELECT revision_id,record_json FROM source_revisions'):
                ref=json.loads(row[1])['cache_ref'];objects[row[0]]=cache.get(ref)
                path=cache.root/ref['path'];assert path.resolve().is_relative_to(tmp_path.resolve());path.unlink()
        evict_fixture_blobs()
        cold=dep.acquire(article(),'Q1')
        assert cold==warm and calls==[[101],[105,1001]]  # bounded frontier content batching
        calls.clear();evict_fixture_blobs()
        restored=dep.materialize(warm)
        assert len(calls)==1 and set(calls[0])=={101,105,1001}
        assert classify_manifest(warm,restored,r)==expected
        assert all(hashlib.sha256(cache.get(json.loads(row[0])['cache_ref'])['content'].encode()).hexdigest()
            for row in store.db.execute('SELECT record_json FROM source_revisions'))


@pytest.mark.parametrize('state',['history','transient','bad_metadata'])
def test_acquisition_failure_states_do_not_publish_a_false_closed_graph(config,tmp_path,state):
    class SourceClient:
        def query(self,*args):
            if state=='transient':raise SourceUnavailable('maxlag',code='maxlag',retryable=True,retry_at='2099-01-01T00:00:00Z')
            return {'query':{'pages':[]}}
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');r=rules(config)
        seed(store,cache,'Q1',item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'));seed(store,cache,'Q5',item('Q5'))
        if state=='bad_metadata':
            seed(store,cache,'Q2',item('Q2'))
            row=store.db.execute("SELECT record_json FROM snapshot_revision_refs WHERE identity='Q2'").fetchone()
            ref=json.loads(row[0]);ref['site']='viwiki'
            store.db.execute("UPDATE snapshot_revision_refs SET record_json=? WHERE identity='Q2'",(json.dumps(ref),))
        dep=DependencySource(RevisionSource(SourceClient(),store,cache),SNAP,r)
        if state=='history':
            m=dep.acquire(article(),'Q1')
            assert not m['complete'] and m['frontier'][0]['source_code']=='history_unavailable'
            assert classify_manifest(m,dep.materialize(m,allow_fetch=False),r).reason=='history_unavailable'
        else:
            with pytest.raises(SourceUnavailable) as error:dep.acquire(article(),'Q1')
            assert error.value.code==('maxlag' if state=='transient' else 'invalid_historical_cache')
            assert error.value.retryable==(state=='transient')
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==0


def test_cold_shared_source_article_order_and_external_warmup_do_not_change_inventory(config,tmp_path):
    from vimfc.domain import canonical
    catalog={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q3':item('Q3',{'P31':['Q901']},'B'),
        'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q901']}),'Q901':item('Q901')}
    class FixtureSource:
        def query(self,site,params):
            ids=params.get('revids')
            qs=[q for q,v in catalog.items() if str(v['revision_id']) in ids.split('|')] if ids else [params['titles']]
            return {'query':{'pages':[{'pageid':int(q[1:])+1000,'title':q,'revisions':[{'revid':catalog[q]['revision_id'],
                'timestamp':catalog[q]['revision_timestamp'],'sha1':hashlib.sha1(canonical(catalog[q]['entity']).encode()).hexdigest(),
                'slots':{'main':{'content':canonical(catalog[q]['entity']),'contentmodel':'wikibase-item'}}}]} for q in qs]}}
    results=[]
    for i,order in enumerate((['Q1','Q3'],['Q3','Q1'])):
        with PipelineStore(tmp_path/f'db{i}') as store:
            cache=RawCache(tmp_path/f'raw{i}');r=rules(config,max_depth=1)
            source=DependencySource(RevisionSource(FixtureSource(),store,cache),SNAP,r);rows={}
            for q in order:
                a=article();a['title']='A' if q=='Q1' else 'B';a['page_id']=1 if q=='Q1' else 2
                m=source.acquire(a,q);rows[q]=(m,classify_manifest(m,source.materialize(m),r))
            results.append(rows)
    assert results[0]==results[1]
    assert results[0]['Q1'][1].reason=='dependency_policy_limit'
    assert results[0]['Q3'][1].outcome=='completed'


@pytest.mark.parametrize('value',['{bad json','{}','{"revision_id":null}'])
def test_malformed_selected_reference_is_a_typed_article_input_failure(config,tmp_path,value):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');seed(store,cache,'Q1',item('Q1',title='A'))
        store.db.execute("UPDATE snapshot_revision_refs SET record_json=? WHERE identity='Q1'",(value,))
        with pytest.raises(SourceUnavailable) as error:
            DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,rules(config)).acquire(article(),'Q1')
        assert error.value.code=='invalid_historical_cache'


def test_materialization_metadata_mismatch_is_invalid_not_missing(config,tmp_path):
    from vimfc.domain import canonical
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');seed(store,cache,'Q1',item('Q1',title='A'))
        dep=DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=101').fetchone()
        value=cache.get(json.loads(row[0])['cache_ref']);value['timestamp']='2026-10-12T00:00:00Z'
        ref=cache.put('wikidata',101,value)
        store.db.execute('UPDATE source_revisions SET record_json=? WHERE revision_id=101',(canonical({'cache_ref':ref}),))
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m)
        assert error.value.code=='dependency_input_invalid'


@pytest.mark.parametrize('timestamp',[None,123,False,'invalid'])
def test_malformed_selected_timestamp_is_a_typed_cache_failure(config,tmp_path,timestamp):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');seed(store,cache,'Q1',item('Q1',title='A'))
        row=store.db.execute("SELECT record_json FROM snapshot_revision_refs WHERE identity='Q1'").fetchone()
        ref=json.loads(row[0]);ref['revision_timestamp']=timestamp
        store.db.execute("UPDATE snapshot_revision_refs SET record_json=? WHERE identity='Q1'",(json.dumps(ref),))
        with pytest.raises(SourceUnavailable) as error:
            DependencySource(RevisionSource(NoNetwork(),store,cache),SNAP,rules(config)).acquire(article(),'Q1')
        assert error.value.code=='invalid_historical_cache'
