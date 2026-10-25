from copy import deepcopy
import json
import pytest
from vimfc.domain import SourceUnavailable
from vimfc.pipeline.dependencies import classify_manifest
from vimfc.source.dependencies import DependencySource
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.unit.test_primary_first import rules
from tests.unit.test_dependency_manifest import article
from tests.unit.test_classify import item,SNAP
from tests.integration.test_cached_hierarchy_source import seed


class MissingSource:
    def __init__(self,code='dependency_input_missing',retryable=False):self.code,self.retryable=code,retryable
    def query(self,*args):raise SourceUnavailable('Fixture dependency unavailable',code=self.code,retryable=self.retryable)


@pytest.mark.parametrize('code,retryable',[('dependency_input_missing',False),('history_unavailable',False),('maxlag',True)])
def test_source_optional_failure_preserves_primary_and_pins_error_role(tmp_path,config,code,retryable):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        seed(store,cache,'Q1',item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'));seed(store,cache,'Q5',item('Q5'))
        dep=DependencySource(RevisionSource(MissingSource(code,retryable),store,cache),SNAP,rules(config))
        m=dep.acquire(article(),'Q1');items=dep.materialize(m,allow_fetch=False)
        c=classify_manifest(m,items,rules(config))
        assert c.outcome=='completed' and c.primary_entity_type=='Person'
        assert c.subtype_status=='incomplete'
        assert m['frontier'][0]['role']=='subtype' and m['frontier'][0]['source_code']==code
        assert all(x['evidence_projection']=='classification-evidence-1' for x in items.values())


def test_compact_source_evidence_preserves_hashes_and_ignores_unrelated_cache(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');values={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
        values['Q1']['entity']['labels']={'en':{'value':'x'*200000}}
        for q,x in values.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        projected=dep.materialize(m,allow_fetch=False)
        assert len(json.dumps(projected))<6000 and 'labels' not in projected['Q1']['entity']
        assert classify_manifest(m,projected,rules(config)).subtypes[0]['subtype_id']=='Q901'
        bad=item('Q999');bad['revision_timestamp']='2099-01-01T00:00:00Z';seed(store,cache,'Q999',bad)
        assert dep.acquire(article(),'Q1')==m
        # Missing declared optional artifact is an optional INPUT failure, not a
        # new smaller classification under the frozen manifest.
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        path=cache.root/json.loads(row[0])['cache_ref']['path'];assert path.resolve().is_relative_to(tmp_path.resolve());path.unlink()
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m,allow_fetch=False)
        assert error.value.code=='dependency_input_missing' and error.value.details['role']=='subtype'


def test_primary_transient_failure_remains_retryable_and_does_not_publish(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');seed(store,cache,'Q1',item('Q1',{'P31':['Q5']},'A'))
        dep=DependencySource(RevisionSource(MissingSource('maxlag',True),store,cache),SNAP,rules(config))
        with pytest.raises(SourceUnavailable) as error:dep.acquire(article(),'Q1')
        assert error.value.retryable and error.value.code=='maxlag'
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==0


def test_corrupt_optional_artifact_is_scoped_without_false_primary_failure(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        (cache.root/json.loads(row[0])['cache_ref']['path']).write_bytes(b'bad fixture')
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        c=classify_manifest(m,dep.materialize(m,allow_fetch=False),rules(config))
        assert c.outcome=='completed' and c.subtype_status=='incomplete'
        assert m['frontier'][0]['source_code']=='invalid_historical_cache'


def test_cold_warm_and_reordered_articles_produce_same_primary_subtype_manifest(tmp_path,config):
    import hashlib
    from vimfc.domain import canonical
    catalog={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q3':item('Q3',{'P31':['Q5'],'P106':['Q901']},'B'),
        'Q5':item('Q5'),'Q901':item('Q901')}
    class FixtureSource:
        def query(self,site,params):
            ids=params.get('revids')
            qs=[q for q,x in catalog.items() if str(x['revision_id']) in ids.split('|')] if ids else [params['titles']]
            return {'query':{'pages':[{'pageid':int(q[1:])+1000,'title':q,'revisions':[{'revid':catalog[q]['revision_id'],
                'timestamp':catalog[q]['revision_timestamp'],'sha1':hashlib.sha1(canonical(catalog[q]['entity']).encode()).hexdigest(),
                'slots':{'main':{'content':canonical(catalog[q]['entity']),'contentmodel':'wikibase-item'}}}]} for q in qs]}}
    outputs=[]
    for i,order in enumerate((['Q1','Q3'],['Q3','Q1'])):
        with PipelineStore(tmp_path/f'db{i}') as store:
            dep=DependencySource(RevisionSource(FixtureSource(),store,RawCache(tmp_path/f'raw{i}')),SNAP,rules(config));rows={}
            for q in order:
                a=article();a['title']='A' if q=='Q1' else 'B';a['page_id']=1 if q=='Q1' else 2
                m=dep.acquire(a,q);rows[q]=(m,classify_manifest(m,dep.materialize(m),rules(config)))
                assert dep.acquire(a,q)==m
            outputs.append(rows)
    assert outputs[0]==outputs[1]


def test_default_materialization_missing_optional_blob_preserves_qid_role_and_revision(tmp_path,config):
    class ContentUnavailable:
        def query(self,site,params):
            assert params['revids']=='1001'
            return {'query':{'pages':[]}}
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(ContentUnavailable(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        path=cache.root/json.loads(row[0])['cache_ref']['path'];assert path.resolve().is_relative_to(tmp_path.resolve());path.unlink()
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m)
        assert error.value.code=='dependency_input_missing'
        assert error.value.details['role']=='subtype' and error.value.details['qid']=='Q901'
        assert error.value.details['unavailable_revision_ids']==[1001]


@pytest.mark.parametrize('registered',[True,False])
def test_corrupt_declared_optional_blob_has_role_on_default_materialization_path(tmp_path,config,registered):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        (cache.root/json.loads(row[0])['cache_ref']['path']).write_bytes(b'bad fixture')
        if not registered:store.db.execute('DELETE FROM source_revisions WHERE revision_id=1001')
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m)
        assert error.value.code=='dependency_input_invalid'
        assert error.value.details['qid']=='Q901' and error.value.details['role']=='subtype'


@pytest.mark.parametrize('fault',['gzip','wrong_header','wrong_site'])
def test_corrupt_optional_peer_does_not_erase_valid_subtype_in_same_content_batch(tmp_path,config,fault):
    from vimfc.domain import canonical
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2','Q901']},'A'),'Q5':item('Q5'),'Q2':item('Q2'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=102').fetchone()
        ref=json.loads(row[0])['cache_ref']
        if fault=='gzip':(cache.root/ref['path']).write_bytes(b'bad fixture')
        elif fault=='wrong_header':
            obj=cache.get(ref);obj['page_id']=999;ref=cache.put('wikidata',102,obj)
            store.db.execute('UPDATE source_revisions SET record_json=? WHERE revision_id=102',(canonical({'cache_ref':ref}),))
        else:
            row=store.db.execute("SELECT record_json FROM snapshot_revision_refs WHERE identity='Q2'").fetchone()
            value=json.loads(row[0]);value['site']='viwiki'
            store.db.execute("UPDATE snapshot_revision_refs SET record_json=? WHERE identity='Q2'",(canonical(value),))
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        c=classify_manifest(m,dep.materialize(m,allow_fetch=False),rules(config))
        assert c.outcome=='completed' and c.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]


@pytest.mark.parametrize('fault',['wrong_page','missing_field','multiple'])
def test_invalid_orphan_metadata_is_scoped_to_optional_qid(tmp_path,config,fault):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        ref=json.loads(row[0])['cache_ref'];obj=cache.get(ref)
        if fault=='wrong_page':obj['page_id']=999
        if fault=='missing_field':obj.pop('page_id')
        if fault=='multiple':obj['other_field']='second object'
        if fault!='multiple':
            path=cache.root/ref['path'];assert path.resolve().is_relative_to(tmp_path.resolve());path.unlink()
        cache.put('wikidata',1001,obj);store.db.execute('DELETE FROM source_revisions WHERE revision_id=1001')
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m)
        assert error.value.code=='dependency_input_invalid'
        assert error.value.details['qid']=='Q901' and error.value.details['role']=='subtype'


@pytest.mark.parametrize('field',['page_id','content','content_model'])
def test_malformed_registered_metadata_is_also_role_scoped(tmp_path,config,field):
    from vimfc.domain import canonical
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
        dep=DependencySource(RevisionSource(MissingSource(),store,cache),SNAP,rules(config));m=dep.acquire(article(),'Q1')
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=1001').fetchone()
        obj=cache.get(json.loads(row[0])['cache_ref']);obj.pop(field);ref=cache.put('wikidata',1001,obj)
        store.db.execute('UPDATE source_revisions SET record_json=? WHERE revision_id=1001',(canonical({'cache_ref':ref}),))
        with pytest.raises(SourceUnavailable) as error:dep.materialize(m)
        assert error.value.code=='dependency_input_invalid' and error.value.details['role']=='subtype'
        assert error.value.details['qid']=='Q901'
