"""Synthetic controls only; never human labels for the real review packet."""
import copy
import json

import pytest

from vimfc.domain import GROUPS, digest


def review_source():
    rows, results = [], []
    for i in range(180):
        outcome = ('completed', 'unclassified', 'unresolved')[i % 3]
        group = GROUPS[(i // 3) % 8]
        inp = {'title': f'Bài {i}', 'lead_text': 'Một chủ thể lịch sử.',
               'sentences': [{'id': 0, 'text': 'Một chủ thể lịch sử.'}]}
        row = {'input': inp, 'input_fingerprint': digest(inp),
               'evaluation': {'page_id': i + 1, 'revision_id': i + 1000,
                              'snapshot': '2026-10-25T03:00:00Z', 'baseline_outcome': outcome,
                              'baseline_primary': group if outcome == 'completed' else None,
                              'classification_reason': None if outcome == 'completed' else
                              'no_supported_rule' if outcome == 'unclassified' else
                              ('primary_ambiguity' if i % 2 else 'primary_dependency_missing')},
               'provenance': {'revision_timestamp': '2026-10-01T00:00:00Z'}}
        abstain = outcome != 'completed' and i % 5 == 0
        prediction = {'predicted_group': None if abstain else group, 'abstain': abstain,
                      'evidence_sentence_ids': [] if abstain else [0], 'reason': 'Fixture.', 'ambiguity': None}
        rows.append(row)
        results.append({'page_id': i + 1, 'input_fingerprint': row['input_fingerprint'],
                        'model_id': 'qwen/qwen3.8-27b', 'mode': 'blind', 'status': 'valid',
                        'prediction': prediction, 'response_content': json.dumps(prediction),
                        'request_key': str(i), 'response_fingerprint': digest(prediction)})
    # An actual reference disagreement, distinct from an error adjudication.
    rows[0]['evaluation']['baseline_primary'] = 'Event'
    return rows, results


def make_packet(tmp_path):
    from vimfc.experiments.groq_classification.review_packet import create_review_packet, load_review_packet
    rows, results = review_source()
    root = tmp_path / 'packet'
    config = {'version': 'qid-5', 'precedence': list(GROUPS),
              'roots': {'Q5': {'name': 'Human', 'group': 'Person', 'subtype': False}}}
    manifest = create_review_packet(rows, results, root, classification=config,
                                    required_ids=[1, 2, 3, 4], source={'kind': 'synthetic-test-only'})
    return load_review_packet(root, manifest['manifest_id'])


def test_random_frame_covers_all_cells_with_exact_inclusion_probabilities():
    from vimfc.experiments.groq_classification.review_sampling import select_review_sample
    rows, results = review_source()
    s = select_review_sample(rows, results, seed=20261031, required_ids=[1, 2, 3, 4])
    assert s == select_review_sample(rows[::-1], results[::-1], seed=20261031, required_ids=[4, 3, 2, 1])
    assert len(s['members']) == 90
    assert len({m['page_id'] for m in s['members']}) == 90
    assert {1, 2, 3, 4} <= {m['page_id'] for m in s['members']}
    random = [m for m in s['members'] if m['partition'] == 'evaluation_random']
    assert len(random) == 60
    assert all(f['sample_count'] >= 1 for f in s['frames'])
    for f in s['frames']:
        drawn = [m for m in random if m['stratum'] == f['stratum']]
        assert len(drawn) == f['sample_count']
        assert all(m['inclusion_probability'] == f['sample_count'] / f['population_count'] for m in drawn)
    assert all(m['inclusion_probability'] is None for m in s['members'] if m['partition'] == 'diagnostic_targeted')


def test_packet_is_blind_bound_to_inputs_and_no_human_labels(tmp_path):
    packet = make_packet(tmp_path)
    assert len(packet['references']) == 90
    for r in packet['references'].values():
        assert set(r) == {'review_id', 'page_id', 'revision_id', 'revision_timestamp', 'snapshot',
                          'input_fingerprint', 'title', 'lead_text', 'sentences'}
        assert digest({k: r[k] for k in ('title', 'lead_text', 'sentences')}) == r['input_fingerprint']
    taxonomy = packet['taxonomy']
    assert taxonomy['classification']['roots']['Q5']['name'] == 'Human'
    assert taxonomy['classification']['precedence'] == list(GROUPS)
    assert packet['manifest']['human_labels_created'] == 0


def test_packet_tampering_and_source_output_overlap_are_rejected(tmp_path):
    from vimfc.experiments.groq_classification.review_packet import create_review_packet, load_review_packet
    p = make_packet(tmp_path)
    root = tmp_path / 'packet'
    with pytest.raises(ValueError):
        load_review_packet(root, 'wrong-manifest')
    (root / 'blind/reference.jsonl').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_review_packet(root, p['manifest']['manifest_id'])
    rows, results = review_source()
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError):
        create_review_packet(rows, results, root / 'nested', classification=p['taxonomy']['classification'],
                             source={'immutable_source_paths': [str(root)]})
    assert list(tmp_path.iterdir()) == before


def test_missing_required_response_is_not_regenerated():
    from vimfc.experiments.groq_classification.review_sampling import select_review_sample
    rows, results = review_source()
    with pytest.raises(ValueError):
        select_review_sample(rows, results[:-1], required_ids=[180])
