from copy import deepcopy
import pytest
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality


def relaxed(config):
    return {**config['extraction'], 'quality_version': 'quality-relaxed-1',
            'quality_revision': 'quality-relaxed-1', 'min_sentences': 1, 'min_tokens': 0}


@pytest.mark.parametrize('outcome', ['completed', 'unclassified', 'unresolved'])
def test_one_short_factual_sentence_does_not_require_verified_primary(config, outcome):
    rules = relaxed(config)
    ext = extract_lead('Viện Sao Mai được thành lập năm 2000.', rules)
    cls = {'outcome': outcome, 'primary_entity_type': None, 'subtypes': []}
    before = deepcopy(cls)
    q = assess_quality(ext, cls, [], rules)
    assert q['eligible'] and q['signals'] and cls == before
    assert q['quality_version'] == 'quality-relaxed-1'
    assert not assess_quality(ext, cls, [], config['extraction'])['eligible']


def test_literal_definition_is_factual_without_old_relation_patterns(config):
    rules = relaxed(config)
    ext = extract_lead('Sao Mai là một thành phố thuộc [[Việt Nam]].', rules)
    q = assess_quality(ext, {'outcome': 'unclassified'}, [], rules)
    assert q['eligible'] and any(s['rule'] == 'definition' for s in q['signals'])


def test_complete_factual_sentence_has_no_hidden_token_floor(config):
    rules = relaxed(config)
    ext = extract_lead('A là đảo.', rules)
    assert len(ext.full_lead.split()) == 3
    assert assess_quality(ext, {'outcome': 'unclassified'}, [], rules)['eligible']


@pytest.mark.parametrize('text', ['', '{{Infobox|a=b}}', 'Thật tuyệt vời.',
                                   'Đây là một bài viết.', 'Sao Mai rất đẹp.',
                                   'Viện dự kiến thành lập năm 2030.',
                                   'Viện được thành lập năm 2000'])
def test_empty_nonfactual_uncertain_or_incomplete_lead_is_rejected(config, text):
    rules = relaxed(config)
    q = assess_quality(extract_lead(text, rules), {'outcome': 'unresolved'}, [], rules)
    assert not q['eligible']


@pytest.mark.parametrize('kind', ['redirect', 'disambiguation', 'list'])
def test_article_kind_and_unresolved_extraction_still_block(config, kind):
    rules = relaxed(config)
    ext = extract_lead('Viện Sao Mai được thành lập năm 2000.', rules)
    assert not assess_quality(ext, {'outcome': 'unclassified'}, [kind], rules)['eligible']
    ext.outcome = 'unresolved_extraction'
    assert not assess_quality(ext, {'outcome': 'completed'}, [], rules)['eligible']
