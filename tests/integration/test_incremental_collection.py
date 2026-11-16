from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import httpx
import pytest

from vimfc.domain import Incompatible, SourceUnavailable, SourceRevisionRef, RawRevision, canonical, digest
from vimfc.pipeline.collection_bundle import export_bundle, read_bundle, inside
from vimfc.pipeline.discovery_policy import seal_inventory
from vimfc.pipeline.incremental_collection import prepare_collection, collect_batch
from vimfc.pipeline.integrity_registry import IntegrityRegistry
from vimfc.pipeline.relaxed_archive import CheckedArchive
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAP = '2026-10-25T03:00:00Z'
HORIZON = '2026-10-26T03:00:00Z'
TEXT = "'''A''' là một nhà khoa học Việt Nam sinh năm 1980 tại [[B]]."


def inputs(tmp_path, config):
    root = tmp_path / 'original'; root.mkdir()
    cache = RawCache(root / 'raw'); cfg = {**config, 'discovery': {'version': 'seed-only-2'}}
    cfg['contact'] = 'fixture@example.com'
    with PipelineStore(root / 'db') as store:
        run = store.create(SNAP, cfg)
        for page, title in [(1, 'A'), (2, 'B'), (3, 'C')]:
            ref = SourceRevisionRef('viwiki', str(page), page, title, 100 + page, SNAP, SNAP, HORIZON)
            text = TEXT if page != 2 else 'B là một thành phố Việt Nam được thành lập năm 1900.'
            if page == 3:
                text = 'C là một thành phố Việt Nam được thành lập năm 1900 tại [[Article 4]].'
            raw_hash = hashlib.sha256(text.encode()).hexdigest()
            sha1 = hashlib.sha1(text.encode()).hexdigest()
            cache_ref = cache.put('viwiki', ref.revision_id, {'page_id': page, 'timestamp': SNAP, 'content': text,
                'content_model': 'wikitext', 'source_sha1': sha1, 'local_sha256': raw_hash})
            store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', str(page), SNAP, canonical(ref)))
            store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)', ('viwiki', ref.revision_id, canonical({'cache_ref': cache_ref})))
            if page == 1:
                store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)', (run, page, 0, canonical({'page_id': page, 'title': title})))
                acquired = asdict(RawRevision(ref, text, 'wikitext', sha1, raw_hash, cache_ref))
                store.complete(run, page, 'acquire', digest({'snapshot': SNAP, 'page_id': page}), acquired)
                store.complete(run, page, 'process', 'metadata-fixture', {'page_id': page, 'revision_id': ref.revision_id,
                    'raw_hash': raw_hash, 'snapshot': SNAP, 'classification': {'outcome': 'completed', 'primary_entity_type': 'Person', 'subtypes': []}})
            if page == 2:
                target = {'status': 'resolved', 'page_id': page, 'title': title, 'revision_id': ref.revision_id,
                    'revision_timestamp': SNAP, 'raw_hash': raw_hash, 'content': text, 'snapshot': SNAP,
                    'chain': [], 'proof': {'kind': 'historical_revision_and_move_log', 'move_index_hash': digest([])}}
                store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, title, canonical(target)))
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, '__move_index__:' + HORIZON, '[]'))
        with IntegrityRegistry(root / 'registry.sqlite', store, run, create=True):
            pass
        context = {'database': str(root / 'db'), 'raw_cache': str(root / 'raw'), 'run_id': run,
                   'snapshot': SNAP, 'config_hash': digest(cfg), 'cohort_hash': digest(store.candidates(run))}
    rows = [{'page_id': p, 'title': t, 'namespace': 0, 'strata': ['Person'], 'origins': [{'kind': 'seed'}],
             'candidate': {'page_id': p, 'title': t, 'discovery_metadata_only': True}}
            for p, t in [(1, 'A'), (2, 'B'), (3, 'C'), (4, 'Article 4'), (5, 'Article 5')]]
    inventory = seal_inventory({'format': 'vimfc-discovery-inventory-1', 'snapshot': SNAP,
        'semantic_rules': {k: digest(cfg[k]) for k in ('classification', 'extraction', 'ranking')},
        'seeds': rows, 'neighbors': []})
    inv_path = root / 'inventory.json'; inv_path.write_text(canonical(inventory), encoding='utf-8')
    return context, inv_path, root / 'registry.sqlite'


def prepared(tmp_path, config):
    context, inventory, registry = inputs(tmp_path, config)
    bundle, work = tmp_path / 'portable bundle', tmp_path / 'linux-workspace'
    export_bundle(context, inventory, registry, bundle)
    header = prepare_collection(bundle, work, target=20)
    return context, bundle, work, header


def factory(calls, *, missing=False, corrupt=False):
    def handler(request):
        calls.append(dict(request.url.params))
        assert request.url.host == 'vi.wikipedia.org'
        assert request.url.params['maxlag'] == '5'
        params = request.url.params
        page = int(params.get('pageids', int(params.get('revids', 104)) - 100))
        if params.get('rvstart'):
            data = {'pageid': page, 'title': 'Article ' + str(page), 'ns': 0,
                    'revisions': [] if missing else [{'revid': 100 + page, 'timestamp': SNAP}]}
        else:
            text = TEXT
            data = {'pageid': page, 'title': 'Article ' + str(page), 'ns': 0, 'revisions': [
                {'revid': 100 + page, 'timestamp': SNAP, 'sha1': 'wrong' if corrupt else hashlib.sha1(text.encode()).hexdigest(),
                 'slots': {'main': {'content': text, 'contentmodel': 'wikitext'}}}]}
        return httpx.Response(200, json={'curtimestamp': HORIZON, 'query': {'pages': [data]}})
    return lambda: MediaWikiClient('fixture@example.com', attempts=1, transport=httpx.MockTransport(handler))


def test_incremental_cached_acquire_offline_process_v2_and_metadata(tmp_path, config, monkeypatch):
    _, bundle, work, header = prepared(tmp_path, config)
    calls = []; client = factory(calls)
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    # The frozen ordering can contain uncached pages; explicitly check that prefix only.
    a = collect_batch(bundle, work, mode='acquire', through=5, batch_size=2, allow_network=True, client_factory=client)
    assert a['summary']['processed_this_invocation'] == 2
    while True:
        r = collect_batch(bundle, work, mode='acquire', through=5, batch_size=2, allow_network=True, client_factory=client)
        if r['summary']['processed_this_invocation'] == 0:
            break
    assert len(calls) == 4  # two new pages, selection + content; cached moves reused
    result = collect_batch(bundle, work, mode='process', through=5, batch_size=100)
    selection = result['selection']
    assert selection['policy'] == 'annotation-edge-1' and selection['ranking_policy'] == 'rank-annotation-1'
    assert selection['edges']['link_occurrences']['VERIFIED_EDGE'] == 0
    assert selection['edges']['link_occurrences']['CANDIDATE_EDGE'] > 0
    fresh = next(a for a in selection['articles'] if a['page_id'] == 3)['extraction']['links'][0]
    assert fresh['edge_status'] == 'CANDIDATE_EDGE'
    assert fresh['target']['proof']['kind'] == 'checked_collection_canonical_title'
    assert next(a for a in selection['articles'] if a['page_id'] == 1)['classification']['primary_entity_type'] == 'Person'
    replay = collect_batch(bundle, work, mode='process', through=5, batch_size=100)
    assert replay['selection'] == selection and len(calls) == 4
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert db.execute('SELECT count(*) FROM collection_items').fetchone()[0] == 5
    assert header['target'] == 20 and header['quality'] == 'quality-relaxed-1'


def test_request_cap_is_durable_and_partial_acquisition_resumes(tmp_path, config, monkeypatch):
    _, bundle, work, header = prepared(tmp_path, config)
    calls = []; client = factory(calls)
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    with pytest.raises(Incompatible, match='budget'):
        collect_batch(bundle, work, mode='acquire', through=5, batch_size=100, max_requests=1, allow_network=True, client_factory=client)
    assert len(calls) == 1
    r = collect_batch(bundle, work, mode='acquire', through=5, batch_size=100, max_requests=10, allow_network=True, client_factory=client)
    assert len(calls) == r['summary']['cumulative_requests'] == 4


def test_interruption_after_acquisition_reuses_stage_without_new_requests(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    import vimfc.pipeline.incremental_collection as module
    save = module._save; calls = []
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module, '_save', lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        collect_batch(bundle, work, mode='acquire', through=5, batch_size=1, allow_network=True, client_factory=factory(calls))
    before = len(calls)
    monkeypatch.setattr(module, '_save', save)
    collect_batch(bundle, work, mode='acquire', through=5, batch_size=1, allow_network=True, client_factory=factory(calls))
    assert len(calls) == before


def test_integrity_failure_is_sticky_and_corrupt_checkpoint_stops(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    calls = []
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    with pytest.raises(SourceUnavailable, match='SHA-1'):
        collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory(calls, corrupt=True))
    before = len(calls)
    with pytest.raises(Incompatible, match='fatal'):
        collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory(calls))
    assert len(calls) == before
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        db.execute("UPDATE collection_items SET record_hash='bad'")
    # Any already committed items are checksum checked before work or selection.
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        count = db.execute('SELECT count(*) FROM collection_items').fetchone()[0]
    if count:
        with pytest.raises(Incompatible, match='checksum'):
            collect_batch(bundle, work, mode='process', through=5)


def test_bundle_tampering_and_path_escape_are_rejected(tmp_path, config):
    context, inv, registry = inputs(tmp_path, config)
    bundle = tmp_path / 'bundle'
    export_bundle(context, inv, registry, bundle)
    with pytest.raises(Incompatible):
        inside(bundle, '../outside')
    with pytest.raises(Incompatible):
        inside(bundle, r'C:\data\cache')
    (bundle / 'inventory.json').write_bytes(b'bad')
    with pytest.raises(Incompatible, match='checksum'):
        prepare_collection(bundle, tmp_path / 'work')


def test_missing_history_is_explicit_and_not_a_quality_rejection(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    calls = []
    result = collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory(calls, missing=True))
    assert result['summary']['blocker'] is None and len(calls) == 2
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert db.execute("SELECT count(*) FROM collection_items WHERE status='missing'").fetchone()[0] == 2
    result = collect_batch(bundle, work, mode='process', through=5)
    assert len(result['selection']['articles']) == 3


def test_processed_raw_corruption_stops_before_new_selection(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    collect_batch(bundle, work, mode='process', through=5)
    next((work / 'raw-cache').rglob('*.gz')).write_bytes(b'corrupt')
    with pytest.raises((Incompatible, ValueError)):
        collect_batch(bundle, work, mode='process', through=5)


def test_duplicate_inventory_ids_and_source_overlap_refused(tmp_path, config):
    context, inv_path, registry = inputs(tmp_path, config)
    inv = json.loads(inv_path.read_text(encoding='utf-8'))
    inv['seeds'].append(inv['seeds'][0])
    inv_path.write_text(canonical(seal_inventory(inv)), encoding='utf-8')
    bundle = tmp_path / 'bundle'
    export_bundle(context, inv_path, registry, bundle)
    with pytest.raises(Incompatible, match='duplicate'):
        prepare_collection(bundle, tmp_path / 'work')
    with pytest.raises(Incompatible):
        export_bundle(context, inv_path, registry, Path(context['raw_cache']) / 'bundle')


def test_server_preflight_and_slurm_policy_are_portable(tmp_path, config, capsys):
    _, bundle, work, _ = prepared(tmp_path, config)
    import importlib.util
    spec = importlib.util.spec_from_file_location('collection_cli', 'scripts/collection-readiness.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    assert cli.main(['preflight', '--bundle', str(bundle), '--workspace-parent', str(tmp_path), '--min-free-gib', '0.001']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['network_status'] == 'NOT_PROBED' and result['gpu_required'] is False
    script = Path('configs/slurm/annotation-pilot10000.sbatch').read_text(encoding='utf-8')
    assert 'C:\\' not in script and '--gres' not in script and 'ssh ' not in script and 'sbatch ' not in script.replace('supplied to sbatch.', '')
    assert '--through 10000' in script and 'VIMFC_OFFLINE=1' in script and 'kill -TERM' in script
    assert '--signal=B:TERM@120' in script
    with pytest.raises(Incompatible, match='network authorization'):
        collect_batch(bundle, work, mode='acquire', through=5)


def test_single_writer_and_checkpoint_checksum(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    import vimfc.pipeline.incremental_collection as module
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    save = module._save
    def nested(store, status, record):
        with pytest.raises((Incompatible, OSError)):
            collect_batch(bundle, work, mode='process', through=5)
        return save(store, status, record)
    monkeypatch.setattr(module, '_save', nested)
    collect_batch(bundle, work, mode='acquire', through=5, batch_size=1, allow_network=True, client_factory=factory([]))
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        db.execute("UPDATE collection_items SET record_hash='tampered'")
    with pytest.raises(Incompatible, match='checksum'):
        collect_batch(bundle, work, mode='process', through=5)


def test_posix_lock_branch_does_not_import_windows_locking(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import vimfc.source.dump_download as locking
    calls = []
    monkeypatch.setattr(locking, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=8,
        flock=lambda stream, operation: calls.append(operation)))
    with locking.exclusive(tmp_path / 'writer.lock'):
        assert calls == [3]
    assert calls == [3, 8]


def test_portable_registry_refusal_verifies_without_original_artifact_reads(tmp_path, config, monkeypatch):
    context, inv, registry = inputs(tmp_path, config)
    from tests.integration.test_integrity_registry import observation, auth
    from tests.integration.test_reviewed_quarantine import review_file
    with PipelineStore(context['database']) as store:
        with IntegrityRegistry(registry, store, context['run_id']) as reg:
            approval, _ = review_file(tmp_path, store.run(context['run_id']))
            reg.review_refusal(observation(reg), approval, auth(tmp_path))
    bundle = tmp_path / 'bundle'
    export_bundle(context, inv, registry, bundle)
    relocated = tmp_path / 'other-linux-root'; shutil.copytree(bundle, relocated)
    manifest, source, resolve = read_bundle(relocated)
    original_read = Path.read_bytes
    def read_only_bundle(path):
        assert path.resolve().is_relative_to(relocated.resolve()), str(path)
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', read_only_bundle)
    with CheckedArchive(source, relocated / 'registry.sqlite', artifact_resolver=resolve) as archive:
        assert any(o['state'] == 'REVIEWED_REFUSAL' for o in archive.registry.observations.values())
