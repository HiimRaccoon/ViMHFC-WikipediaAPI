"""Graph completion reports keep admission frozen and graph scopes separate."""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.helpers import article
from vimfc.domain import Incompatible, digest


def relaxed_fixture():
    rows = [article(1, links=[2, 99]), article(2, links=[3]), article(3)]
    for row in rows:
        row['source_validation'] = {'manifest_id': str(row['page_id']) * 64}
        for link in row['extraction']['links']:
            link['target'] = {'status': 'unresolved', 'reason': 'no_archived_identity_resolution'}
    return {'snapshot': rows[0]['snapshot'], 'cohort_hash': digest([1, 2, 3]),
            'cohort_page_ids': [1, 2, 3], 'cohort': [{'page_id': p, 'title': f'Bài {p}'} for p in [1, 2, 3]],
            'source_run_id': 'fixture', 'source_config_hash': 'a' * 64,
            'records': rows, 'blockers': [], 'status': 'COMPLETE',
            'baseline_metrics': {'eligible': 1, 'eligible_edges': 7},
            'metrics': {'cohort_size': 3, 'processed': 3, 'eligible': 3, 'eligible_edges': 0},
            'quality_policy': {'quality_version': 'quality-relaxed-1'},
            'ranking_policy': {'version': 'rank-hyperlink-1', 'bridge_cap': 20,
                               'comparison_cap': 20, 'comparison_weights': {}, 'bridge_weights': {}, 'stopwords': []},
            'quotas': {'Person': 20000}, 'graph': {}}


def decisions():
    result = {}
    for page in (2, 3, 99):
        result[f'Bài {page}'] = {'state': 'RESOLVED_OUTSIDE_COHORT' if page == 99 else 'RESOLVED_VALID',
                               'target': {'status': 'resolved', 'page_id': page, 'snapshot': '2026-10-14T00:00:00Z',
                                          'revision_id': page + 1000, 'raw_hash': 'a' * 64}}
    return result


def test_completed_graph_counts_internal_paths_and_keeps_external_targets_outside():
    from vimfc.pipeline.graph_pilot import build_graph_report
    before = relaxed_fixture(); original = deepcopy(before)
    report = build_graph_report(before, decisions(), diagnostic_target=2)
    assert report['metrics']['eligible'] == 3
    assert report['graph']['both_endpoints_relaxed_eligible']['directed_nonself_edges'] == 2
    assert report['graph']['both_endpoints_relaxed_eligible']['nonisolated_vertices'] == 3
    assert report['graph']['both_endpoints_relaxed_eligible']['potential_two_hop_paths'] == 1
    assert report['graph']['both_endpoints_relaxed_eligible']['outside_pool_link_occurrences'] == 1
    assert report['comparisons']['original_pilot']['eligible_edges'] == 7
    assert report['comparisons']['relaxed_pilot']['eligible_edges'] == 0
    assert report['metrics']['eligible_edges'] == 2
    assert report['eligibility_unchanged'] is True and before == original
    assert all(len(r['selection']['page_ids']) == 2 for r in report['ablations']['reduced_capacity'].values())


def test_blocked_ambiguous_or_absent_targets_never_reuse_old_target_ids():
    from vimfc.pipeline.graph_pilot import build_graph_report
    base = relaxed_fixture()
    for row in base['records']:
        for link in row['extraction']['links']:
            link['target'] = {'status': 'resolved', 'page_id': 2}
    resolved = decisions()
    resolved['Bài 2'] = {'state': 'AMBIGUOUS', 'target': {'status': 'resolved', 'page_id': 2}}
    resolved['Bài 3'] = {'state': 'SOURCE_INTEGRITY_BLOCKED', 'target': None}
    report = build_graph_report(base, resolved, diagnostic_target=2)
    assert report['metrics']['eligible_edges'] == 0
    assert report['target_states']['AMBIGUOUS'] == 1
    assert report['target_states']['SOURCE_INTEGRITY_BLOCKED'] == 1
    assert report['status'] == 'PARTIAL'


def test_prior_release_verification_rejects_changed_package_or_different_cohort(tmp_path):
    from vimfc.pipeline.graph_pilot import read_prior_release
    from vimfc.pipeline.relaxed_pilot import write_report
    base = relaxed_fixture()
    base.update(format='vimfc-relaxed-pilot-1', edge_witnesses=[], gained_examples=[], rejected_examples=[])
    root = tmp_path / 'prior'; write_report(base, root)
    loaded = read_prior_release(root, base)
    assert loaded['metrics']['eligible'] == 3
    other = {**base, 'cohort_hash': 'b' * 64}
    with pytest.raises(Incompatible): read_prior_release(root, other)
    (root / 'articles.json').write_text('[]', encoding='utf-8')
    with pytest.raises(Incompatible): read_prior_release(root, base)


def test_graph_report_manifest_covers_decisions_witnesses_and_all_report_files(tmp_path):
    from vimfc.pipeline.graph_pilot import build_graph_report, write_graph_report
    report = build_graph_report(relaxed_fixture(), decisions(), diagnostic_target=2)
    manifest = write_graph_report(report, tmp_path / 'report')
    assert {'metrics.json', 'articles.json', 'target-decisions.json', 'edge-witnesses.json', 'comparison.md'} <= set(manifest['files'])
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((tmp_path / 'report' / name).read_bytes()).hexdigest() == expected
    assert json.loads((tmp_path / 'report' / 'manifest.json').read_text(encoding='utf-8')) == manifest


def test_report_attributes_new_lost_edges_coverage_and_connected_membership():
    from vimfc.pipeline.graph_pilot import build_graph_report
    base = relaxed_fixture()
    prior = {'metrics': {'eligible_edges': 1}, 'baseline_metrics': {'eligible_edges': 7},
             'graph': {'both_endpoints_relaxed_eligible': {'edges': [[3, 1]]}},
             'target_coverage': {'unique_titles': 3, 'resolved_occurrences': 1}}
    report = build_graph_report(base, decisions(), prior_relaxed=prior, diagnostic_target=2)
    assert report['graph_change']['new_directed_edges'] == [[1, 2], [2, 3]]
    assert report['graph_change']['lost_directed_edges'] == [[3, 1]]
    assert report['graph_change']['previous_verified_edges_preserved'] is False
    assert report['status'] == 'PARTIAL'
    assert report['target_coverage']['after']['resolved_occurrences'] == 3
    assert report['target_coverage']['after']['outside_cohort_occurrences'] == 1
    assert report['target_coverage']['after']['unique_titles'] == 3
    assert report['graph']['connected_clusters'][0]['page_ids'] == [1, 2, 3]


def test_missing_new_proof_never_restores_an_older_target_with_weaker_validation():
    from vimfc.pipeline.graph_pilot import build_graph_report
    base = relaxed_fixture()
    target = {**decisions()['Bài 2']['target'], 'title': 'Bài 2',
              'revision_timestamp': '2026-10-13T00:00:00Z',
              'proof': {'kind': 'historical_revision_and_move_log'}}
    base['records'][0]['extraction']['links'][0]['target'] = target
    resolved = decisions()
    resolved['Bài 2'] = {'state': 'UNRESOLVED', 'target': None, 'reason': 'graph_proof_missing'}
    report = build_graph_report(base, resolved, diagnostic_target=2)
    assert [1, 2] not in report['graph']['both_endpoints_relaxed_eligible']['edges']
    assert report['graph_change']['lost_directed_edges'] == [[1, 2]]
    assert report['target_decisions']['Bài 2']['state'] == 'UNRESOLVED'


def test_unresolved_source_extraction_never_publishes_edge_witnesses():
    from vimfc.pipeline.graph_pilot import build_graph_report
    base = relaxed_fixture()
    base['records'][0]['extraction']['outcome'] = 'unresolved'
    base['records'][0]['quality']['eligible'] = False
    report = build_graph_report(base, decisions(), diagnostic_target=2)
    assert all(w['source_page_id'] != 1 for w in report['edge_witnesses'])
    assert report['target_link_occurrences'] == 1


def test_recovery_orchestration_is_cache_first_and_preserves_source(tmp_path, config):
    import httpx
    from tests.integration.test_discovery_archive import fixture_archive
    from vimfc.pipeline.graph_pilot import evaluate_graph_archive
    ctx = fixture_archive(tmp_path / 'source', config)
    before = hashlib.sha256(__import__('pathlib').Path(ctx['database']).read_bytes()).hexdigest()
    report = evaluate_graph_archive(ctx, tmp_path / 'owned', expected_count=1,
        transport=httpx.MockTransport(lambda req: pytest.fail('Cache-only Pilot cannot request HTTP')),
        sleep=lambda _: None)
    assert report['cohort_page_ids'] == [1] and report['metrics']['eligible'] == 1
    assert report['recovery']['physical_attempts'] == 0
    assert report['metrics']['eligible_edges'] == 0
    assert hashlib.sha256(__import__('pathlib').Path(ctx['database']).read_bytes()).hexdigest() == before


def test_rate_limit_stops_online_work_and_offline_resume_retains_the_request_charge(tmp_path, config):
    import httpx
    from tests.integration.test_discovery_archive import fixture_archive
    from vimfc.pipeline.graph_pilot import evaluate_graph_archive
    ctx = fixture_archive(tmp_path / 'source', config)
    calls = []
    def wire(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '60'}, json={'error': {'code': 'ratelimited'}})
    kwargs = {'expected_count': 1, 'transport': httpx.MockTransport(wire), 'sleep': lambda _: None}
    first = evaluate_graph_archive(ctx, tmp_path / 'owned', allow_live=True, **kwargs)
    assert len(calls) == 1 and first['recovery']['online_stopped'] is True
    assert first['recovery']['stop_reason'] == 'http_429'
    resumed = evaluate_graph_archive(ctx, tmp_path / 'owned', allow_live=False, **kwargs)
    assert len(calls) == 1 and resumed['recovery']['physical_attempts'] == 1
    assert resumed['metrics']['eligible'] == first['metrics']['eligible'] == 1
