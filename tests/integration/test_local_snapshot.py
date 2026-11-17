from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from vimfc.domain import Incompatible, canonical
from vimfc.pipeline import local_snapshot as s
from vimfc.pipeline.incremental_collection import collect_batch
from test_incremental_collection import prepared, factory


@pytest.fixture
def workspace(tmp_path, config, monkeypatch):
    _, bundle, work, header = prepared(tmp_path, config)
    monkeypatch.setattr('vimfc.pipeline.incremental_collection.time.sleep', lambda _: None)
    collect_batch(bundle, work, mode='acquire', through=5, batch_size=100,
                  allow_network=True, client_factory=factory([]))
    collect_batch(bundle, work, mode='process', through=5, batch_size=100)
    identity = dict(workspace_id='synthetic-workspace', run_id=header['context']['run_id'],
                    bundle_id=header['bundle_id'], attempt_id='synthetic-attempt', epoch_id='epoch-1', generation_id='generation-1')
    return bundle, work, identity


def capture(tmp_path, workspace):
    bundle, work, identity = workspace
    generation = tmp_path / 'generation'
    result = s.snapshot(work, generation, bundle=bundle, identity=identity, runtime='synthetic-runtime-1',
                        stopped_writer=True, synthetic=True)
    return generation, result


def test_snapshot_restore_source_preserved(tmp_path, workspace):
    bundle, work, _ = workspace
    before = s.inventory(work)
    bundle_before = s.inventory(bundle)
    generation, result = capture(tmp_path, workspace)
    assert result['receipt']['records'] == 5
    assert result['receipt']['selection'] == 'stale'
    with closing(s.readonly(generation / 'pipeline.sqlite')) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    # immutable readers report DELETE even for WAL headers; inspect persisted format.
    assert (work / 'pipeline.sqlite').read_bytes()[18:20] == b'\x02\x02'
    receipt = s.restore(generation, tmp_path / 'restore', bundle=bundle, synthetic=True,
                        trusted=result['trusted'], compatible=result['compatible'])
    assert receipt == result['receipt']
    assert s.inventory(work) == before
    assert s.inventory(bundle) == bundle_before
    s.no_sidecars(generation / 'pipeline.sqlite')
    (tmp_path / 'verification-receipts.json').write_text(canonical({
        'snapshot':result, 'restore':receipt, 'source_unchanged':True}), encoding='utf-8')


@pytest.mark.parametrize('fault', ['missing_raw','corrupt_raw','sqlite','hash','path','binding','schema','runtime','partial','symlink'])
def test_reject_invalid_generation(tmp_path, workspace, fault):
    bundle, work, _ = workspace
    before = s.inventory(work)
    generation, result = capture(tmp_path, workspace)
    trusted = dict(result['trusted']); compatible = dict(result['compatible'])
    manifest = s.read_json(generation / 'manifest.json')
    raw = next((generation / 'raw-cache').rglob('*.gz'))
    if fault == 'missing_raw': raw.unlink()
    if fault == 'corrupt_raw': raw.write_bytes(b'broken')
    if fault == 'sqlite': (generation / 'pipeline.sqlite').write_bytes(b'bad sqlite')
    if fault == 'hash': trusted['manifest_sha256'] = '0' * 64
    if fault == 'path': manifest['artifacts']['../escape'] = {'bytes':0,'sha256':'0'*64}
    if fault == 'binding': manifest['identity']['run_id'] = 'wrong'
    if fault == 'schema': compatible['user_version'] = 99
    if fault == 'runtime': compatible['runtime'] = 'unknown'
    if fault == 'partial': (generation / 'workspace.json').unlink()
    if fault == 'symlink':
        try: (generation / 'link').symlink_to(work / 'workspace.json')
        except OSError: pytest.skip('Symlink privilege unavailable on Windows')
    if fault in ('path','binding'):
        (generation / 'manifest.json').write_text(canonical(manifest), encoding='utf-8')
        trusted['manifest_sha256'] = hashlib.sha256((generation / 'manifest.json').read_bytes()).hexdigest()
    with pytest.raises((Incompatible, ValueError, sqlite3.Error)):
        s.restore(generation, tmp_path / 'restore', bundle=bundle, synthetic=True,
                  trusted=trusted, compatible=compatible)
    assert not (tmp_path / 'restore').exists()
    assert s.inventory(work) == before


@pytest.mark.parametrize('fault', ['raw','header','counter','checkpoint'])
def test_consistency_not_just_file_hashes(tmp_path, workspace, fault):
    bundle, work, _ = workspace
    generation, result = capture(tmp_path, workspace)
    if fault == 'raw':
        next((generation / 'raw-cache').rglob('*.gz')).unlink()
    elif fault == 'header':
        header = s.read_json(generation / 'workspace.json')
        header['context']['snapshot'] = '2099-01-01T00:00:00Z'
        (generation / 'workspace.json').write_text(canonical(header))
    else:
        with closing(sqlite3.connect(generation / 'pipeline.sqlite')) as db:
            if fault == 'counter': db.execute("UPDATE collection_control SET value='-1' WHERE key='attempts'")
            else: db.execute("UPDATE collection_items SET record_hash='wrong'")
            db.commit()
    manifest = s.read_json(generation / 'manifest.json')
    manifest['artifacts'] = s.inventory(generation)
    (generation / 'manifest.json').write_text(canonical(manifest))
    trusted = {**result['trusted'], 'manifest_sha256': hashlib.sha256((generation / 'manifest.json').read_bytes()).hexdigest()}
    with pytest.raises((Incompatible, ValueError)):
        s.verify_generation(generation, trusted=trusted, bundle=bundle, compatible=result['compatible'])


def test_stopped_boundary_interruption_and_network_fs(tmp_path, workspace, monkeypatch):
    bundle, work, identity = workspace
    before = s.inventory(work)
    with pytest.raises(Incompatible):
        s.snapshot(work, tmp_path / 'no-boundary', bundle=bundle, identity=identity, runtime='fixture', synthetic=True)
    original = s.verify_workspace
    def interrupted(*args): raise KeyboardInterrupt()
    monkeypatch.setattr(s, 'verify_workspace', interrupted)
    with pytest.raises(KeyboardInterrupt):
        s.snapshot(work, tmp_path / 'interrupted', bundle=bundle, identity=identity, runtime='fixture', synthetic=True, stopped_writer=True)
    assert not (tmp_path / 'interrupted/manifest.json').exists()
    assert s.inventory(work) == before
    monkeypatch.setattr(s, 'verify_workspace', original)
    monkeypatch.setattr(s, 'filesystem', lambda _: 'nfs4')
    monkeypatch.setattr(s.sqlite3, 'connect', lambda *a, **k: pytest.fail('SQLite opened on NFS'))
    with pytest.raises(Incompatible, match='network'):
        s.readonly(work / 'pipeline.sqlite')


def test_absent_selection_orphans_and_pending_wal(tmp_path, workspace):
    bundle, work, identity = workspace
    (work / 'selection.json').unlink()
    orphan = work / 'raw-cache/orphan-fixture.bin'
    orphan.write_bytes(b'preserve orphan')
    generation, result = capture(tmp_path, workspace)
    assert result['receipt']['selection'] == 'absent'
    assert (generation / 'raw-cache/orphan-fixture.bin').read_bytes() == orphan.read_bytes()
    Path(str(work / 'pipeline.sqlite') + '-wal').write_bytes(b'pending')
    before = s.inventory(work)
    with pytest.raises(Incompatible, match='sidecars'):
        s.snapshot(work, tmp_path / 'refused', bundle=bundle, identity=identity,
                   runtime='fixture', synthetic=True, stopped_writer=True)
    assert s.inventory(work) == before


@pytest.mark.parametrize('fault', ['version','duplicate','size','extra'])
def test_manifest_structure(tmp_path, workspace, fault):
    bundle, _, _ = workspace
    generation, result = capture(tmp_path, workspace)
    manifest = s.read_json(generation / 'manifest.json')
    if fault == 'version': manifest['format'] = 'generation-manifest-v99'
    if fault == 'size': manifest['artifacts']['pipeline.sqlite']['bytes'] = True
    if fault == 'extra': manifest['extra'] = 1
    text = canonical(manifest)
    if fault == 'duplicate': text = text.replace('{', '{"format":"duplicate",', 1)
    (generation / 'manifest.json').write_text(text, encoding='utf-8')
    trusted = {**result['trusted'], 'manifest_sha256':hashlib.sha256(text.encode()).hexdigest()}
    with pytest.raises(Incompatible):
        s.verify_generation(generation, trusted=trusted, compatible=result['compatible'], bundle=bundle)


@pytest.mark.parametrize('operation', ['snapshot', 'restore'])
@pytest.mark.parametrize('tree', ['source', 'bundle', 'explicit'])
@pytest.mark.parametrize('relation', ['nested', 'dotdot', 'ancestor', 'same'])
def test_destination_overlap_rejected_before_io(tmp_path, workspace, monkeypatch,
                                                operation, tree, relation):
    bundle, work, identity = workspace
    generation, result = capture(tmp_path, workspace)
    explicit = tmp_path / 'protected'
    explicit.mkdir()
    (explicit / 'sentinel').write_bytes(b'protected input')
    source = work if operation == 'snapshot' else generation
    root = {'source': source, 'bundle': bundle, 'explicit': explicit}[tree]
    output = {'nested': root / 'new-parent/output',
              'dotdot': root / '../' / root.name / 'new-parent/output',
              'ancestor': root.parent, 'same': root}[relation]
    # Include manifests as protected bytes (inventory deliberately omits them).
    before = {p: p.read_bytes() for r in (work, bundle, generation, explicit)
              for p in r.rglob('*') if p.is_file()}
    before_paths = set(tmp_path.rglob('*'))
    monkeypatch.setattr(s, 'verify_workspace', lambda *a: pytest.fail('Input audit before destination rejection'))
    monkeypatch.setattr(s, 'verify_generation', lambda *a, **k: pytest.fail('Generation opened before destination rejection'))
    monkeypatch.setattr(s.shutil, 'copytree', lambda *a, **k: pytest.fail('Unsafe copy'))
    with pytest.raises(Incompatible, match='separate'):
        if operation == 'snapshot':
            s.snapshot(work, output, bundle=bundle, identity=identity, runtime='fixture',
                       synthetic=True, stopped_writer=True, protected_inputs=(explicit,))
        else:
            s.restore(generation, output, bundle=bundle, trusted=result['trusted'],
                      compatible=result['compatible'], synthetic=True, protected_inputs=(explicit,))
    assert set(tmp_path.rglob('*')) == before_paths
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_destination_link_alias_rejected(tmp_path, workspace, kind):
    import os
    import subprocess
    bundle, work, identity = workspace
    alias = tmp_path / 'alias'
    if kind == 'junction':
        if os.name != 'nt': pytest.skip('Windows junction fixture')
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(bundle)], check=True,
                       capture_output=True)
    else:
        try: alias.symlink_to(bundle, target_is_directory=True)
        except OSError: pytest.skip('Symlink privilege unavailable')
    before = s.inventory(bundle)
    try:
        with pytest.raises(Incompatible, match='Symlink/junction'):
            s.snapshot(work, alias / 'new/output', bundle=bundle, identity=identity,
                       runtime='fixture', synthetic=True, stopped_writer=True)
        with pytest.raises(Incompatible, match='Symlink/junction'):
            s.restore(work, alias / 'new/output', bundle=bundle, trusted={},
                      compatible={}, synthetic=True)
        assert s.inventory(bundle) == before
        assert not (bundle / 'new').exists()
    finally:
        if kind == 'junction': alias.rmdir()
        else: alias.unlink()

