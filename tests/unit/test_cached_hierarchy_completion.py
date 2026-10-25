from copy import deepcopy
import pytest
from vimfc.pipeline.classify import classify
from tests.unit.test_classify import item,SNAP


def rules(config,**extra):
    return {**deepcopy(config['classification']),'cached_closure_completion':True,'terminal_unresolved':True,'max_cached_nodes':100,'max_depth':1,**extra}


def test_closed_cached_ancestors_beyond_network_depth_resolve_all_sibling_subtypes(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),
        'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q901','Q3']}),'Q901':item('Q901')}
    result=classify('A','Q1',items,rules(config),SNAP)
    assert result.outcome=='completed'
    assert result.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert any(len(p['properties'])==3 for p in result.provenance)


def test_cached_sibling_can_change_precedence_beyond_network_depth(config):
    items={'Q1':item('Q1',{'P31':['Q3918','Q2']},'A'),'Q3918':item('Q3918'),
        'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q41176']}),'Q41176':item('Q41176')}
    result=classify('A','Q1',items,rules(config),SNAP)
    assert result.outcome=='completed' and result.primary_entity_type=='Landmark / Facility'


def test_finite_unsupported_cached_cycle_is_proven_no_supported_rule(config):
    items={'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q2']})}
    result=classify('A','Q1',items,rules(config),SNAP)
    assert result.outcome=='unclassified' and result.reason=='no_supported_rule'


@pytest.mark.parametrize('defect',['missing','future','identity'])
def test_known_root_does_not_hide_missing_or_invalid_cached_frontier(config,defect):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),
        'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3')}
    if defect=='missing': del items['Q3']
    if defect=='future': items['Q3']['revision_timestamp']='2026-10-15T00:00:00Z'
    if defect=='identity': items['Q3']['entity']['id']='Q999'
    result=classify('A','Q1',items,rules(config),SNAP)
    assert result.outcome=='unresolved' and result.primary_entity_type is None
    assert result.reason=='missing_or_invalid_historical_dependency'
    assert any(d['qid']=='Q3' for d in result.diagnostics)


def test_cached_node_budget_is_terminal_unresolved_with_a_reason(config):
    items={'Q1':item('Q1',{'P31':['Q5','Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2')}
    result=classify('A','Q1',items,rules(config,max_cached_nodes=1),SNAP)
    assert result.outcome=='unresolved' and result.reason=='hierarchy_node_budget_exhausted'
    assert result.diagnostics


def test_legacy_rules_still_report_depth_truncation(config):
    items={'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3')}
    result=classify('A','Q1',items,{**config['classification'],'cached_closure_completion':False,'terminal_unresolved':False,'max_depth':1},SNAP)
    assert result.outcome=='pending' and result.reason=='truncated_hierarchy'
