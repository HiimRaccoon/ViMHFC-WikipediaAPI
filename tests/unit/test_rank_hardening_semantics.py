from dataclasses import asdict
import json
from pathlib import Path
import random

import pytest

from vimfc.domain import digest
from vimfc.pipeline.connectivity_rank import select_connected


def scenario(seed):
    rng = random.Random(seed)
    size = 12 + seed % 19
    rows = []
    for p in range(1, size + 1):
        group = rng.choice([None, 'Person', 'Location'])
        rows.append({'page_id': p, 'title': str(p),
            'quality': {'eligible': p % 11 != 0, 'score': rng.randrange(30, 101)},
            'classification': {'primary_entity_type': group, 'outcome': 'completed',
                'primary_provenance': [{'properties': ['P31']}] if p % 3 else [],
                'subtypes': [{'subtype_id': str(p % 4)}]},
            'extraction': {'links': [{'scope': 'retained_lead_prose',
                'edge_status': rng.choice(['VERIFIED_EDGE', 'CANDIDATE_EDGE']),
                'target': {'status': 'resolved', 'page_id': q}} for q in range(1, size + 1) if rng.random() < .18]}})
    pairs = [{'endpoint_a': p, 'endpoint_b': p + 1, 'kind': 'comparison', 'score': 100} for p in range(1, size, 5)]
    rules = {'version': 'rank-annotation-1' if seed % 2 else 'rank-connectivity-1',
             'connectivity': seed % 5 != 0, 'hub_normalization': seed % 7 != 0,
             'diversity': seed % 3 != 0, 'bridge_cap': 1 + seed % 5}
    return rows, pairs, rules, {'Person': 5, 'Location': 3}, [1, size // 2, size + 5][seed % 3]


ORACLE = json.loads(Path('tests/fixtures/connectivity-3c6aefa-digests.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', ORACLE['cases'], ids=lambda c: str(c['seed']))
def test_full_manifest_matches_frozen_commit_including_steps_weights_and_ties(case):
    rows, pairs, rules, quotas, target = scenario(case['seed'])
    result = select_connected(rows, pairs, rules, quotas, target)
    assert digest(asdict(result)) == case['manifest_digest']
    assert result == select_connected(list(reversed(rows)), list(reversed(pairs)), rules, quotas, target)
