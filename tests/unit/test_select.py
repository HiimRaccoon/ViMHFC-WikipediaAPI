from tests.helpers import article
from vimfc.pipeline.select import select_corpus


def test_partner_bundle_and_stable_ties(config):
    candidates=[article(1,quality=80),article(2,quality=60,links=[3]),article(3,quality=60)]
    pair={'kind':'bridge','endpoint_a':2,'endpoint_b':3,'score':1000}
    result=select_corpus(candidates,[pair],config['ranking'],config['quotas'],target=2)
    assert sorted(result.page_ids)==[2,3] and len(result.steps)==1
    assert select_corpus(list(reversed(candidates)),[pair],config['ranking'],config['quotas'],target=2)==result
    ties=[article(2),article(1)]
    assert select_corpus(ties,[],config['ranking'],config['quotas'],target=1).page_ids==[1]


def test_shortage_never_fills_with_ineligible_and_quotas_are_soft(config):
    a=article(1)
    b=article(2)
    b['quality']['eligible']=False
    result=select_corpus([a,b],[],config['ranking'],config['quotas'],target=2)
    assert result.outcome=='insufficient_supply' and result.shortage==1
    assert result.page_ids==[1]


def test_soft_deficits_favor_an_underrepresented_group_without_a_hard_quota(config):
    candidates=[article(1),article(2),article(3,group='Organization',subtype='Q3918')]
    result=select_corpus(candidates,[],config['ranking'],config['quotas'],target=2)
    assert result.page_ids==[1,3]


def test_subtype_novelty_preserves_diversity_within_the_same_primary_group(config):
    candidates=[article(1),article(2),article(3,subtype='Q33999')]
    result=select_corpus(candidates,[],config['ranking'],config['quotas'],target=2)
    assert result.page_ids==[1,3]


def test_hub_penalty_prevents_link_count_alone_from_winning(config):
    candidates=[article(1,links=range(2,28))]+[article(i) for i in range(2,28)]
    result=select_corpus(candidates,[],config['ranking'],config['quotas'],target=1)
    assert result.page_ids==[2]
