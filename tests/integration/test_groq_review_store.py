import copy
import json

import pytest

from tests.integration.test_groq_review_packet import make_packet


def semantic(**updates):
    return {'membership': 'IN_TAXONOMY', 'primary_group': 'Person',
            'evidence_sentence_ids': [0], 'confidence': 'high', 'notes': '',
            'reviewer': 'synthetic-test-reviewer', 'review_timestamp': '2026-10-31T09:00:00+07:00', **updates}


@pytest.mark.parametrize('membership', ['OUT_OF_TAXONOMY', 'INSUFFICIENT_EVIDENCE', 'AMBIGUOUS'])
def test_non_in_memberships_remain_distinct_and_forbid_primary(tmp_path, membership):
    from vimfc.experiments.groq_classification.review_schema import validate_semantic
    packet = make_packet(tmp_path)
    ref = next(iter(packet['references'].values()))
    value = semantic(membership=membership, primary_group=None, evidence_sentence_ids=[], notes='Fixture uncertainty')
    assert validate_semantic(value, ref)['membership'] == membership
    with pytest.raises(ValueError):
        validate_semantic({**value, 'primary_group': 'Person'}, ref)


@pytest.mark.parametrize('change', [
    {'primary_group': None}, {'primary_group': 'Other'}, {'evidence_sentence_ids': []},
    {'evidence_sentence_ids': [False]}, {'evidence_sentence_ids': [0, 0]}, {'evidence_sentence_ids': [99]},
    {'reviewer': ''}, {'review_timestamp': '2026-10-31'}, {'review_timestamp': 'invalid'},
    {'confidence': 'certain'}, {'extra': 'unexpected'},
    {'membership': 'AMBIGUOUS', 'primary_group': None, 'notes': ''},
])
def test_invalid_submissions_do_not_become_gold(tmp_path, change):
    from vimfc.experiments.groq_classification.review_schema import validate_semantic
    ref = next(iter(make_packet(tmp_path)['references'].values()))
    with pytest.raises(ValueError):
        validate_semantic(semantic(**change), ref)


def test_persistent_history_stale_save_and_comparison_exposure(tmp_path):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    workspace = tmp_path / 'workspace'
    s = ReviewStore(p, workspace)
    rid = next(iter(p['references']))
    s.save(rid, semantic(), expected_revision=0)
    assert s.current(rid)['revision'] == 1
    with pytest.raises(ValueError, match='Stale'):
        s.save(rid, semantic(notes='lost update'), expected_revision=0)
    s.mark_exposed(rid)
    s.save(rid, semantic(notes='manual correction'), expected_revision=1)
    reopened = ReviewStore(p, workspace)
    assert reopened.current(rid)['payload']['notes'] == 'manual correction'
    history = reopened.history(rid)
    assert len(history) == 2 and history[0]['comparison_exposed'] is False
    assert history[1]['comparison_exposed'] is True
    assert history[1]['previous_hash'] == history[0]['record_hash']
    assert reopened.progress()['reviewed'] == 1
    assert reopened.progress()['total'] == 90
    assert not any('human' in r for r in p['references'].values())


@pytest.mark.parametrize('fmt', ['csv', 'jsonl'])
def test_export_import_roundtrip_and_atomic_validation(tmp_path, fmt):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    a = ReviewStore(p, tmp_path / 'a')
    rid = next(iter(p['references']))
    a.save(rid, semantic(notes="'=literal\n=still a note", reviewer='=fixture'), expected_revision=0)
    data = a.export(fmt)
    b = ReviewStore(p, tmp_path / 'b')
    assert b.import_labels(data, fmt) == 1
    assert b.current(rid)['payload'] == a.current(rid)['payload']
    assert b.history(rid)[0]['source'].startswith('import:')
    assert b.progress()['reviewed'] == 1
    if fmt == 'jsonl':
        lines = data.decode('utf-8').splitlines()
        rows = [json.loads(line) for line in lines]
        reviewed = next(r for r in rows if r['review_status'] == 'reviewed')
        pending = next(r for r in rows if r['review_status'] == 'pending')
        invalid = {**pending, 'review_status': 'reviewed', **semantic(evidence_sentence_ids=[999])}
        c = ReviewStore(p, tmp_path / 'c')
        with pytest.raises(ValueError):
            c.import_labels(('\n'.join(map(json.dumps, [reviewed, invalid])) + '\n').encode(), 'jsonl')
        assert c.progress()['reviewed'] == 0 and c.history(rid) == []
        for field, value in [('input_fingerprint', 'changed'), ('page_id', False), ('manifest_id', 'foreign')]:
            with pytest.raises(ValueError):
                c.import_labels((json.dumps({**reviewed, field: value}) + '\n').encode(), 'jsonl')
        with pytest.raises(ValueError):
            c.import_labels((json.dumps(reviewed) + '\n' + json.dumps(reviewed)).encode(), 'jsonl')


def test_workspace_cannot_overlap_packet_sources_or_unowned_directory(tmp_path):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    for path in (p['root'], p['root'] / 'runtime'):
        with pytest.raises(ValueError):
            ReviewStore(p, path)
    unsafe = tmp_path / 'existing'
    unsafe.mkdir()
    (unsafe / 'source.txt').write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError):
        ReviewStore(p, unsafe)
    assert (unsafe / 'source.txt').read_text(encoding='utf-8') == 'preserve'


def audit(**updates):
    return {'verification': 'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE', 'verified_group': None,
            'evidence_refs': [], 'error_tags': [], 'notes': 'No sufficient policy evidence',
            'reviewer': 'synthetic-auditor', 'review_timestamp': '2026-10-31T09:10:00+07:00', **updates}


def test_audit_is_separate_and_cannot_promote_semantic_label(tmp_path):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    rid = next(iter(p['references']))
    with pytest.raises(ValueError):
        s.save_audit(rid, audit(), expected_revision=0, expected_semantic_revision=0)
    s.save(rid, semantic(), expected_revision=0)
    with pytest.raises(ValueError):
        s.save_audit(rid, audit(verification='VERIFIED', verified_group='Person'), expected_revision=0, expected_semantic_revision=1)
    s.save_audit(rid, audit(), expected_revision=0, expected_semantic_revision=1)
    assert s.current(rid)['payload']['primary_group'] == 'Person'
    assert s.current(rid, kind='audit')['payload']['verification'] == 'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE'
    s.save(rid, semantic(primary_group='Event'), expected_revision=1)
    assert s.current(rid, kind='audit')['semantic_revision'] == 1
    assert s.progress()['audited'] == 0  # semantic edit invalidates current audit for metrics


def test_audit_verified_group_requires_exact_historical_binding(tmp_path):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    rid = next(iter(p['references']))
    p['evidence'][rid].update(availability='COMPLETE', verified_primary='Person', evidence_refs=['manifest:fixture'])
    s = ReviewStore(p, tmp_path / 'workspace')
    s.save(rid, semantic(), expected_revision=0)
    with pytest.raises(ValueError):
        s.save_audit(rid, audit(verification='VERIFIED', verified_group='Event', evidence_refs=['manifest:fixture']), expected_revision=0, expected_semantic_revision=1)
    with pytest.raises(ValueError):
        s.save_audit(rid, audit(verification='VERIFIED', verified_group='Person', evidence_refs=['made-up']), expected_revision=0, expected_semantic_revision=1)
    s.save_audit(rid, audit(verification='VERIFIED', verified_group='Person', evidence_refs=['manifest:fixture']), expected_revision=0, expected_semantic_revision=1)
    assert s.progress()['audited'] == 1


def test_stale_export_cannot_replace_newer_labels_and_stale_audit_is_rejected(tmp_path):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    rid = next(iter(p['references']))
    s.save(rid, semantic(), expected_revision=0)
    old = s.export('jsonl')
    s.save(rid, semantic(primary_group='Event'), expected_revision=1)
    with pytest.raises(ValueError, match='Stale'):
        s.import_labels(old, 'jsonl')
    assert s.current(rid)['revision'] == 2
    with pytest.raises(ValueError, match='Stale'):
        s.save_audit(rid, audit(), expected_revision=0, expected_semantic_revision=1)
    assert s.current(rid, 'audit') is None


@pytest.mark.parametrize('fmt', ['csv', 'jsonl'])
def test_comparison_exposure_survives_transfer_to_another_workspace(tmp_path, fmt):
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    a, b = ReviewStore(p, tmp_path / 'a'), ReviewStore(p, tmp_path / 'b')
    rid = next(iter(p['references']))
    a.save(rid, semantic(), expected_revision=0)
    a.mark_exposed(rid)
    b.import_labels(a.export(fmt), fmt)
    assert b.was_exposed(rid) is True
    assert b.history(rid)[0]['comparison_exposed'] is True
    b.save(rid, semantic(notes='manual edit'), expected_revision=1)
    assert b.history(rid)[1]['comparison_exposed'] is True
