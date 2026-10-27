from copy import deepcopy
from importlib import import_module
import pytest
from vimfc.domain import digest,Incompatible

SNAP='2026-10-25T03:00:00Z'

def inventory(config):
    seeds=[]
    groups=list(config['quotas'])
    for i in range(1,101):
        seeds.append({'page_id':i,'title':f'Seed {i}','namespace':0,'strata':[groups[(i-1)%8]],'legacy_order':i,
            'candidate':{'page_id':i,'title':f'Seed {i}','discovery_origin':'fixture'},'origins':[{'kind':'seed','stream':str(i%8)}]})
    neighbors=[]
    for i in range(101,201):
        source=1+(i-101)%10
        neighbors.append({'page_id':i,'title':f'Neighbor {i}','namespace':0,'strata':[],
            'candidate':{'page_id':i,'title':f'Neighbor {i}','discovery_origin':f'lead:{source}'},
            'origins':[{'kind':'lead','source_page_id':source,'source_eligible':True,'scope':'retained_lead_prose','source_revision_id':source+1000,'source_raw_hash':'a'*64,'source_sent_id':0,
                'target':{'status':'resolved','page_id':i,'title':f'Neighbor {i}','revision_id':i+1000,'revision_timestamp':'2026-10-24T00:00:00Z','raw_hash':'b'*64,'snapshot':SNAP}}]})
    result={'format':'vimfc-discovery-inventory-1','snapshot':SNAP,'semantic_rules':{k:digest(config[k]) for k in ('classification','extraction','ranking')},'seeds':seeds,'neighbors':neighbors}
    result['inventory_id']=digest(result);return result

def reseal(value):
    value.pop('inventory_id');value['inventory_id']=digest(value);return value

def test_three_variants_keep_equal_budget_and_hybrid_has_exact_70_30_when_available(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    plans={v:api.plan_cohort(inv,policy,v,100) for v in ('legacy','seed-only','hybrid')}
    assert [p['page_id'] for p in plans['legacy']['candidates']]==list(range(1,101))
    assert all(len(p['candidates'])==len({x['page_id'] for x in p['candidates']})==100 for p in plans.values())
    assert plans['hybrid']['slot_counts']=={'seed':70,'neighbor':30}
    assert sum(p['discovery_slot']=='neighbor' for p in plans['hybrid']['candidates'][:10])==3
    assert plans['seed-only']['slot_counts']=={'seed':100,'neighbor':0}
    assert policy['reporting_groups']['Landmark / Facility']=='Location'
    assert policy['reporting_groups']['Science / Technology'] is None
    assert policy['quotas']==config['quotas']
    assert all('primary_entity_type' not in r for p in plans.values() for r in p['candidates'])

def test_reordered_inventory_has_same_plan_and_multiple_origins_are_not_discarded(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    inv['seeds'][0]['origins'].append({'kind':'seed','stream':'other'})
    a=api.plan_cohort(reseal(inv),policy,'hybrid',100)
    inv['seeds'].reverse();inv['neighbors'].reverse()
    b=api.plan_cohort(reseal(inv),policy,'hybrid',100)
    assert a['candidates']==b['candidates']
    first=next(r for r in a['candidates'] if r['page_id']==1)
    assert len(first['discovery_origins'])==2

@pytest.mark.parametrize('mutation',['body','ineligible','future','bad_hash','namespace','unknown_stratum'])
def test_invalid_frontier_cannot_be_admitted(config,mutation):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    row=inv['neighbors'][0];origin=row['origins'][0]
    if mutation=='body':origin['scope']='body'
    elif mutation=='ineligible':origin['source_eligible']=False
    elif mutation=='future':origin['target']['revision_timestamp']='2026-10-26T00:00:00Z'
    elif mutation=='bad_hash':origin['target']['raw_hash']='not-a-hash'
    elif mutation=='namespace':row['namespace']=6
    else:inv['seeds'][0]['strata']=['fake']
    with pytest.raises(Incompatible):api.plan_cohort(reseal(inv),policy,'hybrid',100)

def test_shortfall_is_explicit_and_no_unqualified_neighbors_pad_the_cohort(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);inv['neighbors']=[]
    plan=api.plan_cohort(reseal(inv),api.default_policy(config['quotas']),'hybrid',100)
    assert len(plan['candidates'])==100 and plan['slot_counts']=={'seed':100,'neighbor':0}
    assert plan['neighbor_shortfall']==30 and plan['seed_fallback']==30
    plan=api.plan_cohort(reseal(inv),api.default_policy(config['quotas']),'seed-only',120)
    assert plan['shortfall']==20 and len(plan['candidates'])==100

def test_tampered_inventory_or_wrong_snapshot_is_rejected(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    inv['seeds'][0]['title']='changed'
    with pytest.raises(Incompatible):api.plan_cohort(inv,policy,'legacy',100)
    inv=inventory(config);inv['snapshot']='no snapshot'
    with pytest.raises((Incompatible,ValueError)):api.plan_cohort(reseal(inv),policy,'legacy',100)

def test_seed_neighbor_overlap_keeps_both_origins_and_refuses_conflicting_identity(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    row=inv['seeds'][0];row.update(page_id=101,title='Neighbor 101')
    row['candidate'].update(page_id=101,title='Neighbor 101')
    plan=api.plan_cohort(reseal(inv),policy,'legacy',100)
    overlap=next(c for c in plan['candidates'] if c['page_id']==101)
    assert {o['kind'] for o in overlap['discovery_origins']}=={'seed','lead'}
    inv['seeds'][0]['title']='Conflicting canonical';inv['seeds'][0]['candidate']['title']='Conflicting canonical'
    with pytest.raises(Incompatible):api.plan_cohort(reseal(inv),policy,'legacy',100)

def test_duplicate_alias_payloads_are_order_independent_and_both_preserved(config):
    api=import_module('vimfc.pipeline.discovery_policy');inv=inventory(config);policy=api.default_policy(config['quotas'])
    alias=deepcopy(inv['seeds'][0]);alias['candidate']['lookup_title']='Alias';inv['seeds'].append(alias)
    first=api.plan_cohort(reseal(inv),policy,'legacy',100)
    inv['seeds'].reverse();again=api.plan_cohort(reseal(inv),policy,'legacy',100)
    assert first['candidates']==again['candidates']
    candidate=next(r for r in first['candidates'] if r['page_id']==1)
    assert {v.get('lookup_title') for v in candidate['discovery_candidate_variants']}=={None,'Alias'}
