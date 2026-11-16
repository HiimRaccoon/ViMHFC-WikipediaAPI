from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from vimfc.domain import Incompatible, SourceUnavailable
from vimfc.pipeline.wikigraph import build_graph, source_links, select_structural, structural_metrics, archived_graph

SNAP = '2026-10-25T03:00:00Z'


def page(number, title, text='', namespace=0):
    return {'page_id': number, 'title': title, 'snapshot': SNAP, 'revision_id': 100 + number,
            'raw_hash': 'checked', 'source_validation': {'manifest_id': 'checked'},
            'namespace': namespace, 'structural_source': source_links(text),
            'quality': {'eligible': True, 'score': 60},
            'classification': {'primary_entity_type': None, 'subtypes': []},
            'extraction': {'links': [], 'sentences': [{'text': 'original sentence'}]}}


@pytest.mark.parametrize('link,expected,reason', [
    ('B', [[1, 2]], None), ('b#Section', [[1, 2]], None),
    (':B', [[1, 2]], None), ('Alias', [[1, 2]], None),
    ('Chain', [[1, 2]], None), ('Loop', [], 'redirect_cycle'),
    ('Missing', [], 'target_not_in_frozen_cache'),
    ('File:X', [], 'nonzero_namespace'), ('Unknown:X', [], 'namespace_unverified'),
    ('A', [], 'self_link'), ('#Section', [], 'self_link'),
    ('SelfAlias', [], 'self_link'), ('123', [[1, 9]], None),
    ('two_words', [[1, 10]], None),
])
def test_deterministic_resolution(link, expected, reason):
    pages = [page(1, 'A', f'[[{link}]]'), page(2, 'B'), page(3, 'Alias', '#REDIRECT [[B#x]]'),
             page(4, 'Chain', '#REDIRECT [[Alias]]'), page(5, 'Loop', '#REDIRECT [[Other]]'),
             page(6, 'Other', '#REDIRECT [[Loop]]'), page(7, 'File:X', namespace=6),
             page(8, 'SelfAlias', '#REDIRECT [[A]]'), page(9, '123'), page(10, 'Two words')]
    graph = build_graph(pages, SNAP)
    assert [edge for edge in graph['edges'] if edge[0] == 1] == expected
    occurrence = graph['occurrences'][0]
    assert occurrence['reason'] == reason
    assert occurrence['state'] == ('UNRESOLVED' if reason else 'VERIFIED_STRUCTURAL_EDGE')
    assert graph == build_graph(list(reversed(pages)), SNAP)
    assert graph['structural_edges_are_factual_evidence'] is False


def test_body_links_deduplication_and_distinct_two_hop_paths():
    pages = [page(1, 'A', 'Lead.\n== Body ==\n[[B]] [[B]]'),
             page(2, 'B', '[[A]] [[C]]'), page(3, 'C', '[[B]]'), page(4, 'Isolate')]
    graph = build_graph(pages, SNAP)
    metrics = structural_metrics(graph, graph['vertices'])
    assert metrics['directed_nonself_edges'] == 4
    assert metrics['potential_two_hop_paths'] == 2
    assert metrics['two_hop_examples'] == [[1, 2, 3], [3, 2, 1]]
    assert metrics['weak_component_sizes'] == [3, 1]


def test_quality_filter_then_connectivity_selection_preserves_evidence_and_metadata():
    pages = [page(1, 'A', '[[B]]'), page(2, 'B', '[[C]]'), page(3, 'C'), page(4, 'D')]
    pages[3]['quality']['score'] = 100
    pages.append(page(5, 'Excluded', '[[A]]'))
    pages[4]['quality']['eligible'] = False
    original = deepcopy(pages)
    graph = build_graph(pages, SNAP)
    result = select_structural(pages, SimpleNamespace(context={'snapshot': SNAP}), {}, 3, graph)
    assert set(result['selection'].page_ids) == {1, 2, 3}
    assert result['articles'] == pages == original
    assert result['manifest'] == select_structural(list(reversed(pages)),
        SimpleNamespace(context={'snapshot': SNAP}), {}, 3, graph)['manifest']


def test_wrong_snapshot_and_ambiguous_title_are_not_verified():
    pages = [page(1, 'A', '[[B]]'), page(2, 'B'), page(3, 'B')]
    assert build_graph(pages, SNAP)['unresolved_reasons'] == {'ambiguous_target': 1}
    pages[0]['snapshot'] = 'different'
    with pytest.raises(Incompatible):
        build_graph(pages, SNAP)


def test_selection_rejects_tampered_graph():
    pages = [page(1, 'A'), page(2, 'B')]
    graph = build_graph(pages, SNAP)
    graph['edges'].append([1, 2])
    with pytest.raises(Incompatible, match='fingerprint'):
        select_structural(pages, SimpleNamespace(context={'snapshot': SNAP}), {}, 2, graph)


def test_archive_loads_targets_before_filtering_and_keeps_missing_raw_unresolved():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE snapshot_revision_refs (site, snapshot, identity, record_json)')
        for number, title in [(2, 'B'), (3, 'Star:Name'), (4, 'Missing')]:
            ref = {'page_id': number, 'title': title, 'revision_id': 100 + number,
                   'revision_timestamp': SNAP, 'lookup_timestamp': SNAP}
            db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', SNAP, str(number), json.dumps(ref)))
        def raw(number, title, **identity):
            assert identity == {'revision_id': 100 + number, 'timestamp': SNAP}
            if number == 4:
                raise SourceUnavailable('Missing raw', code='graph_proof_missing')
            return {'content': '[[Star:Name]]', 'local_sha256': 'checked'}, {}, []
        archive = SimpleNamespace(db=db, context={'snapshot': SNAP}, _moves=lambda _: [], _identity_raw=raw,
            article_source=lambda _: ({'content': '[[A]]', 'local_sha256': 'checked'},
                                      {'namespace_scope': {'namespace': 0}}))
        a = page(1, 'A', '[[B]] [[Missing]]')
        a['source_validation']['namespace_scope'] = {'namespace': 0}
        graph = archived_graph(archive, [a])
    assert graph['edges'] == [[1, 2], [2, 3], [3, 1]]
    assert graph['unavailable_sources'] == {'4': 'graph_proof_missing'}
    assert graph['unresolved_reasons'] == {'target_not_in_frozen_cache': 1}
