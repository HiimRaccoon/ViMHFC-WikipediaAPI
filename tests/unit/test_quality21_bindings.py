import json
from pathlib import Path
import pytest
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality


def assess(text,config):
    ext=extract_lead(text,config['extraction'])
    return ext,assess_quality(ext,{'outcome':'completed'},[],config['extraction'])


@pytest.mark.parametrize('text',[
    'Trường được thành lập (1997).',
    'Trường được thành lập đầu tiên trên cả nước (1997).',
    'Trường được thành lập chính thức (1997).',
])
def test_bare_parenthetical_founding_year_with_no_intervening_object(config,text):
    ext,q=assess(text,config)
    s=next(s for s in q['signals'] if s['rule']=='founded')
    assert s['value_kind']=='year' and s['values']==['1997']
    assert s['binding_reason']=='founding_parenthetical_year'
    assert ext.sentences[0]['text'][s['value_spans'][0]['start']:s['value_spans'][0]['end']]=='1997'
    assert q['quality_revision']=='quality-2.1'


def test_coordinated_founder_role_has_one_explicit_shared_linked_object(config):
    ext,q=assess('Ông là người thành lập và là hiệu trưởng đầu tiên của [[Trường Y khoa Hà Nội]] (tiền thân của [[Đại học Y Hà Nội]]).',config)
    s=next(s for s in q['signals'] if s['rule']=='founded')
    assert s['value_kind']=='entity' and s['values']==['Trường Y khoa Hà Nội']
    assert s['binding_reason']=='coordinated_founding_object'
    v=s['value_spans'][0]
    assert ext.sentences[0]['text'][v['start']:v['end']]=='Trường Y khoa Hà Nội'


@pytest.mark.parametrize('text',[
    'Trường được thành lập theo Nghị định (1997).',
    'Trường được thành lập bởi [[Công ty 1997]].',
    'Trường được thành lập trước bộ phim (1997).',
    'Trường dự kiến thành lập (2030).',
    'Trường không thành lập (1997).',
    'Trường được thành lập (1997–2000).',
    'Ông thành lập và chưa là hiệu trưởng của [[Trường A]].',
    'Ông thành lập và giải thể [[Trường A]].',
])
def test_parentheses_and_coordinated_ambiguity_cannot_prove_new_founding_binding(config,text):
    _,q=assess(text,config)
    assert not any(s.get('binding_reason') in ('founding_parenthetical_year','coordinated_founding_object') for s in q['signals'])
    assert not any(s['rule']=='founded' and s['value_kind']=='year' and len(s['values'])==1 for s in q['signals'])


def test_two_explicit_objects_are_not_collapsed_into_shared_object(config):
    _,q=assess('Ông thành lập [[Trường A]] và là hiệu trưởng của [[Trường B]].',config)
    assert next(s for s in q['signals'] if s['rule']=='founded')['values']==['Trường A']


def test_previous_quality2_rules_preserve_both_archived_false_negatives():
    old=json.loads(Path('configs/legacy/extraction-lead-4-quality-2.json').read_text(encoding='utf-8'))
    for text in ['Trường được thành lập đầu tiên trên cả nước (1997).','Ông thành lập và là hiệu trưởng đầu tiên của [[Trường A]].']:
        assert assess_quality(extract_lead(text,old),{'outcome':'completed'},[],old)['signals']==[]
