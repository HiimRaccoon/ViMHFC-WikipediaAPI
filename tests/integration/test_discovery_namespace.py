from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from vimfc.domain import RawRevision,SourceRevisionRef,SourceUnavailable,Incompatible
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.pipeline.provider import HistoricalProvider
from tests.integration.test_manifest_provider import Identities

SNAP='2026-10-25T03:00:00Z'

@pytest.mark.parametrize('mode',['valid','nonmain','moved','wrong_revision'])
def test_colon_seed_requires_checked_namespace_before_classification(tmp_path,config,mode):
    cfg=deepcopy(config);cfg['discovery']={'version':'stratified-lead-1'};calls=[]
    class Client:
        def query(self,site,params):
            calls.append((site,params));assert params['revids']==77 and params['prop']=='revisions'
            return {'query':{'pages':[{'pageid':1,'title':'Moved' if mode=='moved' else 'Film: Title','ns':14 if mode=='nonmain' else 0,
                'revisions':[{'revid':88 if mode=='wrong_revision' else 77,'timestamp':'2026-10-24T00:00:00Z'}]}]}}
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,cfg);provider=HistoricalProvider(store,run,Client(),RawCache(tmp_path/'raw'),identities=Identities())
        raw=RawRevision(SourceRevisionRef('viwiki','1',1,'Film: Title',77,'2026-10-24T00:00:00Z',SNAP),'','wikitext','a'*40,'b'*64)
        assert hasattr(provider,'_discovery_namespace'), 'Namespace uncertainty needs a required intake guard'
        if mode=='valid':
            with pytest.raises(Incompatible):provider._discovery_namespace(raw,allow_fetch=False)
            result=provider._discovery_namespace(raw)
            assert result['namespace']==0 and result['article']['revision_id']==77
            assert provider._discovery_namespace(raw,allow_fetch=False)==result and len(calls)==1
        else:
            with pytest.raises((SourceUnavailable,Incompatible)):provider._discovery_namespace(raw)
            assert not store.db.execute('SELECT 1 FROM article_manifests').fetchone()
        assert len(calls)==1

def test_namespace_scope_is_bound_to_process_identity_and_survives_cache_only_restart(tmp_path,config):
    from tests.integration.test_discovery_archive import fixture_archive
    from vimfc.pipeline.discovery_archive import Archive
    from vimfc.domain import digest
    cfg=deepcopy(config);cfg['discovery']={'version':'stratified-lead-1'}
    context=fixture_archive(tmp_path,cfg)
    with Archive(context) as archive:
        row=archive.db.execute("SELECT result_json FROM stage_results WHERE stage='process'").fetchone()
        import json
        record=json.loads(row[0])
        assert record.get('discovery_source_scope',{}).get('namespace')==0
        assert record['discovery_source_scope']['article']['content_hash']==record['raw_hash']
    from tests.integration.test_manifest_provider import Client
    from vimfc.pipeline.runner import Builder
    with PipelineStore(context['database']) as store:
        provider=HistoricalProvider(store,context['run_id'],Client(),RawCache(context['raw_cache']),identities=Identities())
        builder=Builder(store,context['run_id'],provider,restore_completed=True)
        assert len(builder.records())==1
        provider.validate_cached_result(builder.records()[0])

def test_primary_first_prepared_worker_accepts_exact_discovery_scope_and_rejects_changes(tmp_path,config):
    from tests.integration.test_manifest_provider import setup,Client,SNAP as FIXTURE_SNAPSHOT
    from vimfc.domain import digest
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config)
        cfg['classification']=deepcopy(config['classification'])
        cfg['discovery']={'version':'stratified-lead-1'}
        run=store.create(FIXTURE_SNAPSHOT,cfg)
        provider=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        candidate={'page_id':1,'title':'A'}
        dependencies=provider.process_dependencies(candidate)
        assert 'discovery_scope_id' in dependencies
        record=provider.process_prepared(candidate,dependencies)
        assert record['classification']['primary_status']=='classified'
        assert digest(record['discovery_source_scope'])==dependencies['discovery_scope_id']
        for invalid in ({k:v for k,v in dependencies.items() if k!='discovery_scope_id'},
                        {**dependencies,'discovery_scope_id':'0'*64}):
            with pytest.raises(Incompatible):provider.process_prepared(candidate,invalid)

def test_builder_executes_primary_first_discovery_worker_and_reuses_identical_checkpoint(tmp_path,config):
    from tests.integration.test_manifest_provider import setup,Client,SNAP as FIXTURE_SNAPSHOT
    from vimfc.pipeline.runner import Builder
    from vimfc.domain import canonical
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config)
        cfg['classification']=deepcopy(config['classification'])
        cfg['discovery']={'version':'stratified-lead-1'}
        run=store.create(FIXTURE_SNAPSHOT,cfg)
        candidate={'page_id':1,'title':'A'}
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,canonical(candidate)))
        provider=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        builder=Builder(store,run,provider,reports=tmp_path/'reports')
        report=builder._process(1,False)  # one offline fixture, never a real pilot
        assert report['data_complete'] and len(builder.records())==1
        before=dict(store.db.execute("SELECT * FROM stage_results WHERE run_id=? AND stage='process'",(run,)).fetchone())
        restarted=Builder(store,run,HistoricalProvider(store,run,Client(),cache,identities=Identities()),reports=tmp_path/'reports')
        again=restarted._process(1,False)
        assert again['data_complete'] and restarted.records()==builder.records()
        after=dict(store.db.execute("SELECT * FROM stage_results WHERE run_id=? AND stage='process'",(run,)).fetchone())
        assert after==before
