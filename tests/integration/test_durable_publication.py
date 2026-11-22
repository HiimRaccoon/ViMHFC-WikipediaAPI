"""Hard process exits and two epochs sharing the same durable fixture control files."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Event

import pytest

from vimfc.domain import Incompatible, canonical
from vimfc.pipeline import backup_recovery, generation_publication as publication
from vimfc.pipeline.durable_publication import (
    DurablePublication, FixtureQuiescence, OperatorDispositionRequired, QuiescenceEvidence, capture_generation,
)
from vimfc.pipeline.transactional_control import SQLiteWitness, TransactionalAuthority
from vimfc.pipeline.worker_activation import SyntheticActivation, SyntheticTermination
from test_generation_publication import all_bytes, workspace


def open_authority(root, binding):
    return TransactionalAuthority(
        root / 'authority.sqlite', SQLiteWitness(root / 'witness.sqlite'),
        authority_id='fixture', binding=binding, runtime='fixture', budget_id='budget',
        ceiling=10, historical_debit=2)


def decide(a, action, payload):
    with closing(sqlite3.connect(a.path)) as db:
        version = db.execute('SELECT count(*) FROM records').fetchone()[0]
    return a.decide(f'test:{version}', version, action, payload)


def quiescence(root, epoch):
    evidence = QuiescenceEvidence('operator', f'job{epoch}', f'a{epoch}', epoch)
    return FixtureQuiescence(evidence, lambda candidate: candidate == evidence
                             and (root / 'fixture-drained').read_text() == str(epoch))


def coordinator(root, spec, a=None, **kwargs):
    a = a or open_authority(root, spec['binding'])
    return DurablePublication(
        a, root / 'published', target=spec['target'], prior=spec['prior'],
        compatible=spec['compatible'], bundle=spec['bundle'], attempt=f"a{spec['epoch']}",
        epoch=spec['epoch'], job=f"job{spec['epoch']}", quiescence=quiescence(root, spec['epoch']), **kwargs)


@pytest.fixture
def prepared(tmp_path, workspace):
    bundle, work, identity = workspace
    binding = {k: identity[k] for k in ('workspace_id', 'run_id', 'bundle_id')}
    a = open_authority(tmp_path, binding)
    a.provision()
    decide(a, 'initialize', {})
    decide(a, 'claim', dict(operator='operator', job='job1', attempt='a1', epoch=1, fencing_evidence='fixture'))
    decide(a, 'reserve', dict(reservation_id='r1', attempt='a1', allowance=3))
    worker = a.launch('launch1', 3, attempt='a1', epoch=1, job='job1', reservation_id='r1', ttl=60,
                      mock_transport=lambda units: units)
    assert worker('request1', 4, 1) == 1
    decide(a, 'stop', {})
    (tmp_path / 'fixture-drained').write_text('1')
    source = tmp_path / 'source1'
    result = capture_generation(a, work, source, bundle=bundle, generation_id='g1',
                                quiescence=quiescence(tmp_path, 1))
    target = dict(result['trusted'], parent_generation=None)
    decide(a, 'authorize_generation', dict(target=target, expected_pointer=None))
    spec = dict(binding=binding, target=target, prior=None, compatible=result['compatible'],
                bundle=str(bundle), epoch=1, source=str(source))
    (tmp_path / 'fixture-inputs.json').write_text(canonical(spec))
    return spec, work


def _child(root, boundary):
    def denied(*_args, **_kwargs):
        raise AssertionError('Network forbidden in crash fixture')

    socket.socket.connect = denied
    spec = json.loads((root / 'fixture-inputs.json').read_text())
    a = open_authority(root, spec['binding'])
    original = a._decide

    def terminate(point):
        if point == boundary:
            os._exit(71)

    def wrapped(ident, version, action, payload, *args, **kwargs):
        a.checkpoint = lambda point: terminate(action + ':' + point)
        return original(ident, version, action, payload, *args, **kwargs)

    a._decide = wrapped

    def fault(point):
        if point == 'during_receipt' and publication.current(root / 'published') != spec['target']:
            return
        terminate(point)

    coordinator(root, spec, a, fault=fault).publish(spec['source'])


def crash(root, boundary):
    repo = Path(__file__).resolve().parents[2]
    code = ("import runpy,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "sys.path.insert(0,str(Path(sys.argv[2]).parent)); "
            "runpy.run_path(sys.argv[2])['_child'](Path(sys.argv[3]),sys.argv[4])")
    result = subprocess.run([sys.executable, '-B', '-c', code, str(repo / 'backend/src'),
                             __file__, str(root), boundary], capture_output=True, text=True, timeout=60)
    assert result.returncode == 71, result.stderr


@pytest.mark.parametrize('boundary,outcome', [
    ('prepare:before_stage', 'OPERATOR'),
    ('prepare:before_commit', 'BLOCKED'),
    ('prepare:after_commit', 'OPERATOR'),
    ('prepare:before_ack', 'OPERATOR'),
    ('prepare:after_ack', 'OPERATOR'),
    ('after_prepared_ack', 'OPERATOR'),
    ('before_promotion', 'OPERATOR'),
    ('after_promotion', 'OPERATOR'),
    ('before_pointer_replace', 'OPERATOR'),
    ('after_pointer_replace', 'COMMITTED'),
    ('during_receipt', 'COMMITTED'),
    ('before_reconciliation_store', 'COMMITTED'),
    ('reconcile:before_commit', 'BLOCKED'),
    ('reconcile:after_commit', 'COMMITTED'),
    ('reconcile:before_ack', 'COMMITTED'),
    ('reconcile:after_ack', 'COMMITTED'),
])
def test_process_exit_recovery(prepared, tmp_path, boundary, outcome):
    spec, _ = prepared
    crash(tmp_path, boundary)
    c = coordinator(tmp_path, spec)
    files = all_bytes(tmp_path / 'published') if (tmp_path / 'published').exists() else {}
    if outcome == 'COMMITTED':
        assert c.recover() == 'COMMITTED'
        before = c.authority.path.read_bytes(), c.authority.witness.path.read_bytes()
        assert coordinator(tmp_path, spec).recover() == 'COMMITTED'
        assert (c.authority.path.read_bytes(), c.authority.witness.path.read_bytes()) == before
        state = c.authority.inspect()['state']
        assert state['owner'] == dict(state='RECONCILED', operator='operator', job='job1', attempt='a1', epoch=1)
        assert state['current'] == spec['target'] and state['previous'] is None
        assert state['allocated'] == 5 and state['spent'] == {'r1': 1}
    else:
        error = OperatorDispositionRequired if outcome == 'OPERATOR' else Incompatible
        with pytest.raises(error):
            c.recover()
        with pytest.raises(Incompatible):
            c.publish(spec['source'])
    assert (all_bytes(tmp_path / 'published') if (tmp_path / 'published').exists() else {}) == files


@pytest.mark.parametrize('damage', ['bytes', 'manifest', 'pointer', 'compatibility', 'ancestry', 'owner',
                                   'quiescence', 'witness'])
def test_pending_commit_reverifies_before_ack(prepared, tmp_path, damage):
    spec, _ = prepared
    crash(tmp_path, 'reconcile:after_commit')
    c = coordinator(tmp_path, spec)
    if damage == 'bytes':
        (tmp_path / 'published/g1/workspace.json').write_bytes(b'corrupt')
    elif damage == 'manifest':
        (tmp_path / 'published/g1/manifest.json').write_bytes(b'{}')
    elif damage == 'pointer':
        (tmp_path / 'published/latest.json').unlink()
    elif damage == 'compatibility':
        c.verification.compatible['schema_hash'] = '0' * 64
    elif damage == 'ancestry':
        c.verification.target['parent_generation'] = 'untrusted-parent'
    elif damage == 'owner':
        c.verification.identity = ('a1', 1, 'wrong-job')
    elif damage == 'quiescence':
        (tmp_path / 'fixture-drained').write_text('uncertain')
    else:
        c.authority.witness.available = False
    before = c.authority.witness.path.read_bytes()
    with pytest.raises(Incompatible):
        c.authority.recover()
    with pytest.raises(Incompatible):
        c.recover()
    assert c.authority.witness.path.read_bytes() == before


def test_bare_reconciliation_and_receipt_cannot_commit(prepared, tmp_path):
    spec, _ = prepared
    crash(tmp_path, 'after_pointer_replace')
    a = open_authority(tmp_path, spec['binding'])
    payload = dict(observed_pointer=spec['target'], verified=True)
    with pytest.raises(Incompatible, match='Checked publication'):
        a._decide('unchecked', 10, 'reconcile', payload)
    (tmp_path / 'published/g1/workspace.json').write_bytes(b'bad')
    publication.atomic_json(tmp_path / 'published/caller-receipt.json', dict(state='COMMITTED', verified=True))
    with pytest.raises(Incompatible):
        coordinator(tmp_path, spec).recover()
    assert a.inspect()['state']['publication']['state'] == 'PREPARED'


@pytest.mark.parametrize('point', ['before_reconciliation_store', 'before_ack'])
def test_live_commit_rechecks_storage_and_ack_boundaries(prepared, tmp_path, point):
    spec, _ = prepared
    crash(tmp_path, 'after_pointer_replace')
    c = coordinator(tmp_path, spec)

    def damage():
        (tmp_path / 'published/g1/workspace.json').write_bytes(b'changed after verification')

    if point == 'before_ack':
        c.authority.checkpoint = lambda current: damage() if current == point else None
    else:
        c.fault = lambda current: damage() if current == point else None
    with pytest.raises(Incompatible):
        c.recover()
    head = c.authority.witness.read(c.authority.genesis)
    assert head[0] == 9
    assert (head[2] is not None) == (point == 'before_ack')
    state = c.authority.inspect()['state']
    assert state['allocated'] == 5 and state['spent'] == {'r1': 1}


@pytest.mark.parametrize('condition', ['quiescence', 'authorization', 'revoked'])
def test_publish_denies_unverified_preconditions(prepared, tmp_path, condition):
    spec, _ = prepared
    c = coordinator(tmp_path, spec)
    if condition == 'quiescence':
        c.verification.quiescence = 'caller says stopped'
    elif condition == 'authorization':
        c.verification.target['manifest_sha256'] = '0' * 64
    else:
        def revoke(point):
            if point == 'before_pointer_replace':
                decide(c.authority, 'unknown', {'reason': 'Fixture revocation'})

        c.fault = revoke
    with pytest.raises(Incompatible):
        c.publish(spec['source'])
    assert publication.current(tmp_path / 'published') is None
    assert c.authority.inspect()['state']['allocated'] == 5


def test_reconciliation_never_opens_published_sqlite(prepared, tmp_path, monkeypatch):
    spec, _ = prepared
    connect = sqlite3.connect

    def guarded(database, *args, **kwargs):
        assert 'published' not in str(database)
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', guarded)
    assert coordinator(tmp_path, spec).publish(spec['source']) == 'COMMITTED'


def test_logical_stop_with_admitted_work_cannot_capture_or_publish(prepared, tmp_path):
    spec, work = prepared
    root = tmp_path / 'inflight'
    root.mkdir()
    a = open_authority(root, spec['binding'])
    a.provision()
    decide(a, 'initialize', {})
    decide(a, 'claim', dict(operator='operator', job='job1', attempt='a1', epoch=1, fencing_evidence='fixture'))
    decide(a, 'reserve', dict(reservation_id='r1', attempt='a1', allowance=3))
    c = coordinator(root, spec, a)
    entered, release = Event(), Event()

    def admitted_mock(_units):
        entered.set()
        assert release.wait(15)

    worker = a.launch('launch1', 3, attempt='a1', epoch=1, job='job1', reservation_id='r1',
                      ttl=60, mock_transport=admitted_mock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(worker, 'inflight-request', 4, 1)
        try:
            assert entered.wait(5)
            decide(a, 'stop', {})
            decide(a, 'authorize_generation', dict(target=spec['target'], expected_pointer=None))
            evidence = QuiescenceEvidence('operator', 'job1', 'a1', 1)
            q = FixtureQuiescence(evidence, lambda _: pending.done())
            with pytest.raises(Incompatible, match='not quiescent'):
                capture_generation(c.authority, work, tmp_path / 'denied', bundle=spec['bundle'],
                                   generation_id='denied', quiescence=q)
            c.verification.quiescence = q
            with pytest.raises(Incompatible, match='not quiescent'):
                c.publish(spec['source'])
            assert not (tmp_path / 'denied').exists()
            assert not (root / 'published').exists()
        finally:
            release.set()
        pending.result(timeout=5)


def test_two_epochs_same_control_files_restore_activate_and_publish(prepared, tmp_path):
    spec1, work = prepared
    protected = all_bytes(work), all_bytes(Path(spec1['bundle']))
    crash(tmp_path, 'after_pointer_replace')
    c = coordinator(tmp_path, spec1)
    assert c.recover() == 'COMMITTED'
    first_bytes = all_bytes(tmp_path / 'published/g1')
    a = c.authority
    decide(a, 'release', {})
    decide(a, 'claim', dict(operator='operator', job='job2', attempt='a2', epoch=2, fencing_evidence='fixture'))
    decide(a, 'reserve', dict(reservation_id='r2', attempt='a2', allowance=3))
    backup = tmp_path / 'backup'
    backup_recovery.transfer_backup(tmp_path / 'published', backup, expected_current=spec1['target'], synthetic=True)
    evidence = SyntheticTermination('operator', 'job1', 'a1', 1, True)
    activation = SyntheticActivation(
        a, expected_current=spec1['target'], compatible=spec1['compatible'],
        verify_termination=lambda candidate: candidate is evidence and
        (tmp_path / 'fixture-drained').read_text() == '1')
    restored, worker = activation.activate(
        'activate2', 13, source=backup, output=tmp_path / 'work2', bundle=spec1['bundle'], evidence=evidence,
        attempt='a2', epoch=2, job='job2', reservation_id='r2', ttl=60, mock_transport=lambda units: units)
    assert restored['selection'] == 'stale'
    assert worker('request2', 14, 1) == 1
    decide(a, 'stop', {})
    (tmp_path / 'fixture-drained').write_text('2')
    result = capture_generation(a, tmp_path / 'work2', tmp_path / 'source2', bundle=spec1['bundle'],
                                generation_id='g2', quiescence=quiescence(tmp_path, 2))
    target2 = dict(result['trusted'], parent_generation='g1')
    decide(a, 'authorize_generation', dict(target=target2, expected_pointer=spec1['target']))
    spec2 = dict(spec1, target=target2, prior=spec1['target'], compatible=result['compatible'],
                 epoch=2, source=str(tmp_path / 'source2'))
    (tmp_path / 'fixture-inputs.json').write_text(canonical(spec2))
    crash(tmp_path, 'reconcile:after_commit')
    second = coordinator(tmp_path, spec2)
    assert second.recover() == 'COMMITTED'
    state = second.authority.inspect()['state']
    assert state['current'] == target2 and state['previous'] == spec1['target']
    assert state['owner']['epoch'] == 2 and state['owner']['attempt'] == 'a2'
    assert state['allocated'] == 8 and state['historical_debit'] == 2
    assert state['spent'] == {'r1': 1, 'r2': 1}
    assert set(state['reservations']) == {'r1', 'r2'}
    assert all_bytes(tmp_path / 'published/g1') == first_bytes
    assert (all_bytes(work), all_bytes(Path(spec1['bundle']))) == protected

