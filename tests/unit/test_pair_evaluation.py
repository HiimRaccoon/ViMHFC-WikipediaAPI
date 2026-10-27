from importlib import import_module
from copy import deepcopy
import pytest
from vimfc.domain import Incompatible

ALLOCATION={('Q13406554','event_end','year'):22,('Q33999','birth','year'):8,('Q937857','birth','year'):6,
    ('Q3918','founded','year'):6,('Q13406554','event_start','year'):4,('Q13406554','event','year'):3,('Q11424','release','year'):1}

def pairs():
    result=[{'kind':'bridge','endpoint_a':i,'endpoint_b':i+1,'features':{'shortcut_risk':10},'provenance':[]} for i in range(1,30)]
    index=100
    for block,n in ALLOCATION.items():
        for _ in range(n+3):
            result.append({'kind':'comparison','endpoint_a':index,'endpoint_b':index+1,'features':{'shortcut_risk':10},'provenance':[{'block':list(block)}]});index+=2
    return result

def test_manual_sample_has_29_bridges_and_50_stratified_comparisons_no_human_labels():
    api=import_module('vimfc.pipeline.pair_evaluation');sample=api.sample_pairs(pairs(),seed='review-79')
    assert len(sample['pairs'])==79
    assert sum(p['kind']=='bridge' for p in sample['pairs'])==29
    assert sum(p['kind']=='comparison' for p in sample['pairs'])==50
    assert sample['human_validated'] is False
    assert sample['valid_two_hop_yield'] is None
    assert all(0<p['inclusion_probability']<=1 for p in sample['pairs'])
    for block,n in ALLOCATION.items():
        assert sum(tuple(p.get('block',[]))==block for p in sample['pairs'])==n
    assert api.sample_pairs(list(reversed(pairs())),seed='review-79')==sample

def test_missing_stratum_or_duplicate_pair_is_not_silently_replaced():
    api=import_module('vimfc.pipeline.pair_evaluation');source=pairs()
    source=[p for p in source if not p['provenance'] or p['provenance'][0]['block'][0]!='Q3918']
    with pytest.raises(Incompatible):api.sample_pairs(source,seed='review-79')

def records():
    output={}
    for pair in pairs():
        for page in (pair['endpoint_a'],pair['endpoint_b']):
            output.setdefault(page,{'page_id':page,'title':f'A{page}','revision_id':page+1000,'revision_timestamp':'2026-10-24T00:00:00Z',
                'snapshot':'2026-10-25T03:00:00Z','raw_hash':'a'*64,'source_url':'fixture',
                'quality':{'eligible':True,'signals':[]},'classification':{'subtypes':[],'primary_provenance':[],'subtype_provenance':[], 'manifest_id':'b'*64},
                'extraction':{'outcome':'completed','paragraphs':['Entity 2000.'],'full_lead':'Entity 2000.','sentences':[{'sent_id':0,'paragraph_id':0,'text':'Entity 2000.','start':0,'end':12}],'links':[]}})
            if pair['kind']=='comparison':
                block=pair['provenance'][0]['block'];output[page]['classification']['subtypes'].append({'subtype_id':block[0]})
                output[page]['quality']['signals'].append({'sent_id':0,'attribute_key':block[1],'value_kind':'year','values':[2000],'value_spans':[{'start':7,'end':11,'text':'2000'}]})
        if pair['kind']=='bridge':
            output[pair['endpoint_a']]['extraction']['links'].append({'scope':'retained_lead_prose','sent_id':0,'paragraph_id':0,'start':0,'end':6,'link_text':'Entity',
                'target':{'status':'resolved','page_id':pair['endpoint_b'],'revision_id':pair['endpoint_b']+1000,'revision_timestamp':'2026-10-24T00:00:00Z','snapshot':'2026-10-25T03:00:00Z','raw_hash':'a'*64}})
    return output

def test_review_pack_retains_full_evidence_and_does_not_assign_validity():
    api=import_module('vimfc.pipeline.pair_evaluation');pack=api.build_review_pack(records(),pairs(),seed='review-79')
    assert len(pack['pairs'])==79 and pack['human_validation']['status']=='unassessed'
    assert all(p['human_verdict'] is None and len(p['documents'])==2 for p in pack['pairs'])
    first=next(p for p in pack['pairs'] if p['kind']=='bridge')
    assert first['witness']['scope']=='retained_lead_prose'
    bad=records();bad[1]['extraction']['links'][0]['link_text']='Wrong'
    with pytest.raises(Incompatible):api.build_review_pack(bad,pairs(),seed='review-79')

def test_written_pack_has_separate_blank_review_sheets_and_readable_pair_cards(tmp_path):
    api=import_module('vimfc.pipeline.pair_evaluation');pack=api.build_review_pack(records(),pairs(),seed='review-79')
    api.write_review_pack(tmp_path/'pack',pack)
    assert len(list((tmp_path/'pack/pairs').glob('*.md')))==79
    assert (tmp_path/'pack/annotator-a.csv').read_bytes()==(tmp_path/'pack/annotator-b.csv').read_bytes()
    with pytest.raises(FileExistsError):api.write_review_pack(tmp_path/'pack',pack)

def test_comparison_without_literal_value_witnesses_is_rejected():
    api=import_module('vimfc.pipeline.pair_evaluation');bad=records()
    for row in bad.values():
        for signal in row['quality']['signals']:signal.pop('value_spans',None)
    with pytest.raises(Incompatible):api.build_review_pack(bad,pairs(),seed='review-79')
    source=pairs();source.append(deepcopy(source[0]))
    with pytest.raises(Incompatible):api.sample_pairs(source,seed='review-79')
