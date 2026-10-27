from pathlib import Path
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality


def test_quality_requires_supported_type_complete_sentences_and_factual_signal(config):
    result=extract_lead(Path('tests/fixtures/lead.wiki').read_text(encoding='utf-8'),config['extraction'])
    quality=assess_quality(result,{'outcome':'completed','primary_entity_type':'Person'},[],config['extraction'])
    assert quality['eligible'] and quality['signals'] and quality['score']>0
    assert any(s['rule']=='birth' and s['sent_id']==1 for s in quality['signals'])
    assert not assess_quality(result,{'outcome':'pending'},[],config['extraction'])['eligible']
    assert not assess_quality(result,{'outcome':'completed','primary_entity_type':'Person'},['disambiguation'],config['extraction'])['eligible']


def test_short_and_unknown_template_leads_are_not_research_candidates(config):
    result=extract_lead('Ông sinh năm 1980. Ông ở Hà Nội.',config['extraction'])
    assert 'too_few_tokens' in assess_quality(result,{'outcome':'completed','primary_entity_type':'Person'},[],config['extraction'])['reasons']


def test_year_is_bound_to_its_factual_clause_not_every_attribute(config):
    result=extract_lead('Ông sinh tại Hà Nội và thành lập Viện A năm 2001. Ông làm việc tại đây.',config['extraction'])
    signals=assess_quality(result,{'outcome':'completed','primary_entity_type':'Person'},[],config['extraction'])['signals']
    founded=next(s for s in signals if s['rule']=='founded')
    assert not any(s['rule']=='birth' and s['value_kind']=='year' for s in signals)
    assert founded['value_kind']=='year' and founded['values']==['2001']


def test_quality_replays_after_canonical_rule_serialization(config):
    import json
    from vimfc.domain import canonical
    result=extract_lead('Ông sinh năm 1980, là tác giả nổi tiếng. Ông gia nhập viện nghiên cứu năm 2001.',config['extraction'])
    cls={'outcome':'completed','primary_entity_type':'Person'}
    assert assess_quality(result,cls,[],config['extraction'])==assess_quality(result,cls,[],json.loads(canonical(config['extraction'])))
