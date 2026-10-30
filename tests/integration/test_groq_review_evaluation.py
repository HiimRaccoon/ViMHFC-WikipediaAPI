import copy

import pytest

from tests.integration.test_groq_review_packet import make_packet
from tests.integration.test_groq_review_store import semantic, audit


def test_without_real_submissions_all_rqs_are_unassessed(tmp_path):
    from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
    p = make_packet(tmp_path)
    result = evaluate_review(p, {}, {})
    assert result['status'] == 'UNASSESSED' and result['reviewed_count'] == 0
    assert result['accepted_corpus_promotion'] is False
    for scope in result['partitions'].values():
        assert scope['rq1'] is None and scope['rq2'] is None and scope['rq3'] is None


def test_primary_denominator_excludes_out_uncertainty_and_pending(tmp_path):
    from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    ids = list(p['references'])[:6]
    for rid in ids:
        p['comparisons'][rid].update(partition='evaluation_random', predicted_group='Person', model_abstain=False)
    s.save(ids[0], semantic(), expected_revision=0)
    s.save(ids[1], semantic(primary_group='Event'), expected_revision=0)
    s.save(ids[2], semantic(membership='OUT_OF_TAXONOMY', primary_group=None, evidence_sentence_ids=[]), expected_revision=0)
    s.save(ids[3], semantic(membership='INSUFFICIENT_EVIDENCE', primary_group=None, evidence_sentence_ids=[], notes='Not enough lead'), expected_revision=0)
    s.save(ids[4], semantic(membership='AMBIGUOUS', primary_group=None, evidence_sentence_ids=[], notes='Unresolved meaning'), expected_revision=0)
    result = evaluate_review(p, *s.snapshot())['partitions']['evaluation_random']
    metric = result['rq2']['groq_only']
    assert metric['denominator'] == 2 and metric['accuracy'] == .5
    assert len(metric['per_group']) == 8 and metric['per_group']['Person']['support'] == 1
    assert metric['per_group']['Person']['precision'] == .5
    assert metric['per_group']['Event']['recall'] == 0
    assert result['rq1']['groq_only']['false_inclusion_rate'] == 1
    assert result['rq1']['groq_only']['membership_precision'] == pytest.approx(2 / 3)
    assert result['rq1']['groq_only']['membership_recall'] == 1
    assert result['rq1']['groq_only']['decided_denominator'] == 3
    assert result['rq1']['groq_only']['by_membership']['AMBIGUOUS']['denominator'] == 1
    assert result['reviewed_count'] == 5


def test_abstention_is_wrong_on_in_but_not_explicit_out_recognition(tmp_path):
    from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    a, b = list(p['references'])[:2]
    for rid in (a, b):
        p['comparisons'][rid].update(partition='evaluation_random', predicted_group=None, model_abstain=True)
    s.save(a, semantic(), expected_revision=0)
    s.save(b, semantic(membership='OUT_OF_TAXONOMY', primary_group=None, evidence_sentence_ids=[]), expected_revision=0)
    metric = evaluate_review(p, *s.snapshot())['partitions']['evaluation_random']
    assert metric['rq2']['groq_only']['accuracy'] == 0
    assert metric['rq2']['groq_only']['abstention_rate'] == 1
    assert metric['rq1']['groq_only']['membership_precision'] is None
    assert metric['rq1']['groq_only']['by_membership']['OUT_OF_TAXONOMY']['abstention_rate'] == 1
    assert metric['rq1']['groq_only']['explicit_out_detection_supported'] is False


def test_rq3_counts_semantic_correct_without_claiming_historical_verification(tmp_path):
    from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    ids = list(p['references'])[:5]
    for rid in ids:
        p['comparisons'][rid].update(partition='diagnostic_targeted', predicted_group='Person', model_abstain=False,
                                     baseline_outcome='unresolved', baseline_primary=None,
                                     classification_reason='primary_dependency_missing')
    s.save(ids[0], semantic(), expected_revision=0)
    s.save_audit(ids[0], audit(), expected_revision=0, expected_semantic_revision=1)
    s.save(ids[1], semantic(primary_group='Event'), expected_revision=0)
    s.save(ids[2], semantic(membership='OUT_OF_TAXONOMY', primary_group=None, evidence_sentence_ids=[]), expected_revision=0)
    s.save(ids[3], semantic(membership='AMBIGUOUS', primary_group=None, evidence_sentence_ids=[], notes='Fixture'), expected_revision=0)
    r = evaluate_review(p, *s.snapshot())['partitions']['diagnostic_targeted']['rq3']
    assert r['semantic_correct'] == 1 and r['semantic_wrong'] == 2 and r['undecidable'] == 1
    assert r['historically_verified_suggestions'] == 0
    assert r['semantic_correct_without_sufficient_historical_verification'] == 1
    assert r['correct_additional_coverage']['numerator'] == 1
    assert r['correct_additional_coverage']['reviewed_article_denominator'] == 4
    assert r['correct_additional_coverage']['rate_over_reviewed_articles'] == .25
    assert r['by_reason']['primary_dependency_missing']['reviewed_suggestions'] == 4
    assert r['pending_suggestions'] >= 1


def test_semantic_edit_requires_new_audit_and_diagnostics_not_pooled(tmp_path):
    from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
    from vimfc.experiments.groq_classification.review_store import ReviewStore
    p = make_packet(tmp_path)
    s = ReviewStore(p, tmp_path / 'workspace')
    a, b = list(p['references'])[:2]
    p['comparisons'][a]['partition'] = 'evaluation_random'
    p['comparisons'][b]['partition'] = 'diagnostic_targeted'
    for rid in (a, b):
        s.save(rid, semantic(), expected_revision=0)
    s.save_audit(a, audit(error_tags=['precedence']), expected_revision=0, expected_semantic_revision=1)
    r = evaluate_review(p, *s.snapshot())
    assert r['partitions']['evaluation_random']['groq_error_tags']['precedence'] == 1
    s.save(a, semantic(notes='edit'), expected_revision=1)
    r = evaluate_review(p, *s.snapshot())
    assert r['partitions']['evaluation_random']['groq_error_tags'].get('precedence', 0) == 0
    assert r['partitions']['diagnostic_targeted']['reviewed_count'] == 1
    assert r['population_accuracy_claim'] is False
