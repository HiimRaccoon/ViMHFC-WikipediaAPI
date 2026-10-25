from copy import deepcopy
from dataclasses import asdict
import json
import pytest
from vimfc.domain import canonical,SourceUnavailable,digest
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.pipeline.runner import Builder
from vimfc.pipeline.freeze import validate_article
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.unit.test_primary_first import rules
from tests.unit.test_classify import item,SNAP
from tests.integration.test_cached_hierarchy_source import seed
from tests.integration.test_manifest_provider import Identities,Client


def setup(store,cache,config):
    cfg=deepcopy(config);cfg['classification']=rules(config)
    a=item('Q1',{'P31':['Q5'],'P106':['Q901']},'A');a['entity']['labels']={'en':{'value':'large'*100000}}
    seed(store,cache,'Q1',a);seed(store,cache,'Q5',item('Q5'))
    return cfg


def test_primary_only_article_passes_quality_and_frozen_replay(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config);run=store.create(SNAP,cfg)
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        a=p.process({'page_id':1,'title':'A'})
        assert a['quality']['eligible'] and a['classification']['primary_entity_type']=='Person'
        assert a['classification']['subtypes']==[] and a['classification']['subtype_status']=='incomplete'
        assert len(canonical(a))<25000
        validate_article(a,SNAP,cfg['extraction']['version'],rules=cfg)
        assert HistoricalProvider(store,run,Client(),cache,identities=Identities()).process({'page_id':1})==a
        second=store.create(SNAP,cfg,reuse_run=run)
        b=HistoricalProvider(store,second,Client(),cache,identities=Identities())
        assert b.process({'page_id':1})==a and b.executor.metrics['classify']['reused']==1


def test_builder_materializes_once_releases_prepared_and_reports_subtypes(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config);run=store.create(SNAP,cfg)
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,canonical({'page_id':1,'title':'A'})))
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities());builder=Builder(store,run,p,reports=tmp_path/'reports')
        result=builder._process(1,False)  # isolated authored fixture, not a pilot
        assert p._prepared=={}
        assert p.source.cache_lookups==7  # select root/human/missing occupation + read two + materialize two
        assert result['subtype_audit']['primary_classified']==1
        assert result['subtype_audit']['primary_only_classified']==1
        assert result['subtype_audit']['subtype_status_counts']=={'incomplete':1}
        assert (tmp_path/'reports'/run/'1'/'subtype-audit.json').is_file()


def test_optional_acquisition_frontier_stays_frozen_in_run_but_new_run_can_acquire_new_inventory(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config);first=store.create(SNAP,cfg)
        p=HistoricalProvider(store,first,Client(),cache,identities=Identities());candidate={'page_id':1}
        before=p.process(candidate)
        seed(store,cache,'Q901',item('Q901'))
        assert p.process(candidate)==before  # same declared input, even after external warm-up
        second=store.create(SNAP,cfg,reuse_run=first)
        q=HistoricalProvider(store,second,Client(),cache,identities=Identities());after=q.process(candidate)
        assert before['classification']['manifest_id']!=after['classification']['manifest_id']
        assert after['classification']['subtypes']==[{'subtype_id':'Q901','name':'Scientist'}]
        assert q.executor.metrics['classify']['new']==1 and q.executor.metrics['quality']['new']==1


def test_legacy_qid4_freeze_validation_and_manifest_hint_reuse_remain_compatible(tmp_path,config):
    from tests.integration.test_manifest_provider import setup as old_setup
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');old=old_setup(store,cache,config);first=store.create(SNAP,old)
        legacy=HistoricalProvider(store,first,Client(),cache,identities=Identities()).process({'page_id':1})
        assert 'primary_status' not in legacy['classification']
        validate_article(legacy,SNAP,old['extraction']['version'],rules=old)
        cfg=deepcopy(config);cfg['classification']=rules(config);second=store.create(SNAP,cfg,reuse_run=first)
        statements=[];store.db.set_trace_callback(statements.append)
        after=HistoricalProvider(store,second,Client(),cache,identities=Identities()).process({'page_id':1})
        assert after['quality']['eligible'] and after['classification']['primary_status']=='classified'
        assert not any('SELECT record_json FROM classification_results WHERE' in s for s in statements)
