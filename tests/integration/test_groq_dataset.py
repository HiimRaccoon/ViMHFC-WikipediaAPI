"""Offline export integrity: trusted historical inputs never become model labels."""
from copy import deepcopy
from dataclasses import asdict
from importlib import import_module
import hashlib
import json
from pathlib import Path

import pytest

from vimfc.domain import GROUPS, Incompatible, canonical, digest
from vimfc.pipeline.extract import extract_lead
from tests.integration.test_discovery_archive import fixture_archive


def api():
    return import_module('vimfc.experiments.groq_classification.dataset')


def synthetic_archive(monkeypatch, config, *, count=24, modern_hints=None):
    """Archive is separately integrated below; large sampling uses verified fixtures."""
    text = 'Đây là một tổ chức nghiên cứu. Tổ chức được thành lập năm 1990.'
    extraction = asdict(extract_lead(text, config['extraction']))
    records = {}
    origins = ('people', 'organizations', 'locations', 'events', 'works', 'awards', 'facilities', 'science')
    context = {'database': 'fixture.sqlite', 'raw_cache': 'fixture-raw', 'run_id': 'fixture-run',
               'snapshot': '2026-10-25T03:00:00Z', 'config_hash': digest(config), 'cohort_hash': 'b' * 64}
    for i in range(count):
        outcome = ('completed', 'unclassified', 'unresolved')[i % 3]
        manifest_id = digest({'source': i})
        records[i + 1] = {'page_id': i + 1, 'revision_id': 100 + i, 'title': 'Bài ' + str(i),
                         'snapshot': context['snapshot'], 'revision_timestamp': '2026-10-20T00:00:00Z',
                         'raw_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'cache_ref': {'path': f'viwiki/{100+i}/raw.json.gz', 'sha256': 'a' * 64},
                         'content_model': 'wikitext', 'extraction': deepcopy(extraction),
                         'classification': {'outcome': outcome, 'primary_entity_type': GROUPS[(i // 3) % 8] if outcome == 'completed' else None,
                                            'reason': None if outcome == 'completed' else 'no_primary', 'manifest_id': manifest_id},
                         'quality': {'eligible': i % 2 == 0}}

    class FixtureArchive:
        def __init__(self, source):
            self.config = config
            self.context = source
            self.cohort = [{'page_id': i + 1, 'discovery_origin': origins[(i // 3) % 8]} for i in range(count)]
            if modern_hints is not None:
                for candidate in self.cohort:
                    candidate['discovery_origin'] = 'stratified-seed-only'
                    candidate['sampling_stratum'] = modern_hints.get(candidate['page_id'], 'Person')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def records(self):
            return deepcopy(records)

        def checked_raw(self, *args, **kwargs):
            return {'content': text}

    monkeypatch.setattr(api(), 'Archive', FixtureArchive)
    return context, records


def test_deterministic_balanced_sampling_and_no_input_metadata_leakage(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config, count=240)
    first = tmp_path / 'Windows path with spaces' / 'first'
    second = tmp_path / 'second'
    manifest = dataset.prepare_dataset(context, first, size=90, seed=20261029)
    other = dataset.prepare_dataset(context, second, size=90, seed=20261029)
    rows = dataset.load_dataset(first)
    assert len(rows) == 90
    assert manifest == other
    assert (first / 'inputs.jsonl').read_bytes() == (second / 'inputs.jsonl').read_bytes()
    assert {outcome: sum(row['evaluation']['baseline_outcome'] == outcome for row in rows)
            for outcome in ('completed', 'unclassified', 'unresolved')} == {'completed': 30, 'unclassified': 30, 'unresolved': 30}
    assert {row['evaluation']['baseline_primary'] for row in rows if row['evaluation']['baseline_outcome'] == 'completed'} == set(GROUPS)
    assert {row['evaluation']['eligible'] for row in rows} == {False, True}
    for row in rows:
        assert set(row) == {'input', 'evaluation', 'provenance', 'input_fingerprint'}
        assert set(row['input']) == {'title', 'lead_text', 'sentences'}
        assert all(set(sentence) == {'id', 'text'} for sentence in row['input']['sentences'])
        assert row['input_fingerprint'] == digest(row['input'])
    before = (first / 'inputs.jsonl').read_bytes()
    with pytest.raises((FileExistsError, Incompatible)):
        dataset.prepare_dataset(context, first, size=90)
    assert (first / 'inputs.jsonl').read_bytes() == before


def test_actual_archive_is_read_only_and_raw_tampering_fails_closed(tmp_path, config):
    dataset = api()
    context = fixture_archive(tmp_path, config)
    before = Path(context['database']).read_bytes()
    root = tmp_path / 'export'
    dataset.prepare_dataset(context, root, size=1)
    rows = dataset.load_dataset(root)
    assert rows[0]['evaluation']['page_id'] == 1
    assert rows[0]['provenance']['source_run_id'] == context['run_id']
    assert Path(context['database']).read_bytes() == before
    raw = next(Path(context['raw_cache']).rglob('*.json.gz'))
    raw.write_bytes(b'corrupted raw')
    with pytest.raises(Incompatible):
        dataset.load_dataset(root)


def test_provisional_extraction_is_audited_without_reinterpreting_it(tmp_path, monkeypatch, config):
    dataset = api()
    context, records = synthetic_archive(monkeypatch, config)
    records[1]['extraction']['outcome'] = 'unresolved_extraction'
    records[1]['extraction']['stats']['provisional'] = True
    root = tmp_path / 'export'
    manifest = dataset.prepare_dataset(context, root, size=12)
    assert manifest['source']['processed_count'] == 24
    exclusions = [json.loads(line) for line in (root / 'exclusions.jsonl').read_text(encoding='utf-8').splitlines()]
    assert exclusions == [{'page_id': 1, 'revision_id': 100, 'reason': 'unresolved_extraction'}]
    assert 1 not in {row['evaluation']['page_id'] for row in dataset.load_dataset(root)}


@pytest.mark.parametrize('mutation', ['offset', 'text', 'version'])
def test_source_extraction_inconsistency_aborts_export(tmp_path, monkeypatch, config, mutation):
    dataset = api()
    context, records = synthetic_archive(monkeypatch, config)
    extraction = records[1]['extraction']
    if mutation == 'offset':
        extraction['sentences'][0]['start'] = 1
    elif mutation == 'text':
        extraction['full_lead'] = 'Forged text.'
    else:
        extraction['extraction_version'] = 'future-policy'
    root = tmp_path / 'export'
    with pytest.raises(Incompatible):
        dataset.prepare_dataset(context, root, size=12)
    assert not root.exists()


@pytest.mark.parametrize('target', ['inputs.jsonl', 'manifest.json', 'exclusions.jsonl', 'sampling-report.json'])
def test_dataset_artifact_tampering_is_rejected(tmp_path, monkeypatch, config, target):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    root = tmp_path / 'export'
    dataset.prepare_dataset(context, root, size=12)
    path = root / target
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(Incompatible):
        dataset.load_dataset(root)


@pytest.mark.parametrize('mutation', ['input', 'baseline', 'provenance', 'leakage'])
def test_resealed_input_metadata_cannot_forge_trusted_source(tmp_path, monkeypatch, config, mutation):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    root = tmp_path / 'export'
    dataset.prepare_dataset(context, root, size=12)
    rows = dataset.load_dataset(root)
    if mutation == 'input':
        rows[0]['input']['lead_text'] = 'A replacement article.'
        rows[0]['input_fingerprint'] = digest(rows[0]['input'])
    elif mutation == 'baseline':
        rows[0]['evaluation']['eligible'] = not rows[0]['evaluation']['eligible']
    elif mutation == 'provenance':
        rows[0]['provenance']['manifest_id'] = '0' * 64
    else:
        rows[0]['input']['baseline_primary'] = 'Person'
        rows[0]['input_fingerprint'] = digest(rows[0]['input'])
    raw = ''.join(canonical(row) + '\n' for row in rows).encode('utf-8')
    (root / 'inputs.jsonl').write_bytes(raw)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    manifest['files']['inputs.jsonl'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    manifest['selection'] = [{'page_id': row['evaluation']['page_id'], 'row_hash': digest(row), 'input_fingerprint': row['input_fingerprint']} for row in rows]
    manifest.pop('manifest_id')
    manifest['manifest_id'] = digest(manifest)
    (root / 'manifest.json').write_text(canonical(manifest) + '\n', encoding='utf-8')
    with pytest.raises(Incompatible):
        dataset.load_dataset(root)


def test_changed_historical_snapshot_aborts_export(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    context['snapshot'] = '2027-01-01T00:00:00Z'
    with pytest.raises(Incompatible):
        dataset.prepare_dataset(context, tmp_path / 'forged', size=12)


def test_insufficient_trusted_articles_does_not_publish_partial_dataset(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    root = tmp_path / 'export'
    with pytest.raises(Incompatible):
        dataset.prepare_dataset(context, root, size=25)
    assert not root.exists()


def test_all_rare_verified_focus_examples_are_sampled_and_reported(tmp_path, monkeypatch, config):
    dataset = api()
    context, records = synthetic_archive(monkeypatch, config, count=360)
    for record in records.values():
        if record['classification']['outcome'] == 'completed':
            record['classification']['primary_entity_type'] = 'Person'
    for page_id, group in ((1, 'Event'), (4, 'Location'), (7, 'Science / Technology')):
        records[page_id]['classification']['primary_entity_type'] = group
    root = tmp_path / 'export'
    dataset.prepare_dataset(context, root, size=90)
    rows = dataset.load_dataset(root)
    assert {1, 4, 7}.issubset({row['evaluation']['page_id'] for row in rows})
    report = json.loads((root / 'sampling-report.json').read_text(encoding='utf-8'))
    fractions = {item['stratum']: item['selection_fraction'] for item in report['strata']}
    assert fractions['completed:Event'] == fractions['completed:Location'] == fractions['completed:Science / Technology'] == 1.0
    assert fractions['completed:Person'] < 0.25


def test_output_directory_cannot_modify_historical_raw_cache(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    context['raw_cache'] = str(tmp_path / 'historical-cache')
    root = Path(context['raw_cache']) / 'new-experiment'
    with pytest.raises(Incompatible):
        dataset.prepare_dataset(context, root, size=12)
    assert not root.exists()


def test_modern_discovery_hints_are_preserved_separately_from_experiment_buckets(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config, modern_hints={1: 'Event', 2: 'Science / Technology', 3: 'Location'})
    root = tmp_path / 'modern'
    dataset.prepare_dataset(context, root, size=24)
    rows = {row['evaluation']['page_id']: row for row in dataset.load_dataset(root)}
    assert rows[1]['evaluation']['sampling_stratum'] == 'Event'
    assert rows[1]['evaluation']['baseline_primary'] == 'Person'
    assert rows[1]['evaluation']['experiment_sampling_bucket'] == 'completed:Person'
    assert rows[2]['evaluation']['sampling_stratum'] == 'Science / Technology'
    assert rows[2]['evaluation']['experiment_sampling_bucket'] == 'unclassified:Science / Technology'
    assert rows[3]['evaluation']['sampling_stratum'] == 'Location'
    assert rows[3]['evaluation']['experiment_sampling_bucket'] == 'unresolved:Location'
    assert all(set(row['input']) == {'title', 'lead_text', 'sentences'} for row in rows.values())
    report = json.loads((root / 'sampling-report.json').read_text(encoding='utf-8'))
    assert report['strata_basis'] == 'evaluation.experiment_sampling_bucket'
    hints = {item['stratum']: item['selected'] for item in report['discovery_strata']}
    assert hints == {'Event': 1, 'Location': 1, 'Person': 21, 'Science / Technology': 1}


def test_unknown_outcomes_prioritize_modern_focus_hints(tmp_path, monkeypatch, config):
    dataset = api()
    hints = {i + 1: ('Person', 'Event', 'Location', 'Science / Technology')[(i // 3) % 4] for i in range(240)}
    context, _ = synthetic_archive(monkeypatch, config, count=240, modern_hints=hints)
    root = tmp_path / 'modern-focus'
    dataset.prepare_dataset(context, root, size=90)
    rows = dataset.load_dataset(root)
    for outcome in ('unclassified', 'unresolved'):
        selected = [row for row in rows if row['evaluation']['baseline_outcome'] == outcome]
        counts = {group: sum(row['evaluation']['sampling_stratum'] == group for row in selected)
                  for group in ('Person', 'Event', 'Location', 'Science / Technology')}
        assert counts['Person'] > 0
        assert all(counts[focus] > counts['Person'] for focus in ('Event', 'Location', 'Science / Technology'))


def test_legacy_discovery_origin_remains_a_fallback_hint(tmp_path, monkeypatch, config):
    dataset = api()
    context, _ = synthetic_archive(monkeypatch, config)
    root = tmp_path / 'legacy'
    dataset.prepare_dataset(context, root, size=24)
    rows = {row['evaluation']['page_id']: row for row in dataset.load_dataset(root)}
    assert rows[2]['evaluation']['sampling_stratum'] == 'Person'
    assert rows[2]['evaluation']['experiment_sampling_bucket'] == 'unclassified:Person'
