from vimfc.pipeline.classify import classify

SNAP='2026-10-14T00:00:00Z'


def item(qid, claims=None, title=None):
    return {'revision_id':int(qid[1:])+100,'revision_timestamp':'2026-10-13T00:00:00Z',
            'entity':{'id':qid,'sitelinks':{'viwiki':{'title':title}} if title else {},'claims':{
                prop:[{'mainsnak':{'snaktype':'value','datavalue':{'value':{'id':target}}}} for target in targets]
                for prop,targets in (claims or {}).items()}}}


def test_occupation_and_hierarchy_paths_backed_by_historical_revisions(config):
    items={'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}
    result=classify('A','Q1',items,config['classification'],SNAP)
    assert result.primary_entity_type=='Person'
    assert result.subtypes==[{'subtype_id':'Q901','name':'Scientist'}]
    assert any(p['properties']==['P106'] for p in result.provenance)
    items.pop('Q901')
    assert classify('A','Q1',items,config['classification'],SNAP).outcome=='unresolved'


def test_specific_group_precedence_and_missing_sitelink(config):
    items={'Q1':item('Q1',{'P31':['Q3918','Q41176']},'A'),'Q3918':item('Q3918'),'Q41176':item('Q41176')}
    assert classify('A','Q1',items,config['classification'],SNAP).primary_entity_type=='Landmark / Facility'
    assert classify('B','Q1',items,config['classification'],SNAP).outcome=='unresolved'
    items['Q41176']['revision_timestamp']='2026-10-15T00:00:00Z'
    assert classify('A','Q1',items,config['classification'],SNAP).outcome=='unresolved'


def test_cycle_and_truncated_closure_never_guess(config):
    items={'Q1':item('Q1',{'P31':['Q2']},'A'),'Q2':item('Q2',{'P279':['Q3']}),'Q3':item('Q3',{'P279':['Q2']})}
    assert classify('A','Q1',items,config['classification'],SNAP).outcome=='unclassified'
    result=classify('A','Q1',items,{**config['classification'],'cached_closure_completion':False,'terminal_unresolved':False,'max_depth':1},SNAP)
    assert result.outcome=='pending' and result.reason=='truncated_hierarchy'
