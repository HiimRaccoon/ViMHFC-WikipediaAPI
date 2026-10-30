"""Prepare only from checksum-verified saved sources; never consult live data."""
from copy import deepcopy
from pathlib import Path

from vimfc.pipeline.discovery_archive import Archive
from .dataset import load_dataset
from .human_review import _read, _records, _safe_output, _sha, load_bundle
from .review_packet import create_review_packet

DATASET_MANIFEST = 'ece7a99e23bbd95488abd226e3cc83ff703e33fff23a01d5eaa8f1181ae2e161'
PREVIOUS_MANIFEST = 'd33cb23c625450bc581dea8279288fc691fcc53beafe125f54ac2690b4f44c66'


def archive_evidence(row, record):
    e, c = row['evaluation'], record['classification']
    if (c['manifest_id'] != row['provenance']['manifest_id']
            or c['dependency_manifest']['manifest_id'] != c['manifest_id']
            or c['outcome'] != e['baseline_outcome'] or c.get('primary_entity_type') != e['baseline_primary']
            or c.get('reason') != e['classification_reason']):
        raise ValueError('Historical classification/manifest differs from frozen source')
    status = ('COMPLETE' if c['outcome'] == 'completed' else
              'ABSENT_SUPPORTED_RULE' if c.get('reason') == 'no_supported_rule' else
              'AMBIGUOUS' if c.get('reason') == 'primary_ambiguity' else
              'DEPENDENCY_UNAVAILABLE' if c.get('reason') == 'primary_dependency_missing' else
              'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE')
    refs = ['manifest:' + c['manifest_id']]
    refs += [f"node:{n['qid']}:{n['revision_id']}" for n in c['dependency_manifest']['nodes']]
    return {'availability': status, 'verified_primary': e['baseline_primary'],
            'evidence_refs': refs, 'original_classification': deepcopy(c),
            'source_binding': deepcopy(row['provenance']),
            'verification_scope': 'Original archived qid-5 primary only. A path for another candidate, '
                                  'an unresolved dependency or a semantic label does not verify a new primary.',
            'cache_absence_is_historical_nonexistence': False,
            'default_suggestion_verdict': 'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE'}


def prepare_review(dataset, results, previous_bundle, output, *, seed=20261031,
                   expected_dataset_manifest=DATASET_MANIFEST,
                   expected_previous_manifest=PREVIOUS_MANIFEST):
    dataset, results, previous_bundle = map(Path, (dataset, results, previous_bundle))
    dm = _read(dataset / 'manifest.json')
    if dm['manifest_id'] != expected_dataset_manifest:
        raise ValueError('Unexpected frozen dataset identity')
    _, previous_refs, _ = load_bundle(previous_bundle, expected_previous_manifest)
    protected = [str(p.resolve()) for p in (dataset.parent, results.parent, previous_bundle)]
    output = _safe_output(output, protected)
    rows = load_dataset(dataset)
    saved = _read(results)
    by_id, available = _records(rows, saved)
    if len(rows) != 300 or len(available) != 179 or sum(r['prediction']['abstain'] for r in available.values()) != 28:
        raise ValueError('Expected frozen 300 inputs and exactly 179 captured responses, including 28 abstentions')
    required = []
    for ref in previous_refs.values():
        row = by_id.get(ref['page_id'])
        if row is None or ref['input_fingerprint'] != row['input_fingerprint']:
            raise ValueError('Previous review case does not match frozen source')
        required.append(ref['page_id'])
    with Archive(dm['source']['context']) as archive:
        records = archive.records()
        if archive.config != dm['config']:
            raise ValueError('Archived policy changed')
        evidence = {page: archive_evidence(by_id[page], records[page]) for page in available}
    pipeline = Path(__file__).resolve().parents[2] / 'pipeline'
    policy_sources = {name: {'sha256': _sha(pipeline / name), 'text': (pipeline / name).read_text(encoding='utf-8')}
                      for name in ('primary.py', 'classify.py', 'dependencies.py')}
    context = dm['source']['context']
    protected += [str(Path(context['database']).resolve()), str(Path(context['raw_cache']).resolve())]
    source = {'dataset_manifest_id': dm['manifest_id'], 'dataset_manifest_sha256': _sha(dataset / 'manifest.json'),
              'inputs_sha256': _sha(dataset / 'inputs.jsonl'), 'results_sha256': _sha(results),
              'previous_review_manifest_id': expected_previous_manifest,
              'previous_review_ids': {ref['review_id']: ref['page_id'] for ref in previous_refs.values()},
              'captured_responses': 179, 'predictions': 151, 'abstentions': 28, 'invalid': 0, 'pending': 121,
              'snapshot': context['snapshot'], 'source_context': context,
              'pipeline_rules': 'qid-5 / lead-4 / quality-2.1 / rank-1', 'immutable_source_paths': protected,
              'new_api_requests': 0, 'human_gold_source': 'Actual manual submissions only; none created during preparation.'}
    return create_review_packet(rows, saved, output, classification=dm['config']['classification'], source=source,
                                evidence=evidence, seed=seed, required_ids=required, policy_sources=policy_sources)
