from copy import deepcopy
from tests.helpers import article
from vimfc.pipeline.select import select_corpus


def rules(config):
    return {**deepcopy(config['ranking']), 'version': 'rank-hyperlink-1',
            'selection_version': 'hyperlink-greedy-1'}


def test_short_connected_chain_precedes_long_isolated_article(config):
    items = [article(1, links=[2], quality=40), article(2, links=[3], quality=40),
             article(3, quality=40), article(4, quality=100)]
    result = select_corpus(items, [], rules(config), config['quotas'], target=3)
    assert set(result.page_ids) == {1, 2, 3}
    assert select_corpus(list(reversed(items)), [], rules(config), config['quotas'], target=3) == result


def test_unknown_primary_and_no_link_fallback_do_not_change_metadata(config):
    items = [article(1), article(2, group='Organization', subtype='Q3918')]
    items[0]['classification'].update(outcome='unclassified', primary_entity_type=None, subtypes=[])
    before = deepcopy(items)
    result = select_corpus(items, [], rules(config), config['quotas'], target=3)
    assert set(result.page_ids) == {1, 2} and result.shortage == 1
    assert items == before


def test_three_graph_scopes_and_distinct_two_hop_paths(config):
    from vimfc.pipeline.hyperlink_rank import graph_metrics
    items = [article(1, links=[2, 999]), article(2, links=[1, 3]), article(3)]
    m = graph_metrics(items)
    assert m['directed_nonself_edges'] == 3 and m['nonisolated_vertices'] == 3
    assert m['potential_two_hop_paths'] == 1 and m['outside_pool_link_occurrences'] == 1
    assert m['two_hop_examples'] == [[1, 2, 3]]


def test_diversity_and_external_degree_do_not_beat_internal_connectivity(config):
    items = [article(1, links=range(100, 200)), article(2, links=[3]),
             article(3, group='Organization', subtype='Q3918')]
    result = select_corpus(items, [], rules(config), config['quotas'], target=2)
    assert set(result.page_ids) == {2, 3}


def test_unresolved_target_page_id_never_contributes_selected_connectivity(config):
    items = [article(1), article(2)]
    expected = select_corpus(items, [], rules(config), config['quotas'], target=2)
    dirty = deepcopy(items)
    dirty[0]['extraction']['links'] = article(1, links=[2])['extraction']['links']
    dirty[0]['extraction']['links'][0]['target']['status'] = 'unresolved'
    result = select_corpus(dirty, [], rules(config), config['quotas'], target=2)
    assert result == expected
