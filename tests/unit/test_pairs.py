from copy import deepcopy
from tests.helpers import article
from vimfc.pipeline.pairs import build_pairs


def test_lead_bridges_are_directed_and_comparison_requires_compatible_attributes(config):
    a,b,c=article(1,links=[2]),article(2),article(3,subtype='Q11424')
    for candidate in (a,b,c):
        candidate['quality']['signals']=[s for s in candidate['quality']['signals'] if s['rule']=='birth']
    b['quality']['signals'][0].update(rule='founded',attribute_key='founded')
    manifest=build_pairs([a,b,c],config['ranking'])
    assert [(p['endpoint_a'],p['endpoint_b']) for p in manifest.pairs if p['kind']=='bridge']==[(1,2)]
    assert not [p for p in manifest.pairs if p['kind']=='comparison']
    b['quality']['signals'][0].update(rule='birth',attribute_key='birth')
    manifest=build_pairs([a,b,c],config['ranking'])
    comparison=[p for p in manifest.pairs if p['kind']=='comparison']
    assert len(comparison)==1 and comparison[0]['features']['attribute_compatibility']==100
    assert comparison[0]['provenance']


def test_partner_caps_deterministic_replay_and_no_full_article_links(config):
    articles=[article(i) for i in range(1,31)]
    articles[0]['full_article_links']=[2]
    rules={**config['ranking'],'comparison_cap':3}
    first=build_pairs(articles,rules)
    second=build_pairs(list(reversed(articles)),rules)
    assert first==second and first.omitted['comparison']>0
    counts={i:0 for i in range(1,31)}
    for pair in first.pairs:
        assert pair['kind']=='comparison'
        counts[pair['endpoint_a']]+=1
        counts[pair['endpoint_b']]+=1
    assert max(counts.values())<=3
