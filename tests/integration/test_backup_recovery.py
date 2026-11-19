"""Fixture-only transfer, restore ordering and authority recovery boundaries."""
import os
import subprocess
from types import SimpleNamespace

import pytest

from vimfc.domain import Incompatible
from vimfc.pipeline import backup_recovery as backup
from vimfc.pipeline import generation_publication as publication
from vimfc.pipeline import local_snapshot as snapshot
from vimfc.pipeline.transactional_control import SQLiteWitness, TransactionalAuthority
from test_generation_publication import all_bytes, generation, publish, workspace


@pytest.fixture
def history(tmp_path, workspace):
    bundle, work, identity = workspace
    source, compatible, one = generation(tmp_path, workspace)
    publish(tmp_path, workspace, source, compatible, one)
    second, compatible, two = generation(tmp_path, workspace, 'g2')
    two['target']['parent_generation'] = 'g1'
    two['expected_pointer'] = one['target']
    publish(tmp_path, workspace, second, compatible, two)
    authority = TransactionalAuthority(tmp_path / 'authority.sqlite', SQLiteWitness(tmp_path / 'witness.sqlite'),
                                       authority_id='a', budget_id='b', ceiling=100, historical_debit=50,
                                       runtime=compatible['runtime'],
                                       binding={k: identity[k] for k in ('workspace_id', 'run_id', 'bundle_id')})
    authority.provision()
    authority.decide('init', 0, 'initialize', {})
    return (tmp_path / 'storage', tmp_path / 'shared-fixture', tmp_path / 'fresh', work,
            dict(expected_current=two['target'], expected_previous=one['target'], synthetic=True),
            dict(bundle=bundle, compatible=compatible, authority=authority))


def test_two_generation_transfer_and_restore(history, monkeypatch):
    source, shared, fresh, work, expected, validation = history
    authority = validation['authority']
    before = all_bytes(source), all_bytes(work), all_bytes(validation['bundle']), authority.inspect()
    real_connect = snapshot.sqlite3.connect
    opens = []

    def connect(database, *args, **kwargs):
        opens.append(str(database))
        assert all(root.name not in str(database) for root in (source, shared))
        assert publication.verify_bytes(fresh, expected['expected_current'])
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(snapshot.sqlite3, 'connect', connect)
    backup.transfer_backup(source, source / '..' / shared.name, **expected,
                           protected_inputs=(work, validation['bundle'], authority.path, authority.witness.path))
    assert opens == []
    receipt = backup.restore_backup(shared, shared / '..' / fresh.name, **expected, **validation,
                                    protected_inputs=(work, source))
    assert opens and receipt['verified'] and receipt['generation_id'] == 'g2'
    assert receipt['previous'] == expected['expected_previous']
    assert receipt['selection'] == 'stale' and not receipt['selection_epoch_bound']
    assert all_bytes(fresh) == all_bytes(source / 'g2')
    assert all_bytes(shared / 'g1') == all_bytes(source / 'g1')
    assert (all_bytes(source), all_bytes(work), all_bytes(validation['bundle']), authority.inspect()) == before
    with pytest.raises(Incompatible, match='Fresh separate'):
        backup.restore_backup(shared, fresh, **expected, **validation)


def no_sqlite(monkeypatch):
    monkeypatch.setattr(snapshot.sqlite3, 'connect', lambda *a, **kw: pytest.fail('Unexpected SQLite open'))


@pytest.fixture
def containment(tmp_path, monkeypatch):
    roots = {name: tmp_path / name / 'protected' for name in ('source', 'bundle', 'explicit')}
    for root in roots.values():
        root.mkdir(parents=True)
        (root / 'sentinel').write_bytes(b'protected tree')
    for name in ('authority', 'witness'):
        path = tmp_path / name / 'control.sqlite'
        path.parent.mkdir()
        path.write_bytes(b'protected control')
        roots[name] = path
    authority = SimpleNamespace(path=roots['authority'], witness=SimpleNamespace(path=roots['witness']))
    monkeypatch.setattr(backup, '_pair', lambda *_: pytest.fail('Read generation before guarding output'))
    no_sqlite(monkeypatch)
    target = {'identity': {k: 'fixture' for k in publication.IDENTITY_KEYS},
              'manifest_sha256': 'a' * 64, 'parent_generation': None}

    def run(operation, output, explicit):
        params = dict(expected_current=target, protected_inputs=(explicit,), synthetic=True)
        if operation == 'transfer':
            return backup.transfer_backup(roots['source'], output, **params)
        return backup.restore_backup(roots['source'], output, bundle=roots['bundle'],
                                     authority=authority, compatible={}, **params)

    return roots, run


@pytest.mark.parametrize('operation,role', [
    ('transfer', 'source'), ('transfer', 'explicit'),
    ('restore', 'source'), ('restore', 'bundle'), ('restore', 'authority'),
    ('restore', 'witness'), ('restore', 'explicit'),
])
@pytest.mark.parametrize('relation', ['equal', 'nested', 'ancestor'])
def test_output_overlap_rejected_before_side_effects(tmp_path, containment, operation, role, relation):
    roots, run = containment
    protected = roots[role]
    output = {'equal': protected, 'nested': protected / 'new' / 'output',
              'ancestor': protected.parent}[relation]
    alias = roots['explicit'].parent / '..' / output.relative_to(tmp_path)
    entries = set(tmp_path.rglob('*'))
    files = all_bytes(tmp_path)
    with pytest.raises(Incompatible, match='protected input overlap'):
        run(operation, alias, roots['explicit'])
    assert set(tmp_path.rglob('*')) == entries
    assert all_bytes(tmp_path) == files


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_link_aliases_rejected_on_both_sides(tmp_path, containment, kind):
    roots, run = containment
    protected = roots['explicit']
    alias = tmp_path / 'alias'
    if kind == 'junction':
        if os.name != 'nt':
            pytest.skip('Windows junction fixture')
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(protected)],
                       check=True, capture_output=True)
    else:
        try:
            alias.symlink_to(protected, target_is_directory=True)
        except OSError:
            pytest.skip('Symlink privilege unavailable')
    entries = set(tmp_path.rglob('*'))
    files = all_bytes(tmp_path)
    try:
        for operation in ('transfer', 'restore'):
            for output in (alias / 'new' / 'output', alias / '..' / 'fresh'):
                with pytest.raises(Incompatible, match='Symlink/junction'):
                    run(operation, output, protected)
            with pytest.raises(Incompatible, match='Symlink/junction'):
                run(operation, tmp_path / 'fresh', alias)
        assert set(tmp_path.rglob('*')) == entries
        assert all_bytes(tmp_path) == files
    finally:
        if kind == 'junction':
            alias.rmdir()
        else:
            alias.unlink()


@pytest.mark.parametrize('boundary', ['during_copy', 'before_backup_verification', 'before_pointer_replace'])
def test_interrupted_transfer_leaves_no_current(history, monkeypatch, boundary):
    source, shared, fresh, _, expected, validation = history
    before = all_bytes(source)
    no_sqlite(monkeypatch)

    def interrupt(point):
        if point == boundary:
            raise OSError('interrupted')

    with pytest.raises(OSError, match='interrupted'):
        backup.transfer_backup(source, shared, **expected, fault=interrupt)
    assert publication.current(shared) is None and all_bytes(source) == before
    with pytest.raises(Incompatible, match='pointer'):
        backup.restore_backup(shared, fresh, **expected, **validation)


@pytest.mark.parametrize('damage', [
    'parent', 'stale', 'missing', 'extra', 'empty-dir', 'copy', 'pointer', 'runtime', 'interrupt', 'pointer-race',
])
def test_pre_sqlite_rejections_never_fall_back(history, monkeypatch, damage):
    source, shared, fresh, _, expected, validation = history
    backup.transfer_backup(source, shared, **expected)
    if damage == 'parent':
        (shared / 'g1' / 'pipeline.sqlite').write_bytes(b'corrupt')
    elif damage == 'stale':
        expected = dict(expected, expected_current=expected['expected_previous'], expected_previous=None)
    elif damage == 'missing':
        (shared / 'g2' / 'workspace.json').unlink()
    elif damage == 'extra':
        (shared / 'g2' / 'pipeline.sqlite-wal').touch()
    elif damage == 'empty-dir':
        (shared / 'g2' / 'extra').mkdir()
    elif damage == 'pointer':
        publication.atomic_json(shared / 'latest.json', expected['expected_previous'],
                                expected_pointer=expected['expected_current'])
    elif damage == 'runtime':
        validation['compatible'] = dict(validation['compatible'], runtime='wrong')

    before = all_bytes(shared)
    no_sqlite(monkeypatch)

    def corrupt_copy(point):
        if damage == 'interrupt' and point == 'during_copy' and any(fresh.iterdir()):
            raise OSError('Interrupted restore')
        if damage == 'copy' and point == 'before_restore_verification':
            (fresh / 'pipeline.sqlite').write_bytes(b'broken in transit')
        if damage == 'pointer-race' and point == 'before_restore_verification':
            publication.atomic_json(shared / 'latest.json', expected['expected_previous'],
                                    expected_pointer=expected['expected_current'])

    with pytest.raises(OSError if damage == 'interrupt' else Incompatible):
        backup.restore_backup(shared, fresh, **expected, **validation, fault=corrupt_copy)
    if damage == 'pointer-race':
        assert publication.current(shared) == expected['expected_previous']
        del before['latest.json']
        after = all_bytes(shared)
        del after['latest.json']
        assert after == before
        return
    assert all_bytes(shared) == before


@pytest.mark.parametrize('field,error', [('checkpoint', 'checkpoint binding'), ('compatibility', 'SQLite schema')])
def test_local_database_checks_even_with_matching_trusted_digest(history, field, error):
    source, shared, fresh, _, expected, validation = history
    backup.transfer_backup(source, shared, **expected)
    path = shared / 'g2' / 'manifest.json'
    manifest = publication.read_canonical(path)
    if field == 'checkpoint':
        manifest[field]['attempts'] += 1
    else:
        manifest[field]['schema_hash'] = '0' * 64
        validation['compatible'] = manifest[field]
    publication.atomic_json(path, manifest)
    target = dict(expected['expected_current'], manifest_sha256=snapshot.file_hash(path))
    publication.atomic_json(shared / 'latest.json', target, expected_pointer=expected['expected_current'])
    expected['expected_current'] = target
    with pytest.raises(Incompatible, match=error):
        backup.restore_backup(shared, fresh, **expected, **validation)


@pytest.mark.parametrize('pending_ack', [False, True])
def test_prepared_authority_blocks_without_recovery(history, monkeypatch, pending_ack):
    source, shared, fresh, _, expected, validation = history
    backup.transfer_backup(source, shared, **expected)
    a = validation['authority']
    a.decide('claim', 1, 'claim', dict(operator='op', job='job', attempt='new', epoch=1,
                                       fencing_evidence='fixture'))
    a.decide('reserve', 2, 'reserve', dict(reservation_id='r', attempt='new', allowance=10))
    a.launch('launch', 3, attempt='new', epoch=1, job='job', reservation_id='r', ttl=60,
             mock_transport=pytest.fail)
    a.decide('stop', 4, 'stop', {})
    target = expected['expected_previous']
    target = dict(target, identity=dict(target['identity'], attempt_id='new', epoch_id='1'))
    a.decide('authorize', 5, 'authorize_generation', dict(target=target, expected_pointer=None))
    a.decide('publish', 6, 'publish', {})

    if pending_ack:
        def crash(point):
            if point == 'after_commit':
                raise OSError('interrupted prepare')

        a.checkpoint = crash
        with pytest.raises(OSError, match='interrupted'):
            a.decide('prepare', 7, 'prepare', {})
    else:
        a.decide('prepare', 7, 'prepare', {})
    before = a.inspect(), a.path.read_bytes(), a.witness.path.read_bytes(), all_bytes(shared)
    real_connect = snapshot.sqlite3.connect

    def connect(database, *args, **kwargs):
        assert all(root.name not in str(database) for root in (source, shared, fresh))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(snapshot.sqlite3, 'connect', connect)
    with pytest.raises(Incompatible, match='acknowledged authority|Unresolved publication intent'):
        backup.restore_backup(shared, fresh, **expected, **validation)
    assert a.inspect()['state']['allocated'] == 60
    assert (a.inspect(), a.path.read_bytes(), a.witness.path.read_bytes(), all_bytes(shared)) == before

