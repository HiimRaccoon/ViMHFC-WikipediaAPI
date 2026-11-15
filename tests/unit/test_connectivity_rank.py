"""Connectivity utility is selected-set utility, never external-link popularity."""
from copy import deepcopy
import pytest

from vimfc.pipeline import connectivity_rank as rank


def article(page, links=(), quality=100, group=None, verified=False):
    return {'page_id': page, 'title': str(page),
            'quality': {'eligible': True, 'score': quality},
            'classification': {'primary_entity_type': group, 'subtypes': [],
                               'outcome': 'completed' if verified else 'unclassified',
                               'primary_provenance': [{'root': 'Q5'}] if verified else []},
            'extraction': {'links': [
                {'scope': 'retained_lead_prose', 'target': {'status': 'resolved', 'page_id': p}}
                for p in links]}}


def select(rows, target, rules=None, pairs=(), quotas=None):
    return rank.select_connected(rows, pairs, rules or {}, quotas or {}, target)


def test_real_distinct_triple_beats_long_isolate_and_connectivity_ablation():
    rows = [article(1, [2]), article(2, [3]), article(3), article(4, quality=300)]
    result = select(rows, 3)
    assert set(result.page_ids) == {1, 2, 3}
    assert result.steps[0]['features']['internal_edges'] == 2
    assert result.steps[0]['features']['two_hop_paths'] == 1
    assert 4 in select(rows, 3, {'connectivity': False}).page_ids


def test_external_unresolved_body_and_self_links_cannot_create_connectivity():
    popular = article(1, range(100, 150), quality=110)
    popular['extraction']['links'] += [
        {'scope': 'retained_lead_prose', 'target': {'status': 'ambiguous', 'page_id': 2}},
        {'scope': 'article_body', 'target': {'status': 'resolved', 'page_id': 2}},
        {'scope': 'retained_lead_prose', 'target': {'status': 'resolved', 'page_id': 1}}]
    result = select([popular, article(2, [3]), article(3)], 2)
    assert set(result.page_ids) == {2, 3}
    rows = rank.article_scores([popular, article(2), article(3)])
    assert next(r for r in rows if r['page_id'] == 1)['candidate_out_degree'] == 0


def test_reciprocal_cycle_is_not_distinct_three_vertex_path():
    result = select([article(1, [2]), article(2, [1])], 2)
    assert sum(s['features']['two_hop_paths'] for s in result.steps) == 0
    assert sum(s['features']['internal_edges'] for s in result.steps) == 2


def test_normalization_prefers_specific_pair_over_equally_good_hub_spokes():
    rows = [article(1, range(2, 22))] + [article(p) for p in range(2, 22)]
    rows += [article(30, [31], group='Person', verified=True), article(31)]
    assert set(select(rows, 2).page_ids) == {30, 31}
    assert set(select(rows, 2, {'hub_normalization': False}).page_ids) == {30, 31}
    # Without any verified primary tie-break the unnormalised hub pair wins by ID.
    rows[-2]['classification']['primary_provenance'] = []
    assert set(select(rows, 2, {'hub_normalization': False}).page_ids) == {1, 2}


def test_diversity_is_soft_and_null_primary_is_preserved():
    rows = [article(1, group='Person'), article(2, group='Person'),
            article(3, group='Location'), article(4)]
    original = deepcopy(rows)
    quotas = {'Person': 1, 'Location': 1}
    assert select(rows, 2, quotas=quotas).page_ids == [1, 3]
    assert select(rows, 2, {'diversity': False}, quotas=quotas).page_ids == [1, 2]
    assert len(select(rows, 4, quotas=quotas).page_ids) == 4
    assert rows == original and rows[-1]['classification']['primary_entity_type'] is None


@pytest.mark.parametrize('kind', ['comparison', 'shared_entity'])
def test_no_link_pair_fallback_does_not_fabricate_graph_edges(kind):
    rows = [article(1), article(2), article(3)]
    result = select(rows, 2, pairs=[{'kind': kind, 'endpoint_a': 2, 'endpoint_b': 3, 'score': 640}])
    assert set(result.page_ids) == {2, 3}
    assert sum(s['features']['internal_edges'] for s in result.steps) == 0


def test_determinism_cardinality_shortage_and_ineligible_pool():
    rows = [article(1, [2]), article(2, [3]), article(3), article(4)]
    rows[-1]['quality']['eligible'] = False
    before = deepcopy(rows)
    result = select(rows, 20)
    assert result == select(list(reversed(rows)), 20)
    assert set(result.page_ids) == {1, 2, 3}
    assert result.shortage == 17 and result.outcome == 'insufficient_supply'
    assert len(select(rows, 1).page_ids) == 1
    assert select(rows, 0).page_ids == []
    assert rows == before


def test_sparse_proposals_do_not_cap_selected_graph_accounting():
    rows = [article(1, [2, 3, 4]), article(2, [3, 4]), article(3, [4]), article(4)]
    result = select(rows, 4, {'bridge_cap': 1})
    assert sum(s['features']['internal_edges'] for s in result.steps) == 6
    assert sum(s['features']['two_hop_paths'] for s in result.steps) == 4


def test_article_diagnostics_are_deterministic_and_show_uncapped_degrees():
    rows = [article(1, [2, 3, 4]), article(2), article(3), article(4)]
    result = rank.article_scores(rows, {'bridge_cap': 1})
    assert result == rank.article_scores(reversed(rows), {'bridge_cap': 1})
    assert next(r for r in result if r['page_id'] == 1)['candidate_out_degree'] == 3


def test_public_selection_dispatches_new_version():
    from vimfc.pipeline.select import select_corpus
    rows = [article(1, [2]), article(2), article(3, quality=300)]
    assert set(select_corpus(rows, [], {'version': 'rank-connectivity-1'}, {}, 2).page_ids) == {1, 2}


def test_instance_evidence_breaks_ties_without_excluding_concepts():
    concept, instance = article(1, group='Person', verified=True), article(2, group='Person', verified=True)
    concept['classification']['primary_provenance'] = [{'properties': ['P279']}]
    instance['classification']['primary_provenance'] = [{'properties': ['P31', 'P279']}]
    assert select([concept, instance], 1).page_ids == [2]
    assert set(select([concept, instance], 2).page_ids) == {1, 2}
