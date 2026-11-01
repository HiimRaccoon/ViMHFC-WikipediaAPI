import copy
import json
import pytest

from vimfc.domain import digest


def row(verified=False):
    inp = {'title': 'Viện Sao Mai', 'lead_text': 'Viện Sao Mai là một tổ chức nghiên cứu. Viện được thành lập năm 2000.',
           'sentences': [{'id': 0, 'text': 'Viện Sao Mai là một tổ chức nghiên cứu.'},
                         {'id': 1, 'text': 'Viện được thành lập năm 2000.'}]}
    return {'input': inp, 'input_fingerprint': digest(inp), 'trusted_lead': True,
            'evaluation': {'page_id': 17, 'revision_id': 101, 'snapshot': '2026-10-25T03:00:00Z',
                           'baseline_outcome': 'completed' if verified else 'unclassified',
                           'baseline_primary': 'Organization' if verified else None, 'eligible': verified,
                           'classification_reason': None if verified else 'no_supported_rule'},
            'quality_gate': {'extraction_pass': True, 'nonclassification_quality_pass': True}}


def response(**updates):
    return {'membership': 'IN_TAXONOMY', 'predicted_group': 'Organization', 'abstain': False,
            'article_subject': 'Viện Sao Mai', 'subject_sentence_id': 0,
            'subject_quote': 'Viện Sao Mai là một tổ chức nghiên cứu.', 'evidence_sentence_ids': [0],
            'technical_domain': None, 'technical_basis_quote': None,
            'reason': 'Chủ thể là viện, không phải một người được nhắc tới.', **updates}


@pytest.mark.parametrize('change', [
    {'predicted_group': 'Other'}, {'abstain': True}, {'evidence_sentence_ids': [False]},
    {'evidence_sentence_ids': [1]}, {'evidence_sentence_ids': [0, 0]},
    {'subject_quote': 'Bằng chứng không tồn tại'}, {'article_subject': 'Người khác'},
    {'subject_sentence_id': True}, {'membership': 'UNKNOWN'},
    {'predicted_group': 'Science / Technology'}, {'extra': 'not allowed'}])
def test_invalid_taxonomy_subject_evidence_and_generic_science_are_rejected(change):
    from vimfc.experiments.groq_classification.integration_policy import validate_integration_response
    r = validate_integration_response(json.dumps(response(**change)), row())
    assert r['status'] != 'valid' and r['prediction'] is None


def test_outside_insufficient_and_ambiguous_are_not_conflated():
    from vimfc.experiments.groq_classification.integration_policy import validate_integration_response
    for membership in ('OUT_OF_TAXONOMY', 'INSUFFICIENT_EVIDENCE', 'AMBIGUOUS'):
        value = response(membership=membership, predicted_group=None, abstain=True)
        r = validate_integration_response(json.dumps(value), row())
        assert r['status'] == 'valid' and r['prediction']['membership'] == membership


def test_valid_prediction_stays_provisional_and_no_gold():
    from vimfc.experiments.groq_classification.integration_policy import resolve_classification
    source = row(); before = copy.deepcopy(source)
    r = resolve_classification(source, response(), origin='new_response')
    assert r['classification_state'] == 'llm_provisional_primary'
    assert r['llm_provisional_primary'] == 'Organization' and r['wikidata_verified_primary'] is None
    assert r['provisional_eligible_candidate'] is True and r['verified_eligible'] is False
    assert r['human_gold'] is None and r['accepted_corpus_promotion'] is False and source == before


def test_verified_route_wins_before_lead_or_model_validation():
    from vimfc.experiments.groq_classification.integration_policy import resolve_classification
    source = row(True); source['input'] = None; source['trusted_lead'] = False
    r = resolve_classification(source, response(predicted_group='Person'), origin='unused')
    assert r['wikidata_verified_primary'] == 'Organization' and r['llm_provisional_primary'] is None
    assert r['classification_state'] == 'wikidata_verified_primary' and r['verified_eligible'] is True


def test_legacy_abstention_does_not_create_outside_taxonomy():
    from vimfc.experiments.groq_classification.integration_policy import legacy_prediction
    source = row()
    legacy = {'prediction': {'predicted_group': None, 'abstain': True, 'evidence_sentence_ids': [], 'reason': 'Thiếu căn cứ.', 'ambiguity': None}}
    migrated = legacy_prediction(source, legacy)
    assert migrated['membership'] == 'UNSPECIFIED_ABSTENTION'
    assert migrated['predicted_group'] is None
    legacy['prediction'] = {'predicted_group': 'Science / Technology', 'abstain': False, 'evidence_sentence_ids': [0], 'reason': 'Khái niệm.', 'ambiguity': None}
    assert legacy_prediction(source, legacy) is None


def test_text_bound_legacy_subject_gate_and_science_specific_quote():
    from vimfc.experiments.groq_classification.integration_policy import legacy_prediction, validate_integration_response
    legacy = {'prediction': {'predicted_group': 'Organization', 'abstain': False, 'evidence_sentence_ids': [0], 'reason': 'Viện.', 'ambiguity': None}}
    assert legacy_prediction(row(), legacy)['membership'] == 'IN_TAXONOMY'
    legacy['prediction']['evidence_sentence_ids'] = [1]
    assert legacy_prediction(row(), legacy) is None
    source = row(); source['input']['title'] = 'Thuật toán Sao Mai'
    source['input']['lead_text'] = 'Thuật toán Sao Mai là một thuật toán tìm kiếm.'
    source['input']['sentences'] = [{'id': 0, 'text': source['input']['lead_text']}]
    source['input_fingerprint'] = digest(source['input'])
    value = response(predicted_group='Science / Technology', article_subject=source['input']['title'],
                     subject_quote=source['input']['lead_text'], technical_domain='technology',
                     technical_basis_quote='thuật toán tìm kiếm')
    assert validate_integration_response(json.dumps(value), source)['status'] == 'valid'
