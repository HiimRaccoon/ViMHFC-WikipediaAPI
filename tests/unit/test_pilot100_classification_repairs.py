from vimfc.pipeline.classify import classify
from tests.unit.test_classify import item,SNAP


def test_known_root_terminates_its_ancestor_branch(config):
    items={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5',{'P279':['Q999']})}
    result=classify('A','Q1',items,config['classification'],SNAP)
    assert result.outcome=='completed' and result.primary_entity_type=='Person'


def test_known_root_cannot_hide_a_necessary_missing_sibling(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q3']})}
    result=classify('A','Q1',items,config['classification'],SNAP)
    assert result.outcome=='unresolved' and result.reason=='missing_or_invalid_historical_dependency'


def test_all_sibling_roots_resolve_before_precedence_and_subtypes(config):
    items={'Q1':item('Q1',{'P31':['Q3918','Q41176']},'A'),'Q3918':item('Q3918',{'P279':['Q999']}),'Q41176':item('Q41176',{'P279':['Q888']})}
    result=classify('A','Q1',items,config['classification'],SNAP)
    assert result.outcome=='completed' and result.primary_entity_type=='Landmark / Facility'
    assert result.subtypes==[{'subtype_id':'Q41176','name':'Building'}]


def test_unresolved_sibling_at_depth_limit_remains_pending(config):
    items={'Q1':item('Q1',{'P31':['Q5','Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q3']})}
    rules={**config['classification'],'cached_closure_completion':False,'terminal_unresolved':False,'max_depth':1}
    result=classify('A','Q1',items,rules,SNAP)
    assert result.outcome=='pending' and result.reason=='truncated_hierarchy'


def test_cycle_back_to_already_visited_node_at_depth_limit_is_not_truncation(config):
    items={'Q1':item('Q1',{'P31':['Q5','Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q2','Q1']})}
    rules={**config['classification'],'max_depth':1}
    assert classify('A','Q1',items,rules,SNAP).outcome=='completed'


def test_depth_limit_waits_for_required_children_already_queued_as_siblings(config):
    items={'Q1':item('Q1',{'P31':['Q5','Q2','Q3']},'A'),'Q5':item('Q5'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3')}
    rules={**config['classification'],'max_depth':1}
    assert classify('A','Q1',items,rules,SNAP).outcome=='completed'


def test_empty_json_arrays_for_claims_and_sitelinks_are_valid_empty_maps(config):
    items={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}
    items['Q5']['entity'].update({'claims':[],'sitelinks':[]})
    assert classify('A','Q1',items,config['classification'],SNAP).outcome=='completed'
    items['Q1']['entity']['sitelinks']=[]
    assert classify('A','Q1',items,config['classification'],SNAP).reason=='historical_sitelink_mismatch'
