"""Immutable, offline sampling of trusted historical lead checkpoints.

Only the ``input`` member is suitable for an inference request. Evaluation and
provenance are deliberately stored beside it, never merged into model input.
"""
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from vimfc.domain import GROUPS, Incompatible, canonical, digest
from vimfc.pipeline.discovery_archive import Archive, STREAM_STRATA
from vimfc.pipeline.extract import extract_lead


FORMAT = 'vimfc-groq-dataset-1'
SAMPLING_VERSION = 'outcome-balanced-focus-round-robin-2'
OUTCOMES = ('completed', 'unclassified', 'unresolved')
FOCUS = ('Event', 'Location', 'Science / Technology')
ARTIFACTS = ('inputs.jsonl', 'exclusions.jsonl', 'sampling-report.json')
CONTEXT_KEYS = ('database', 'raw_cache', 'run_id', 'snapshot', 'config_hash', 'cohort_hash')


def _source_context(context):
    result = {key: str(context[key]) for key in CONTEXT_KEYS}
    for key in ('database', 'raw_cache'):
        result[key] = str(Path(result[key]).resolve())
    return result


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_lines(rows):
    return ''.join(canonical(row) + '\n' for row in rows).encode('utf-8')


def _verified_input(archive, record):
    extraction = record['extraction']
    if extraction['outcome'] != 'completed':
        return None, 'unresolved_extraction'
    if extraction.get('diagnostics') or extraction.get('stats', {}).get('provisional'):
        raise Incompatible('Completed historical extraction contains provisional prose')
    if extraction['extraction_version'] != archive.config['extraction']['version']:
        raise Incompatible('Historical extraction policy differs from archived configuration')
    if record['snapshot'] != archive.context['snapshot']:
        raise Incompatible('Historical article snapshot differs from source context')
    if record.get('content_model') != 'wikitext':
        raise Incompatible('Historical article is not wikitext')
    # Archive.records already checks namespace proof for discovery sources. For
    # older sources, a colon title needs a separate explicit namespace proof.
    if ':' in record['title'] and record.get('discovery_source_scope', {}).get('namespace') != 0:
        return None, 'historical_namespace_unverified'
    raw = archive.checked_raw(record['cache_ref'], page_id=record['page_id'],
                              revision_timestamp=record['revision_timestamp'],
                              raw_hash=record['raw_hash'], model='wikitext')
    replay = asdict(extract_lead(raw['content'], archive.config['extraction']))
    # This is validation under the saved policy, never a replacement extraction.
    # In particular unresolved checkpoints are excluded before replay is called.
    if replay['outcome'] != 'completed' or any(extraction[key] != replay[key]
                                             for key in ('full_lead', 'paragraphs', 'sentences')):
        raise Incompatible('Historical retained lead/sentence offsets do not replay exactly')
    if not extraction['full_lead'].strip() or not extraction['sentences']:
        return None, 'empty_historical_lead'
    for index, sentence in enumerate(extraction['sentences']):
        paragraph = extraction['paragraphs'][sentence['paragraph_id']]
        if (type(sentence['sent_id']) is not int or sentence['sent_id'] != index
                or type(sentence['start']) is not int or type(sentence['end']) is not int
                or not 0 <= sentence['start'] < sentence['end'] <= len(paragraph)
                or paragraph[sentence['start']:sentence['end']] != sentence['text']):
            raise Incompatible('Historical sentence ID or Unicode span is invalid')
    return {'title': record['title'], 'lead_text': extraction['full_lead'],
            'sentences': [{'id': sentence['sent_id'], 'text': sentence['text']}
                          for sentence in extraction['sentences']]}, None


def _rows(archive):
    records = archive.records()
    # Modern cohorts carry the frozen discovery hint explicitly. Preserve it
    # even when verified primary classification later establishes another group.
    # STREAM_STRATA is solely a compatibility fallback for older cohorts.
    origins = {candidate['page_id']: candidate['sampling_stratum'] if 'sampling_stratum' in candidate
               else STREAM_STRATA.get(candidate.get('discovery_origin'), 'unassigned')
               for candidate in archive.cohort}
    rows, exclusions = [], []
    for page_id, record in sorted(records.items()):
        inputs, reason = _verified_input(archive, record)
        if reason:
            exclusions.append({'page_id': page_id, 'revision_id': record['revision_id'], 'reason': reason})
            continue
        classification = record['classification']
        outcome, primary = classification['outcome'], classification.get('primary_entity_type')
        if outcome not in OUTCOMES or (outcome == 'completed' and primary not in GROUPS) or (outcome != 'completed' and primary is not None):
            raise Incompatible('Invalid historical primary classification state')
        discovery_hint = origins.get(page_id, 'unassigned')
        if not isinstance(discovery_hint, str) or not discovery_hint:
            raise Incompatible('Historical discovery sampling stratum must be a nonempty string')
        group = primary if outcome == 'completed' else discovery_hint
        rows.append({'input': inputs,
                     'evaluation': {'page_id': page_id, 'revision_id': record['revision_id'],
                                    'snapshot': record['snapshot'], 'baseline_outcome': outcome,
                                    'baseline_primary': primary, 'classification_reason': classification.get('reason'),
                                    'sampling_stratum': discovery_hint, 'experiment_sampling_bucket': outcome + ':' + group,
                                    'eligible': record['quality']['eligible']},
                     'provenance': {'source_run_id': archive.context['run_id'], 'raw_hash': record['raw_hash'],
                                    'cache_ref': deepcopy(record['cache_ref']), 'manifest_id': classification['manifest_id'],
                                    'revision_timestamp': record['revision_timestamp'], 'source_config_hash': archive.context['config_hash'],
                                    'extraction_version': record['extraction']['extraction_version'],
                                    'extraction_hash': digest(record['extraction']), 'namespace_proof': deepcopy(record.get('discovery_source_scope'))},
                     'input_fingerprint': digest(inputs)})
    return rows, exclusions, len(records)


def _sample(rows, size, seed):
    """Equal outcome quotas; weighted round robin within saved source strata."""
    if type(size) is not int or size <= 0 or type(seed) is not int:
        raise Incompatible('Dataset size must be positive and seed must be an integer')
    if size > len(rows):
        raise Incompatible(f'Only {len(rows)} trusted historical articles available for requested {size}')
    rank = lambda row: digest([seed, row['evaluation']['page_id'], row['evaluation']['revision_id'], row['input_fingerprint']])
    pools = {outcome: [row for row in rows if row['evaluation']['baseline_outcome'] == outcome] for outcome in OUTCOMES}
    quotas = {outcome: 0 for outcome in OUTCOMES}
    for _ in range(size):
        available = [outcome for outcome in OUTCOMES if quotas[outcome] < len(pools[outcome])]
        outcome = min(available, key=lambda item: (quotas[item], OUTCOMES.index(item)))
        quotas[outcome] += 1
    selected = []
    for outcome in OUTCOMES:
        groups = defaultdict(list)
        for row in pools[outcome]:
            groups[row['evaluation']['experiment_sampling_bucket'].split(':', 1)[1]].append(row)
        queues = {group: deque(sorted(values, key=rank)) for group, values in groups.items()}
        # Start with one from every represented group, then repeat focus groups
        # twice per cycle. Origin strata for unresolved articles are sampling
        # hints, never assertions of article type or human ground truth.
        cycle = [group for group in GROUPS if group in queues]
        cycle += sorted(set(queues) - set(cycle))
        initial = min(quotas[outcome], len(cycle))
        for group in cycle[:initial]:
            selected.append(queues[group].popleft())
        remaining = quotas[outcome] - initial
        weighted = [group for group in FOCUS if group in queues] + cycle
        while remaining:
            for group in weighted:
                if remaining and queues[group]:
                    selected.append(queues[group].popleft())
                    remaining -= 1
    return sorted(selected, key=rank), quotas


def _sampling_report(available, selected, quotas):
    population = Counter(row['evaluation']['experiment_sampling_bucket'] for row in available)
    chosen = Counter(row['evaluation']['experiment_sampling_bucket'] for row in selected)
    discovery_population = Counter(row['evaluation']['sampling_stratum'] for row in available)
    discovery_chosen = Counter(row['evaluation']['sampling_stratum'] for row in selected)
    return {'version': SAMPLING_VERSION, 'outcome_quotas': quotas, 'focus_groups': list(FOCUS),
            'method': 'equal outcome quotas; one per represented group then focus weight 2, other weight 1; seeded SHA256 order',
            'interpretation': 'diagnostic stratified sample; not population representative; origin hints are not gold labels',
            'strata_basis': 'evaluation.experiment_sampling_bucket',
            'strata': [{'stratum': group, 'available': population[group], 'selected': chosen[group],
                        'selection_fraction': chosen[group] / population[group]} for group in sorted(population)],
            'discovery_strata_basis': 'evaluation.sampling_stratum',
            'discovery_strata': [{'stratum': group, 'available': discovery_population[group], 'selected': discovery_chosen[group],
                                  'selection_fraction': discovery_chosen[group] / discovery_population[group]}
                                 for group in sorted(discovery_population)],
            'eligible_selected': sum(row['evaluation']['eligible'] for row in selected)}


def prepare_dataset(context: dict, output: Path, size=300, seed=20261029) -> dict:
    """Export a new directory, refusing existing paths or incompatible sources."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Dataset output already exists: {output}')
    try:
        source = _source_context(context)
        if output.resolve().is_relative_to(Path(source['raw_cache'])) or output.resolve() == Path(source['database']):
            raise Incompatible('Experiment output must be separate from historical raw cache and database')
        with Archive(source) as archive:
            available, exclusions, processed = _rows(archive)
            selected, quotas = _sample(available, size, seed)
            config = deepcopy(archive.config)
        report = _sampling_report(available, selected, quotas)
        payloads = {'inputs.jsonl': _json_lines(selected), 'exclusions.jsonl': _json_lines(exclusions),
                    'sampling-report.json': (canonical(report) + '\n').encode('utf-8')}
        manifest = {'format': FORMAT, 'sampling_version': SAMPLING_VERSION, 'seed': seed, 'size': size,
                    'source': {'context': source, 'run_id': source['run_id'], 'snapshot': source['snapshot'],
                               'config_hash': source['config_hash'], 'cohort_hash': source['cohort_hash'],
                               'processed_count': processed, 'trusted_count': len(available), 'excluded_count': len(exclusions)},
                    'config': config, 'config_hash': digest(config),
                    'rules_hashes': {key: digest(config[key]) for key in ('classification', 'extraction', 'ranking')},
                    'files': {name: {'sha256': _sha(data), 'bytes': len(data)} for name, data in payloads.items()},
                    'selection': [{'page_id': row['evaluation']['page_id'], 'row_hash': digest(row),
                                   'input_fingerprint': row['input_fingerprint']} for row in selected]}
        manifest['manifest_id'] = digest(manifest)
    except (ValueError, KeyError, TypeError, OSError, AttributeError, IndexError) as error:
        raise Incompatible('Invalid historical dataset source; no network recovery') from error
    # The directory is the exclusive reservation. Write the commit marker last;
    # interrupted exports remain invalid and cannot be silently overwritten.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in payloads.items():
        with (output / name).open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    with (output / 'manifest.json').open('xb') as stream:
        stream.write((canonical(manifest) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    return manifest


def load_dataset(root: Path) -> list[dict]:
    """Verify artifact hashes and replay source provenance before returning rows."""
    try:
        root = Path(root)
        manifest_bytes = (root / 'manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        identity = manifest.pop('manifest_id')
        if identity != digest(manifest) or manifest_bytes != (canonical({**manifest, 'manifest_id': identity}) + '\n').encode('utf-8'):
            raise Incompatible('Dataset manifest checksum or canonical encoding mismatch')
        if manifest['format'] != FORMAT or manifest['sampling_version'] != SAMPLING_VERSION or set(manifest['files']) != set(ARTIFACTS):
            raise Incompatible('Unsupported dataset format or artifacts')
        content = {}
        for name in ARTIFACTS:
            data = (root / name).read_bytes()
            if manifest['files'][name] != {'sha256': _sha(data), 'bytes': len(data)}:
                raise Incompatible('Dataset artifact checksum mismatch: ' + name)
            content[name] = data
        rows = [json.loads(line) for line in content['inputs.jsonl'].decode('utf-8').splitlines()]
        source = manifest['source']['context']
        if digest(manifest['config']) != manifest['config_hash'] or manifest['config_hash'] != source['config_hash']:
            raise Incompatible('Dataset source configuration mismatch')
        with Archive(source) as archive:
            if archive.config != manifest['config']:
                raise Incompatible('Dataset configuration differs from archived source')
            available, exclusions, processed = _rows(archive)
        expected, quotas = _sample(available, manifest['size'], manifest['seed'])
        if rows != expected or content['inputs.jsonl'] != _json_lines(expected):
            raise Incompatible('Dataset rows differ from deterministic trusted source sample')
        if content['exclusions.jsonl'] != _json_lines(exclusions):
            raise Incompatible('Dataset exclusions differ from trusted source')
        report = _sampling_report(available, expected, quotas)
        if content['sampling-report.json'] != (canonical(report) + '\n').encode('utf-8'):
            raise Incompatible('Dataset sampling report differs from trusted source')
        selection = [{'page_id': row['evaluation']['page_id'], 'row_hash': digest(row),
                      'input_fingerprint': row['input_fingerprint']} for row in rows]
        expected_source = {'context': source, 'run_id': source['run_id'], 'snapshot': source['snapshot'],
                           'config_hash': source['config_hash'], 'cohort_hash': source['cohort_hash'],
                           'processed_count': processed, 'trusted_count': len(available), 'excluded_count': len(exclusions)}
        if (manifest['selection'] != selection or manifest['source'] != expected_source
                or manifest['rules_hashes'] != {key: digest(manifest['config'][key]) for key in ('classification', 'extraction', 'ranking')}):
            raise Incompatible('Dataset selection, rule hashes or source identity mismatch')
        return rows
    except (ValueError, KeyError, TypeError, OSError, AttributeError, IndexError) as error:
        raise Incompatible('Dataset missing, corrupt or incompatible with historical source') from error
