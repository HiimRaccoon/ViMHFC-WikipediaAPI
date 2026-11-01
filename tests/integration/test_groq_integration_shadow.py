import copy
import json
from pathlib import Path
import pytest

from vimfc.domain import digest
from tests.integration.test_groq_integration_policy import row, response


def rows():
    a, b, c, d = row(True), row(), row(), row()
    for i, r in enumerate((a, b, c, d), 1):
        r['evaluation']['page_id'] = i
    c['trusted_lead'] = False; c['input'] = None; c['input_fingerprint'] = None
    d['quality_gate']['nonclassification_quality_pass'] = False
    return [a, b, c, d]


def test_replay_keeps_verified_and_candidates_in_separate_denominators():
    from vimfc.experiments.groq_classification.integration_shadow import replay_shadow
    data = {'rows': rows(), 'legacy': {}, 'manifest': {'manifest_id': 'fixture'}}
    new = {2: {'status': 'valid', 'prediction': response(), 'request_key': 'two'},
           4: {'status': 'valid', 'prediction': response(), 'request_key': 'four'}}
    r = replay_shadow(data, new)
    assert r['counts'] == {'wikidata_verified_primary': 1, 'llm_provisional_primary': 2,
                            'abstained': 0, 'out_of_taxonomy': 0, 'unresolved': 1}
    assert r['verified_eligible'] == 1 and r['provisional_eligible_candidates'] == 1
    assert r['coverage']['baseline_assigned'] == .25 and r['coverage']['assisted_assigned'] == .75
    assert r['groups']['Organization']['verified'] == 1 and r['groups']['Organization']['provisional'] == 2
    assert r['groups']['Organization']['provisional_eligible_candidates'] == 1
    assert r['accepted_corpus_promotion'] is False and r['human_accuracy'] == 'UNASSESSED'


def test_model_outside_vs_unspecified_abstain_and_invalid_terminal():
    from vimfc.experiments.groq_classification.integration_shadow import replay_shadow
    data = {'rows': rows(), 'legacy': {}, 'manifest': {'manifest_id': 'fixture'}}
    data['rows'][2] = row(); data['rows'][2]['evaluation']['page_id'] = 3
    new = {2: {'status': 'valid', 'prediction': response(membership='OUT_OF_TAXONOMY', predicted_group=None, abstain=True)},
           3: {'status': 'valid', 'prediction': response(membership='INSUFFICIENT_EVIDENCE', predicted_group=None, abstain=True)},
           4: {'status': 'invalid_integration_response', 'prediction': None}}
    result = replay_shadow(data, new)
    assert result['counts']['out_of_taxonomy'] == 1 and result['counts']['abstained'] == 1
    assert result['invalid_terminal_outputs'] == 1 and result['pending_live_count'] == 0
    assert result['out_of_taxonomy_is_model_declared_not_gold'] is True


def test_frozen_shadow_tamper_and_output_source_protection(tmp_path):
    from vimfc.experiments.groq_classification.integration_shadow import freeze_shadow, load_shadow
    root = tmp_path / 'packet'
    m = freeze_shadow(rows(), {}, root, source={'kind': 'synthetic'}, config={'classification': {'version': 'qid-5'}})
    assert len(load_shadow(root)['rows']) == 4
    (root / 'rows.jsonl').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError): load_shadow(root)
    with pytest.raises(ValueError):
        freeze_shadow(rows(), {}, root / 'nested', source={'immutable_source_paths': [str(root)]}, config={})


def test_legacy_cache_identity_checks_do_not_trust_edited_predictions(config):
    from vimfc.experiments.groq_classification.integration_shadow import validate_legacy_cache
    from vimfc.experiments.groq_classification.classifier import request_key
    from vimfc.experiments.groq_classification.prompt import build_prompt, response_format
    from vimfc.experiments.groq_classification.provider import select_model
    source = row(); profile = select_model(['qwen/qwen3.8-27b'], 'qwen/qwen3.8-27b')
    prompt = build_prompt(source, config['classification'])
    p = {'predicted_group': 'Organization', 'abstain': False, 'evidence_sentence_ids': [0], 'reason': 'Viện.', 'ambiguity': None}
    r = {'page_id': 17, 'input_fingerprint': source['input_fingerprint'], 'model_id': profile['model_id'],
         'prompt_hash': prompt['prompt_hash'], 'schema_hash': prompt['schema_hash'], 'prompt_version': prompt['prompt_version'],
         'request_message_hash': digest(prompt['messages']), 'parameters': {'model': profile['model_id'], **profile['request_kwargs'],
         'max_completion_tokens': 256, 'response_format': response_format(profile)},
         'request_key': request_key(source, profile, prompt, {'max_output_tokens': 256}),
         'status': 'valid', 'prediction': p, 'finish_reason': 'stop', 'response_content': json.dumps(p)}
    assert validate_legacy_cache(source, r, config['classification']) == r
    reordered = copy.deepcopy(source)
    reordered['input'] = json.loads(json.dumps(source['input'], sort_keys=True))
    assert validate_legacy_cache(reordered, r, config['classification']) == r
    for field in ('prompt_hash', 'request_key', 'input_fingerprint'):
        bad = {**r, field: 'changed'}
        with pytest.raises(ValueError): validate_legacy_cache(source, bad, config['classification'])
