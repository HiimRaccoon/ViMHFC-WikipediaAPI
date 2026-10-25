from tests.integration.test_pair_generation_pipeline import pair_articles
from vimfc.pipeline.pairs import build_pairs
from vimfc.pipeline.report import pair_generation_audit


def test_pair_audit_counts_real_graph_and_block_opportunities(config):
    articles=pair_articles(config)
    manifest=build_pairs(articles,config['ranking'])
    audit=pair_generation_audit(articles,manifest.pairs,config['ranking'])
    assert audit['eligible_article_count']==3
    assert audit['directed_internal_edge_count']==1
    assert audit['multi_article_comparison_block_count']==2
    assert audit['generated_counts']=={'bridge':1,'comparison':1}
    assert audit['positive_counts']=={'bridge':1,'comparison':1}


def test_pair_audit_explains_zero_opportunities_without_synthetic_links(config):
    a=pair_articles(config)[0]
    a['extraction']['links'][0]['target']['page_id']=a['page_id']
    audit=pair_generation_audit([a],[],config['ranking'])
    assert audit['self_link_occurrences']==1
    assert audit['directed_internal_edge_count']==0
    assert audit['multi_article_comparison_block_count']==0
    assert audit['zero_pair_reasons']==['no_internal_nonself_lead_edge','no_shared_subtype_attribute_block']
