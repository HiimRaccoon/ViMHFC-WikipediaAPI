from copy import deepcopy
from types import SimpleNamespace

import pytest

from vimfc.domain import Incompatible, SourceUnavailable
from vimfc.pipeline.annotation_edges import classify_links, edge_report, select_articles
from vimfc.pipeline.select import select_corpus

SNAP = '2026-10-25T03:00:00Z'


def article(page, links=(), score=60):
    return {'page_id': page, 'title': str(page), 'snapshot': SNAP,
            'revision_id': 500 + page, 'raw_hash': 'source-raw',
            'source_validation': {'manifest_id': 'checked-source'},
            'quality': {'eligible': True, 'score': score, 'quality_version': 'quality-relaxed-1'},
            'classification': {'primary_entity_type': None, 'subtypes': []},
            'extraction': {'links': [{'scope': 'retained_lead_prose', 'target_title': title}
                                     for title in links]}}


def target(page, **changes):
    return {'status': 'resolved', 'page_id': page, 'revision_id': 100 + page,
            'raw_hash': 'checked-raw', 'snapshot': SNAP,
            'proof': {'kind': 'historical_revision_and_move_log'},
            'chain': [{'original_title': 'Alias', 'target': 'Final', 'page_id': page}]
            if page == 2 else [], **changes}


def test_checked_redirect_is_candidate_and_not_verified_without_v2_authority():
    archive = SimpleNamespace(context={'snapshot': SNAP}, resolve=lambda title: target(2))
    original = article(1, ['Alias'])
    records, counts = classify_links([original, article(2)], archive)
    link = records[0]['extraction']['links'][0]
    assert link['edge_status'] == 'CANDIDATE_EDGE'
    assert link['target']['chain'][0]['target'] == 'Final'
    assert counts['VERIFIED_EDGE'] == 0 and counts['CANDIDATE_EDGE'] == 1
    assert 'target' not in original['extraction']['links'][0]


def test_numeric_title_unresolved_and_wrong_snapshot_blocked():
    archive = SimpleNamespace(context={'snapshot': SNAP}, resolve=lambda title: target(2, snapshot='2026-11-01T00:00:00Z'))
    records, counts = classify_links([article(1, ['123', 'Alias'])], archive)
    assert [link['edge_status'] for link in records[0]['extraction']['links']] == ['UNRESOLVED', 'BLOCKED_EDGE']
    assert counts['UNRESOLVED'] == counts['BLOCKED_EDGE'] == 1
    assert all(link['target']['status'] == 'unresolved' for link in records[0]['extraction']['links'])


def test_missing_source_manifest_cannot_enter_collection():
    row = article(1, ['Alias']); row['source_validation'] = {}
    with pytest.raises(Incompatible):
        classify_links([row], SimpleNamespace(context={'snapshot': SNAP}, resolve=lambda _: target(2)))


def test_candidate_internal_edge_beats_high_quality_isolate_and_metrics_stay_separate():
    archive = SimpleNamespace(context={'snapshot': SNAP}, resolve=lambda title: target({'B': 2, 'C': 3}[title]))
    rows, _ = classify_links([article(1, ['B']), article(2, ['C']), article(3), article(4, score=100)], archive)
    result = select_corpus(rows, [], {'version': 'rank-annotation-1'}, {}, 3)
    assert set(result.page_ids) == {1, 2, 3}
    assert result.steps[0]['features']['two_hop_paths'] == 1
    metrics = edge_report(rows)
    assert metrics['internal_directed_edges'] == {'VERIFIED_EDGE': 0, 'CANDIDATE_EDGE': 2}
    assert metrics['link_occurrences']['UNRESOLVED'] == 0


def test_verified_rank_weight_exceeds_candidate_without_changing_old_policy():
    rows = [article(1, ['B']), article(2), article(3, ['D']), article(4)]
    for row, page, state in ((rows[0], 2, 'CANDIDATE_EDGE'), (rows[2], 4, 'VERIFIED_EDGE')):
        row['extraction']['links'][0].update(edge_status=state, edge_policy='annotation-edge-1', target=target(page))
    chosen = select_corpus(rows, [], {'version': 'rank-annotation-1'}, {}, 2)
    assert set(chosen.page_ids) == {3, 4}
    assert chosen.steps[0]['features']['internal_edges'] == 1
    assert edge_report(rows)['internal_directed_edges'] == {'VERIFIED_EDGE': 1, 'CANDIDATE_EDGE': 1}


def test_unresolved_and_blocked_links_never_create_rank_edges():
    rows = [article(1, ['B']), article(2), article(3, score=100)]
    rows[0]['extraction']['links'][0].update(edge_status='BLOCKED_EDGE', edge_policy='annotation-edge-1', target=target(2))
    before = deepcopy(rows)
    chosen = select_corpus(rows, [], {'version': 'rank-annotation-1'}, {}, 2)
    assert set(chosen.page_ids) == {1, 3}
    assert rows == before


def test_missing_title_proof_stays_unresolved_but_integrity_failure_blocks():
    def resolve(title):
        raise SourceUnavailable('unavailable', code='graph_proof_missing' if title == 'Missing' else 'source_integrity_blocked')
    rows, counts = classify_links([article(1, ['Missing', 'Corrupt'])],
                                  SimpleNamespace(context={'snapshot': SNAP}, resolve=resolve))
    assert [link['edge_status'] for link in rows[0]['extraction']['links']] == ['UNRESOLVED', 'BLOCKED_EDGE']
    assert counts['CANDIDATE_EDGE'] == 0


def test_collection_entrypoint_uses_entire_supplied_pool_and_versions_result():
    archive = SimpleNamespace(context={'snapshot': SNAP}, resolve=lambda _: target(2))
    pool = [article(1, ['B']), article(2), article(3)]
    result = select_articles(pool, archive, {}, target=3)
    assert result['selection'].page_ids and len(result['selection'].page_ids) == 3
    assert result['edges']['internal_directed_edges']['CANDIDATE_EDGE'] == 1
    assert result['ranking_policy'] == 'rank-annotation-1'
    assert result['manifest']['selected_page_ids'] == result['selection'].page_ids
    assert result['manifest']['input_digest'] == select_articles(list(reversed(pool)), archive, {}, target=3)['manifest']['input_digest']
    with pytest.raises(Incompatible):
        select_articles(pool + [pool[0]], archive, {}, target=3)
