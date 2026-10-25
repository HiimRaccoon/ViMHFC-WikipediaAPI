from copy import deepcopy
from dataclasses import asdict
import pytest
from vimfc.domain import SourceUnavailable,Incompatible
from vimfc.pipeline.primary import make_manifest,classify_manifest,project_item
from tests.unit.test_classify import item,SNAP
from tests.unit.test_dependency_manifest import article


def rules(config,**policy):
    return {**deepcopy(config['classification']),'version':'qid-5','cached_closure_completion':False,
        'dependency_policy':{'version':'primary-first-1','max_depth':12,'max_nodes':2048,**policy}}


def run(items,r,failures=None):
    def load(q):
        if q in items:return items[q]
        raise SourceUnavailable('Fixture evidence missing',code=(failures or {}).get(q,'dependency_input_missing'))
    m=make_manifest(article(),'Q1',load,r,SNAP)
    declared={n['qid']:project_item(items[n['qid']],SNAP) for n in m['nodes']}
    return m,classify_manifest(m,declared,r)


def test_direct_human_primary_survives_optional_occupation_beyond_depth_12(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q1001']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    for i in range(1001,1013):items[f'Q{i}']=item(f'Q{i}',{'P279':[f'Q{i+1}' if i<1012 else 'Q901']})
    m,c=run(items,rules(config))
    assert c.outcome=='completed' and c.primary_status=='classified' and c.primary_entity_type=='Person'
    assert c.primary_decision=='closed_primary_graph' and c.subtypes==[]
    assert c.subtype_status=='undetermined_in_policy' and c.subtype_search_status=='policy_limited'
    assert m['primary']['complete'] and not m['subtype']['complete']
    assert c.primary_provenance[0]['properties']==['P31']


@pytest.mark.parametrize('code',['history_unavailable','dependency_input_missing','invalid_historical_cache','maxlag'])
def test_optional_dependency_failure_is_not_primary_missing_evidence(config,code):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5')}
    m,c=run(items,rules(config),{'Q901':code})
    assert c.outcome=='completed' and c.primary_entity_type=='Person'
    assert c.subtype_status=='incomplete' and c.subtype_search_status=='dependency_missing'
    assert all(f['role']=='subtype' for f in m['frontier'])
    assert m['subtype']['frontier'][0]['source_code']==code


def test_required_primary_frontier_keeps_lower_precedence_winner_unresolved(config):
    items={'Q1':item('Q1',{'P31':['Q5','Q2']},'A'),'Q5':item('Q5')}
    m,c=run(items,rules(config))
    assert c.outcome=='unresolved' and c.primary_status=='unresolved'
    assert c.primary_entity_type is None and c.reason=='primary_ambiguity'
    assert c.primary_candidates[0]['group']=='Person' and c.subtype_status=='not_attempted'


def test_global_precedence_winner_can_prove_dominance_over_open_primary_branch(config):
    items={'Q1':item('Q1',{'P31':['Q41176','Q2']},'A'),'Q41176':item('Q41176')}
    m,c=run(items,rules(config))
    assert c.outcome=='completed' and c.primary_entity_type=='Landmark / Facility'
    assert c.primary_decision=='precedence_dominates_open_branches'
    assert not m['primary']['complete'] and c.subtypes[0]['subtype_id']=='Q41176'


def test_multiple_p31_roots_use_precedence_not_first_or_nearest(config):
    items={'Q1':item('Q1',{'P31':['Q3918','Q2']},'A'),'Q3918':item('Q3918'),
        'Q2':item('Q2',{'P279':['Q41176']}),'Q41176':item('Q41176')}
    m,c=run(items,rules(config));m2,c2=run(dict(reversed(list(items.items()))),rules(config))
    assert m==m2 and c==c2 and c.primary_entity_type=='Landmark / Facility'
    assert {x['group'] for x in c.primary_candidates}=={'Organization','Landmark / Facility'}


def test_p106_alone_never_proves_person_primary(config):
    m,c=run({'Q1':item('Q1',{'P106':['Q901']},'A'),'Q901':item('Q901')},rules(config))
    assert c.outcome=='unclassified' and c.primary_status=='unclassified'
    assert c.primary_entity_type is None and c.subtypes==[]
    assert [n['qid'] for n in m['nodes']]==['Q1']


def test_supported_subtype_and_partial_search_have_positive_provenance(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901','Q2']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    m,c=run(items,rules(config))
    assert c.outcome=='completed' and c.subtype_status=='supported'
    assert c.subtype_search_status=='dependency_missing' and not m['subtype']['complete']
    assert c.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert c.subtype_provenance[0]['properties']==['P106']


def test_closed_no_subtype_is_not_a_claim_of_no_real_world_subtype(config):
    m,c=run({'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')},rules(config))
    assert c.outcome=='completed' and c.subtype_status=='no_configured_subtype_found'
    assert c.subtype_search_status=='complete' and c.subtypes==[]


def test_missing_primary_and_cycles_do_not_invent_a_group(config):
    _,missing=run({'Q1':item('Q1',{'P31':['Q2']},'A')},rules(config))
    _,cycle=run({'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q2']})},rules(config))
    assert missing.outcome=='unresolved' and missing.reason=='primary_dependency_missing'
    assert cycle.outcome=='unclassified' and cycle.reason=='no_supported_rule'


def test_primary_owns_union_budget_before_optional_search(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    m,c=run(items,rules(config,max_nodes=2))
    assert len(m['nodes'])==2 and c.outcome=='completed' and c.primary_entity_type=='Person'
    assert m['subtype']['frontier'][0]['reason']=='hierarchy_node_budget_exhausted'
    _,blocked=run(items,rules(config,max_nodes=1))
    assert blocked.outcome=='unresolved'


def test_manifest_projection_tampering_and_declared_missing_artifact_are_rejected(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    m,c=run(items,r);projected={q:project_item(v,SNAP) for q,v in items.items()}
    assert classify_manifest(m,items,r)==classify_manifest(m,projected,r)==c
    projected['Q1']['entity']['claims']={}
    with pytest.raises(SourceUnavailable):classify_manifest(m,projected,r)
    with pytest.raises(SourceUnavailable) as error:classify_manifest(m,{q:v for q,v in items.items() if q!='Q901'},r)
    assert error.value.code=='dependency_input_missing' and error.value.details['role']=='subtype'


def test_unrelated_invalid_cache_and_extra_labels_do_not_change_logical_evidence(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}
    m,c=run(items,r)
    assert classify_manifest(m,{**items,'Q999':{'bad':'unrelated'}},r)==c
    assert asdict(c)['policy_version']=='primary-first-1'


def test_subtype_paths_retain_primary_budget_reservation_across_role_depths(config):
    items={'Q1':item('Q1',{'P31':['Q2'],'P106':['Q3']},'A'),'Q2':item('Q2',{'P279':['Q901']}),'Q901':item('Q901'),'Q3':item('Q3')}
    m,c=run(items,rules(config,max_nodes=3))
    assert c.outcome=='completed' and c.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert c.subtype_provenance[0]['properties']==['P31','P279']


@pytest.mark.parametrize('root_depth',[12,13])
def test_primary_depth_boundary_is_identical_for_cold_and_extra_cached_deep_root(config,root_depth):
    r=rules(config);values={'Q1':item('Q1',{'P31':['Q1001']},'A'),'Q5':item('Q5')}
    for depth in range(1,root_depth):
        values[f'Q{1000+depth}']=item(f'Q{1000+depth}',{'P279':['Q5' if depth==root_depth-1 else f'Q{1001+depth}']})
    m,c=run(values,r)
    assert c.outcome==('completed' if root_depth==12 else 'unresolved')
    if root_depth==13:
        cold={q:v for q,v in values.items() if q!='Q5'}
        assert run(cold,r)==(m,c)


def test_primary_budget_cannot_hide_conflicting_sibling_even_with_human_proof(config):
    m,c=run({'Q1':item('Q1',{'P31':['Q5','Q9']},'A'),'Q5':item('Q5'),'Q9':item('Q9')},rules(config,max_nodes=2))
    assert c.outcome=='unresolved' and c.reason=='primary_ambiguity'
    assert m['primary']['frontier'][0]['reason']=='hierarchy_node_budget_exhausted'


def test_direct_configured_root_identity_preserves_explicit_legacy_taxonomy(config):
    r=rules(config);a=article();values={'Q43229':item('Q43229',{},'A')}
    m=make_manifest(a,'Q43229',lambda q:values[q],r,SNAP)
    c=classify_manifest(m,values,r)
    assert c.outcome=='completed' and c.primary_entity_type=='Organization'
    assert c.primary_provenance==[{'path':[{'qid':'Q43229','revision_id':43329,'revision_timestamp':'2026-10-13T00:00:00Z'}],'properties':[],'root':'Q43229'}]


def test_invalid_optional_p106_does_not_discard_independently_proven_p31_subtype(config):
    a=item('Q1',{'P31':['Q901']},'A');a['entity']['claims']['P106']='invalid'
    m,c=run({'Q1':a,'Q901':item('Q901')},rules(config))
    assert c.outcome=='completed' and c.subtype_status=='supported'
    assert c.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert c.subtype_provenance[0]['properties']==['P31']
    assert c.subtype_search_status=='dependency_missing'
    assert m['subtype']['frontier'][0]['property']=='P106'


@pytest.mark.parametrize('bad_hash',[None,123,False])
def test_invalid_compact_source_hash_is_a_typed_input_error(config,bad_hash):
    r=rules(config);values={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')};m,_=run(values,r)
    compact={q:project_item(x,SNAP) for q,x in values.items()};compact['Q1']['source_entity_hash']=bad_hash
    with pytest.raises(SourceUnavailable) as error:classify_manifest(m,compact,r)
    assert error.value.code=='dependency_input_invalid'
