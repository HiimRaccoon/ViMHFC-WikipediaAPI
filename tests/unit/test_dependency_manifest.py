from copy import deepcopy
import pytest
from vimfc.domain import SourceUnavailable,Incompatible
from vimfc.pipeline.dependencies import make_manifest,classify_manifest,article_identity
from tests.unit.test_classify import item,SNAP


def rules(config,**policy):
    return {**deepcopy(config['classification']),'version':'qid-4','cached_closure_completion':False,
        'dependency_policy':{'version':'bounded-bfs-1','max_depth':12,'max_nodes':2048,**policy}}


def article():
    return {'site':'viwiki','page_id':1,'title':'A','revision_id':77,'revision_timestamp':'2026-10-13T00:00:00Z','content_hash':'a'*64}


def manifest(items,r):
    return make_manifest(article(),'Q1',lambda q:items[q],r,SNAP)


def test_same_manifest_ignores_extra_physical_cache_and_preserves_subtype(config):
    r=rules(config)
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    m=manifest(items,r)
    warm={**items,'Q999':{'invalid':'unrelated'}}
    a=classify_manifest(m,items,r)
    b=classify_manifest(m,warm,r)
    assert a==b and a.outcome=='completed'
    assert a.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert m['complete'] and m['manifest_id']==manifest(warm,r)['manifest_id']


def test_missing_declared_artifact_raises_input_error_instead_of_smaller_classification(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    m=manifest(items,r)
    with pytest.raises(SourceUnavailable) as error: classify_manifest(m,{q:x for q,x in items.items() if q!='Q901'},r)
    assert error.value.code=='dependency_input_missing'


def test_depth_12_13_is_semantic_bound_even_when_deep_node_is_cached(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q1001']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    for i in range(1001,1013): items[f'Q{i}']=item(f'Q{i}',{'P279':[f'Q{i+1}' if i<1012 else 'Q901']})
    cold={q:x for q,x in items.items() if q!='Q901'}
    a=manifest(cold,r);b=manifest(items,r)
    assert a==b and not a['complete']
    assert {f['reason'] for f in a['frontier']}=={'dependency_policy_limit'}
    assert classify_manifest(a,items,r).reason=='dependency_policy_limit'
    assert all(n['qid']!='Q901' for n in a['nodes'])


def test_cycles_and_shared_queued_children_close_before_no_supported_rule(config):
    r=rules(config,max_depth=1)
    items={'Q1':item('Q1',{'P31':['Q2','Q3']},'A'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q2']})}
    m=manifest(items,r)
    assert m['complete'] and classify_manifest(m,items,r).outcome=='unclassified'


def test_budget_is_logical_and_known_root_cannot_hide_a_sibling(config):
    r=rules(config,max_nodes=2)
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q2']},'A'),'Q5':item('Q5'),'Q2':item('Q2')}
    m=manifest(items,r)
    assert not m['complete'] and len(m['nodes'])==2
    assert classify_manifest(m,items,r).reason=='hierarchy_node_budget_exhausted'


def test_history_unavailable_evidence_is_distinct_from_policy_and_missing_input(config):
    r=rules(config)
    def load(q):
        if q=='Q1': return item(q,{'P31':['Q2']},'A')
        raise SourceUnavailable('No matching historical revision',code='history_unavailable')
    m=make_manifest(article(),'Q1',load,r,SNAP)
    assert m['frontier'][0]['reason']=='history_unavailable'
    assert classify_manifest(m,{'Q1':load('Q1')},r).reason=='history_unavailable'


def test_manifest_tampering_or_wrong_rules_is_incompatible(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}
    m=manifest(items,r);bad=deepcopy(m);bad['complete']=False
    with pytest.raises(Incompatible): classify_manifest(bad,items,r)
    with pytest.raises(Incompatible): classify_manifest(m,items,rules(config,max_depth=11))


def test_entity_hash_binds_claims_and_future_dependencies_are_rejected(config):
    r=rules(config);items={'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}
    m=manifest(items,r);bad=deepcopy(items);bad['Q1']['entity']['claims']={}
    with pytest.raises(SourceUnavailable) as error: classify_manifest(m,bad,r)
    assert error.value.code=='dependency_input_invalid'
    bad=deepcopy(items);bad['Q5']['revision_timestamp']='2026-10-15T00:00:00Z'
    with pytest.raises(SourceUnavailable): manifest(bad,r)


def test_precedence_and_all_required_subtypes_are_preserved(config):
    r=rules(config)
    items={'Q1':item('Q1',{'P31':['Q3918','Q2'],'P106':['Q901']},'A'),'Q3918':item('Q3918'),
        'Q2':item('Q2',{'P279':['Q41176','Q2']}),'Q41176':item('Q41176'),'Q901':item('Q901')}
    m=manifest(items,r);result=classify_manifest(m,items,r)
    assert m['complete'] and result.primary_entity_type=='Landmark / Facility'
    assert result.subtypes==[{'subtype_id':'Q41176','name':'Building'}]
    assert len(result.provenance)==3


@pytest.mark.parametrize('size',[2047,2048,2049])
def test_actual_2048_node_boundary_has_no_false_completion(config,size):
    r=rules(config);children=[f'Q{i}' for i in range(10000000,10000000+size-1)]
    items={'Q1':item('Q1',{'P31':children},'A'),**{q:item(q) for q in children}}
    m=manifest(items,r);result=classify_manifest(m,items,r)
    assert len(m['nodes'])==min(size,2048)
    assert m['complete']==(size<=2048)
    assert result.outcome==('unclassified' if size<=2048 else 'unresolved')


@pytest.mark.parametrize('mutation',['statement','mainsnak','datavalue','target_id','sitelinks','title','claims_scalar','claims_null','claims_list','scalar_value','empty_snak'])
def test_malformed_consumed_entity_shape_is_typed_invalid_input(config,mutation):
    x=item('Q1',{'P31':['Q5']},'A')
    if mutation=='statement':x['entity']['claims']['P31']=[None]
    if mutation=='mainsnak':x['entity']['claims']['P31'][0]['mainsnak']=None
    if mutation=='datavalue':x['entity']['claims']['P31'][0]['mainsnak']['datavalue']=None
    if mutation=='target_id':x['entity']['claims']['P31'][0]['mainsnak']['datavalue']['value']['id']=123
    if mutation=='sitelinks':x['entity']['sitelinks']='bad'
    if mutation=='title':x['entity']['sitelinks']['viwiki']['title']=None
    if mutation=='claims_scalar':x['entity']['claims']=''
    if mutation=='claims_null':x['entity']['claims']=None
    if mutation=='claims_list':x['entity']['claims']=[{}]
    if mutation=='scalar_value':x['entity']['claims']['P31'][0]['mainsnak']['datavalue']['value']='bad'
    if mutation=='empty_snak':x['entity']['claims']['P31'][0]['mainsnak']={}
    with pytest.raises(SourceUnavailable) as error:manifest({'Q1':x,'Q5':item('Q5')},rules(config))
    assert error.value.code=='dependency_input_invalid'


def test_explicit_empty_php_containers_are_supported_without_falsy_scalar_coercion(config):
    r=rules(config);a=item('Q1',{},'A');a['entity']['claims']=[]
    m=manifest({'Q1':a},r)
    assert m['complete'] and classify_manifest(m,{'Q1':a},r).outcome=='unclassified'
    a=item('Q1',{'P31':['Q5']},'A');b=item('Q5');b['entity']['sitelinks']=[];b['entity']['claims']=[]
    m=manifest({'Q1':a,'Q5':b},r)
    assert m['complete'] and classify_manifest(m,{'Q1':a,'Q5':b},r).outcome=='completed'


@pytest.mark.parametrize('timestamp',[None,123,False,'invalid'])
def test_malformed_node_timestamp_is_typed_invalid_input(config,timestamp):
    x=item('Q1',{},'A');x['revision_timestamp']=timestamp
    with pytest.raises(SourceUnavailable) as error:manifest({'Q1':x},rules(config))
    assert error.value.code=='dependency_input_invalid'
