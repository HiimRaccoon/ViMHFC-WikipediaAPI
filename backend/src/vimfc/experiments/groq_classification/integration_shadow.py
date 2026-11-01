"""Read-only full-cohort freeze and descriptive integration replay."""
from collections import Counter
import copy
from pathlib import Path
import sqlite3

from vimfc.domain import GROUPS, digest
from vimfc.pipeline.discovery_archive import Archive
from .classifier import request_key
from .dataset import _verified_input
from .human_review import _bytes, _jsonl, _lines, _new, _read, _safe_output, _sha
from .integration_policy import MODEL, VERSION, legacy_prediction, resolve_classification
from .prompt import build_prompt, response_format, validate_prediction
from .provider import select_model

FORMAT = 'vimfc-integration-shadow-1'


def validate_legacy_cache(row, result, rules):
    profile = select_model([MODEL], MODEL)
    prompt = build_prompt(row, rules)
    # The saved dataset serialized input keys canonically, while the historical
    # extraction adapter reconstructs title/lead/sentences order. Reproduce only
    # those two known legacy wire representations and require an exact hash.
    message_hashes = set()
    for fields in (('lead_text', 'sentences', 'title'), ('title', 'lead_text', 'sentences')):
        variant = {**row, 'input': {k: row['input'][k] for k in fields}}
        message_hashes.add(digest(build_prompt(variant, rules)['messages']))
    params = result['parameters']
    expected = {'model': MODEL, **profile['request_kwargs'], 'max_completion_tokens': params['max_completion_tokens'],
                'response_format': response_format(profile)}
    if (result['page_id'] != row['evaluation']['page_id'] or result['model_id'] != MODEL
            or result['input_fingerprint'] != row['input_fingerprint'] or params != expected
            or result['prompt_hash'] != prompt['prompt_hash'] or result['schema_hash'] != prompt['schema_hash']
            or result['prompt_version'] != prompt['prompt_version']
            or result['request_message_hash'] not in message_hashes
            or result['request_key'] != request_key(row, profile, prompt, {'max_output_tokens': params['max_completion_tokens']})):
        raise ValueError('Legacy cache request/provenance identity differs')
    checked = validate_prediction(result['response_content'], row)
    if result['finish_reason'] != 'stop' or checked['status'] != 'valid' or checked['prediction'] != result['prediction'] or result['status'] != 'valid':
        raise ValueError('Legacy cache response is not valid')
    return result


def freeze_shadow(rows, legacy, output, *, source, config):
    root = _safe_output(output, source.get('immutable_source_paths', []))
    if len({r['evaluation']['page_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate shadow article identity')
    content = {'rows.jsonl': _jsonl(rows), 'legacy-cache.json': _bytes(legacy)}
    m = {'format': FORMAT, 'policy_version': VERSION, 'source': source, 'config': config,
         'size': len(rows), 'files': {name: {'sha256': __import__('hashlib').sha256(data).hexdigest(), 'bytes': len(data)} for name, data in content.items()}}
    m['manifest_id'] = digest(m)
    for name, data in content.items(): _new(root / name, data)
    _new(root / 'manifest.json', _bytes(m))
    return m


def load_shadow(root, expected_manifest_id=None):
    root = Path(root).resolve()
    m = _read(root / 'manifest.json'); identity = m.pop('manifest_id')
    if m.get('format') != FORMAT or digest(m) != identity or (expected_manifest_id is not None and identity != expected_manifest_id):
        raise ValueError('Shadow manifest differs')
    if set(m['files']) != {'rows.jsonl', 'legacy-cache.json'}:
        raise ValueError('Unexpected shadow paths')
    for name, info in m['files'].items():
        p = root / name
        if not p.resolve().is_relative_to(root) or p.stat().st_size != info['bytes'] or _sha(p) != info['sha256']:
            raise ValueError('Shadow frozen source tampered')
    rows, legacy = _lines(root / 'rows.jsonl'), _read(root / 'legacy-cache.json')
    if len(rows) != m['size'] or len({r['evaluation']['page_id'] for r in rows}) != len(rows):
        raise ValueError('Shadow membership differs')
    for r in rows:
        if r['trusted_lead']:
            from .prompt import validate_input
            validate_input(r['input'])
            if digest(r['input']) != r['input_fingerprint']: raise ValueError('Shadow input fingerprint differs')
    m['manifest_id'] = identity
    return {'root': root, 'rows': rows, 'legacy': legacy, 'manifest': m}


def _table_rows(db, table):
    return [dict(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]


def prepare_shadow(context_path, legacy_ledger, output):
    context_path, legacy_ledger = Path(context_path), Path(legacy_ledger)
    context = _read(context_path)
    _safe_output(output, [Path(context['database']).parent, legacy_ledger.parent])
    with sqlite3.connect(legacy_ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("SELECT 1 FROM state WHERE key='lease'").fetchone():
            raise ValueError('Original Groq worker has an active lease')
        cache = []
        for r in db.execute('SELECT record_json,record_hash FROM results'):
            from .human_review import _json
            obj = _json(r[0])
            if digest(obj) != r[1]: raise ValueError('Legacy result checksum differs')
            cache.append(obj)
        ledger_counts = {'results': len(cache), 'attempts': db.execute('SELECT count(*) FROM attempts').fetchone()[0]}
    raw_by = {(r['page_id'], r['input_fingerprint']): r for r in cache if r['model_id'] == MODEL}
    rows, legacy = [], {}
    excluded = Counter()
    with Archive(context) as archive:
        records = archive.records()
        checkpoint_hashes = {int(r[0]): r[1] for r in archive.db.execute(
            "SELECT identity,result_hash FROM stage_results WHERE run_id=? AND stage='process' AND status='completed'", (context['run_id'],))}
        order = {c['page_id']: i for i, c in enumerate(archive.cohort)}
        if len(records) != 985: raise ValueError('Expected exact985processed historical Pilot records')
        for page, record in sorted(records.items(), key=lambda item: order[item[0]]):
            c, q, x = record['classification'], record['quality'], record['extraction']
            inputs, reason = _verified_input(archive, record)
            trusted = inputs is not None
            if not trusted: excluded[reason] += 1
            e = {'page_id': page, 'revision_id': record['revision_id'], 'snapshot': record['snapshot'],
                 'baseline_outcome': c['outcome'], 'baseline_primary': c.get('primary_entity_type'),
                 'classification_reason': c.get('reason'), 'eligible': q['eligible']}
            residual = [reason for reason in q['reasons'] if reason != 'unsupported_classification']
            row = {'evaluation': e, 'trusted_lead': trusted, 'lead_exclusion_reason': reason,
                   'input': inputs, 'input_fingerprint': digest(inputs) if trusted else None,
                   'quality_gate': {'extraction_pass': x['outcome'] == 'completed',
                                    'nonclassification_quality_pass': not residual,
                                    'original_quality_reasons': q['reasons'], 'remaining_reasons': residual},
                   'provenance': {'source_run_id': context['run_id'], 'original_record_hash': checkpoint_hashes[page],
                                  'manifest_id': c['manifest_id'], 'raw_hash': record['raw_hash'],
                                  'revision_timestamp': record['revision_timestamp'], 'cache_ref': record['cache_ref'],
                                  'extraction_hash': digest(x), 'classification_hash': digest(c), 'quality_hash': digest(q)}}
            rows.append(row)
            # Never look up per-article LLM cache for an already verified primary.
            if trusted and c['outcome'] != 'completed':
                old = raw_by.get((page, row['input_fingerprint']))
                if old is not None:
                    legacy[str(page)] = validate_legacy_cache(row, old, archive.config['classification'])
        config = copy.deepcopy(archive.config)
        work = [__import__('json').loads(r[0]) for r in archive.db.execute('SELECT record_json FROM candidate_work WHERE run_id=?', (context['run_id'],))]
        source_state = Counter(r.get('status', 'unknown') for r in work)
        cohort_size = len(archive.cohort)
    paths = [context_path, Path(context['database']), legacy_ledger]
    paths += [Path(str(p) + '-wal') for p in paths if p.suffix == '.sqlite']
    source = {'context': context, 'cohort_size': cohort_size, 'processed_count': len(rows),
              'source_work_status_counts': dict(source_state), 'trusted_leads': sum(r['trusted_lead'] for r in rows),
              'lead_exclusions': dict(excluded), 'legacy_ledger': str(legacy_ledger.resolve()),
              'legacy_ledger_counts': ledger_counts, 'legacy_records_on_assistance_candidates': len(legacy),
              'snapshot': context['snapshot'], 'pipeline_rules': 'qid-5 / lead-4 / quality-2.1 / rank-1',
              'pinned_source_files': {str(p.resolve()): _sha(p) for p in paths if p.exists()},
              'immutable_source_paths': [str(Path(context['database']).parent.resolve()),
                                         str(legacy_ledger.parent.resolve())]}
    return freeze_shadow(rows, legacy, output, source=source, config=config)


def replay_shadow(data, new_results=None):
    new_results = new_results or {}
    outcomes, pending, rejected_cache = [], [], []
    invalid = 0
    for row in data['rows']:
        e = row['evaluation']; page = e['page_id']
        if e['baseline_outcome'] == 'completed' or not row['trusted_lead']:
            outcomes.append(resolve_classification(row)); continue
        result = new_results.get(page)
        if result is not None:
            if result['status'] != 'valid': invalid += 1
            resolved = resolve_classification(row, result['prediction'], origin='integration_cache',
                                               unresolved_reason=result['status'])
            resolved['request_key'] = result.get('request_key')
            outcomes.append(resolved); continue
        legacy = data['legacy'].get(str(page))
        migrated = legacy_prediction(row, legacy) if legacy else None
        if migrated is not None:
            resolved = resolve_classification(row, migrated, origin='legacy_cache')
            resolved['request_key'] = legacy.get('request_key')
            outcomes.append(resolved); continue
        if legacy: rejected_cache.append(page)
        pending.append(page)
        outcomes.append(resolve_classification(row, unresolved_reason='awaiting_new_schema_response'))
    counts = Counter(r['classification_state'] for r in outcomes)
    groups = {}
    for group in GROUPS:
        verified = [r for r in outcomes if r['wikidata_verified_primary'] == group]
        provisional = [r for r in outcomes if r['llm_provisional_primary'] == group]
        groups[group] = {'verified': len(verified), 'provisional': len(provisional),
                         'verified_extraction_pass': sum(r['quality_gate']['extraction_pass'] for r in verified),
                         'provisional_extraction_pass': sum(r['quality_gate']['extraction_pass'] for r in provisional),
                         'verified_nonclassification_quality_pass': sum(r['quality_gate']['nonclassification_quality_pass'] for r in verified),
                         'provisional_nonclassification_quality_pass': sum(r['quality_gate']['nonclassification_quality_pass'] for r in provisional),
                         'verified_eligible': sum(r['verified_eligible'] for r in verified),
                         'provisional_eligible_candidates': sum(r['provisional_eligible_candidate'] for r in provisional)}
    n = len(outcomes)
    verified = counts['wikidata_verified_primary']; provisional = counts['llm_provisional_primary']
    return {'format': FORMAT, 'policy_version': VERSION, 'dataset_manifest_id': data['manifest']['manifest_id'],
            'processed_denominator': n,
            'counts': {k: counts[k] for k in ('wikidata_verified_primary', 'llm_provisional_primary', 'abstained', 'out_of_taxonomy', 'unresolved')},
            'coverage': {'baseline_assigned_count': verified, 'assisted_assigned_count': verified + provisional,
                         'denominator': n, 'baseline_assigned': verified / n if n else None,
                         'assisted_assigned': (verified + provisional) / n if n else None,
                         'note': 'Assigned coverage includes provisional labels; it is not verified coverage or accuracy.'},
            'groups': groups, 'verified_eligible': sum(r['verified_eligible'] for r in outcomes),
            'provisional_eligible_candidates': sum(r['provisional_eligible_candidate'] for r in outcomes),
            'trusted_leads': sum(r['trusted_lead'] for r in data['rows']),
            'legacy_cache_reused': sum(r['origin'] == 'legacy_cache' for r in outcomes),
            'legacy_cache_needs_new_subject_or_scope_validation': len(rejected_cache),
            'invalid_terminal_outputs': invalid, 'pending_live_count': len(pending), 'pending_page_ids': pending,
            'membership_counts': dict(Counter(r['membership'] for r in outcomes if r['membership'])),
            'source': data['manifest'].get('source', {}), 'out_of_taxonomy_is_model_declared_not_gold': True,
            'human_accuracy': 'UNASSESSED', 'correct_additional_coverage': None,
            'accepted_corpus_promotion': False, 'outcomes': outcomes}
