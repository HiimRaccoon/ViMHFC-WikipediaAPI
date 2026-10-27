from copy import deepcopy
from importlib import import_module
import json
from pathlib import Path
import pytest
from vimfc.domain import Incompatible,digest,canonical
from vimfc.storage.pipeline_store import PipelineStore
from tests.unit.test_discovery_policy import inventory

def test_prepare_creates_isolated_run_preserves_rules_and_never_clones_old_cohort(tmp_path,config):
    api=import_module('vimfc.pipeline.discovery_experiments');planner=import_module('vimfc.pipeline.discovery_policy')
    (tmp_path/'source').mkdir();source=tmp_path/'source/source.sqlite'
    with PipelineStore(source) as store:
        run=store.create('2026-10-25T03:00:00Z',config)
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,999,0,canonical({'page_id':999,'title':'Old'})))
    context={'database':str(source),'raw_cache':str(tmp_path/'raw'),'run_id':run,'snapshot':'2026-10-25T03:00:00Z','config_hash':digest(config)}
    inv=inventory(config);inv['neighbors']=[]
    for page in range(101,1001):
        row=deepcopy(inv['seeds'][(page-1)%100]);row.update(page_id=page,title=f'Seed {page}',legacy_order=page)
        row['candidate'].update(page_id=page,title=f'Seed {page}');inv['seeds'].append(row)
    inv=planner.seal_inventory(inv);plan=planner.plan_cohort(inv,planner.default_policy(config['quotas']),'seed-only',1000)
    result=api.prepare_experiment(context,config,inv,plan,tmp_path/'experiment')
    assert result['run_id']!=run and result['database']!=str(source)
    with PipelineStore(result['database']) as store:
        new=store.run(result['run_id']);rows=store.candidates(result['run_id'])
        assert rows==plan['candidates'] and len(rows)==1000
        assert all(r['title']!='Old' for r in rows)
        assert all(new['config'][k]==config[k] for k in ('classification','extraction','ranking','quotas'))
        assert new['config']['discovery']['manifest_id']==plan['manifest_id']
        assert store.candidates(run)==[{'page_id':999,'title':'Old'}]
        assert api.validate_experiment(result,store)==plan
        cfgfile=Path(result['config']);saved=json.loads(cfgfile.read_text(encoding='utf-8'));saved['seed']='changed'
        cfgfile.write_text(canonical(saved),encoding='utf-8')
        with pytest.raises(Incompatible):api.validate_experiment(result,store)
    with pytest.raises((Incompatible,FileExistsError)):api.prepare_experiment(context,config,inv,plan,tmp_path/'experiment')

def test_private_cache_preparation_copies_valid_baseline_without_writing_source(tmp_path,config):
    api=import_module('vimfc.pipeline.discovery_experiments');planner=import_module('vimfc.pipeline.discovery_policy')
    raw=tmp_path/'raw';raw.mkdir();(raw/'fixture').write_bytes(b'opaque fixture baseline')
    inv=inventory(config);inv['neighbors']=[]
    for page in range(101,1001):
        row=deepcopy(inv['seeds'][(page-1)%100]);row.update(page_id=page,title=f'Seed {page}',legacy_order=page)
        row['candidate'].update(page_id=page,title=f'Seed {page}');inv['seeds'].append(row)
    inv=planner.seal_inventory(inv);plan=planner.plan_cohort(inv,planner.default_policy(config['quotas']),'seed-only',1000)
    (tmp_path/'source').mkdir();database=tmp_path/'source/db'
    from tests.integration.test_reviewed_quarantine import review_file
    with PipelineStore(database) as store:
        run=store.create('2026-10-25T03:00:00Z',config);review,_=review_file(tmp_path,store.run(run))
    context={'database':str(database),'raw_cache':str(raw),'run_id':run,'snapshot':'2026-10-25T03:00:00Z','config_hash':digest(config)}
    result=api.prepare_experiment(context,config,inv,plan,tmp_path/'experiment',source_review=review,private_cache=True)
    assert Path(result['raw_cache'])!=raw and (Path(result['raw_cache'])/'fixture').read_bytes()==(raw/'fixture').read_bytes()
    assert result['cache_baseline']['mode']=='private-copy'
    from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine
    with PipelineStore(result['database']) as store:
        with pytest.raises(Incompatible):ReviewedIntegrityQuarantine.from_file(result['quarantine_review_draft'],store.run(result['run_id']))

def test_offline_yield_does_not_turn_unknown_sources_into_ineligible(config):
    api=import_module('vimfc.pipeline.discovery_experiments');planner=import_module('vimfc.pipeline.discovery_policy')
    inv=inventory(config);plan=planner.plan_cohort(inv,planner.default_policy(config['quotas']),'hybrid',100)
    result=api.offline_metrics(plan,{},config['ranking'])
    assert result['unknown_source_inputs']==100 and result['eligible_observed']==0
    assert result['eligible_yield_complete'] is None and result['eligible_lower_bound']==0
    assert result['physical_api_requests']==0 and result['live_api_cost'] is None
    assert result['human_valid_two_hop_yield'] is None

def test_graph_isolates_do_not_become_connected_during_component_traversal(config):
    api=import_module('vimfc.pipeline.discovery_experiments');planner=import_module('vimfc.pipeline.discovery_policy')
    from tests.helpers import article
    inv=inventory(config);plan=planner.plan_cohort(inv,planner.default_policy(config['quotas']),'legacy',3)
    records={i:article(i,links=(2,) if i==1 else ()) for i in (1,2,3)}
    result=api.offline_metrics(plan,records,config['ranking'])
    assert result['lead_graph']['vertices']==3
    assert result['lead_graph']['nonisolated_vertices']==2
    assert result['lead_graph']['nonisolated_fraction']==2/3

def test_new_config_cannot_fall_back_to_legacy_live_discovery(tmp_path,config):
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.storage.raw_cache import RawCache
    from tests.integration.test_manifest_provider import Client,Identities
    config=deepcopy(config);config['discovery']={'version':'stratified-lead-1','manifest_id':'a'*64}
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-25T03:00:00Z',config)
        provider=HistoricalProvider(store,run,Client(),RawCache(tmp_path/'raw'),identities=Identities())
        with pytest.raises(Incompatible):provider.discover({},1)
