"""Local fixtures and hard process exits at transaction and mock gate boundaries."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest

from vimfc.domain import Incompatible, digest
from vimfc.pipeline import transactional_control as control


BINDING = dict(workspace_id='w', run_id='r', bundle_id='b')
OWNER = dict(attempt='a', epoch=1, job='job')
RESERVATION = dict(reservation_id='res', attempt='a', allowance=3)


def opened(root, **kwargs):
    return control.TransactionalAuthority(
        root / 'authority.sqlite', control.SQLiteWitness(root / 'witness.sqlite'),
        authority_id='authority', binding=BINDING, runtime='runtime', budget_id='budget',
        ceiling=5, historical_debit=2, **kwargs)


@pytest.fixture
def ready(tmp_path):
    a = opened(tmp_path)
    a.provision()
    a.decide('init', 0, 'initialize', {})
    a.decide('claim', 1, 'claim', dict(OWNER, operator='op', fencing_evidence='synthetic'))
    return a


def launch(a, transport, **identity):
    return a.launch('launch', 3, **(OWNER | identity), reservation_id='res',
                    ttl=60, mock_transport=transport)


def _child(root, operation, point):
    def checkpoint(current):
        if point == current:
            os._exit(71)

    def transport(units):
        with (root / 'mock-calls').open('a') as output:
            output.write(f'{units}\n')

    a = opened(root, checkpoint=checkpoint)
    if operation == 'reserve':
        a.decide('reserve', 2, 'reserve', RESERVATION)
    else:
        if operation == 'request':
            a.checkpoint = lambda _: None
        worker = launch(a, transport)
        if operation == 'request':
            a.checkpoint = checkpoint
            worker('request', 4, 2)


def crash(root, operation, point):
    repo = Path(__file__).resolve().parents[2]
    code = ('import runpy, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
            "runpy.run_path(sys.argv[2])['_child'](Path(sys.argv[3]), *sys.argv[4:])")
    result = subprocess.run([sys.executable, '-B', '-c', code, str(repo / 'backend/src'),
                             __file__, str(root), operation, point], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 71, result.stderr


@pytest.mark.parametrize('point,status', [
    ('before_stage', 'ACKNOWLEDGED'),
    ('before_commit', 'BLOCKED'),
    ('after_commit', 'PENDING_ACKNOWLEDGMENT'),
    ('before_ack', 'PENDING_ACKNOWLEDGMENT'),
    ('after_ack', 'ACKNOWLEDGED'),
])
def test_reservation_process_exit(ready, tmp_path, point, status):
    crash(tmp_path, 'reserve', point)
    a = opened(tmp_path)
    if status == 'BLOCKED':
        with pytest.raises(Incompatible, match='pending witness'):
            a.recover()
        with pytest.raises(Incompatible):
            a.decide('other', 2, 'reserve', RESERVATION)
        return
    view = a.inspect()
    assert view['status'] == status
    if point == 'before_stage':
        assert view['state']['allocated'] == 2
    else:
        assert view['state']['allocated'] == 5
    if status == 'PENDING_ACKNOWLEDGMENT':
        with pytest.raises(Incompatible, match='acknowledgment required'):
            a.decide('reserve', 2, 'reserve', RESERVATION)
    a.recover()
    a.decide('reserve', 2, 'reserve', RESERVATION)
    assert a.inspect()['state']['allocated'] == 5


@pytest.mark.parametrize('point', ['after_commit', 'after_ack', 'before_worker_issue'])
def test_interrupted_launch_cannot_reissue_worker(ready, tmp_path, point):
    ready.decide('reserve', 2, 'reserve', RESERVATION)
    crash(tmp_path, 'launch', point)
    a = opened(tmp_path)
    a.recover()
    for decision_id in ('launch', 'new-launch'):
        with pytest.raises(Incompatible):
            a.launch(decision_id, 4, **OWNER, reservation_id='res', ttl=1, mock_transport=pytest.fail)
    assert a.inspect()['state']['allocated'] == 5
    assert not (tmp_path / 'mock-calls').exists()


@pytest.mark.parametrize('point,calls', [
    ('after_commit', ''), ('before_dispatch', ''), ('after_dispatch_admission', ''), ('after_dispatch', '2\n'),
])
def test_interrupted_dispatch_is_charged_without_retry(ready, tmp_path, point, calls):
    ready.decide('reserve', 2, 'reserve', RESERVATION)
    crash(tmp_path, 'request', point)
    a = opened(tmp_path)
    a.recover()
    log = tmp_path / 'mock-calls'
    assert (log.read_text() if log.exists() else '') == calls
    assert a.inspect()['state']['spent'] == {'res': 2}
    assert a.inspect()['state']['allocated'] == 5
    with pytest.raises(Incompatible):
        launch(a, pytest.fail)
    assert (log.read_text() if log.exists() else '') == calls


def test_mock_gate_zero_calls_on_denials(ready, monkeypatch):
    a = ready
    calls = []
    a.decide('reserve', 2, 'reserve', RESERVATION)
    monkeypatch.setattr(control, 'monotonic', lambda: 1)
    for identity in ({'job': 'other'}, {'epoch': 2}, {'attempt': 'other'}):
        with pytest.raises(Incompatible):
            launch(a, calls.append, **identity)
    with pytest.raises(Incompatible):
        a.decide('bypass', 3, 'dispatch', {'reservation_id': 'res', 'units': 1})
    worker = launch(a, calls.append)
    a.witness.available = False
    with pytest.raises(Incompatible):
        worker('unavailable', 4, 1)
    a.witness.available = True
    with pytest.raises(Incompatible):
        worker('stale', 3, 1)
    assert calls == []
    worker('first', 4, 2)
    for args in (('first', 4, 2), ('exhausted', 5, 2), ('invalid', 5, True)):
        with pytest.raises(Incompatible):
            worker(*args)
    monkeypatch.setattr(control, 'monotonic', lambda: 61)
    with pytest.raises(Incompatible):
        worker('expired', 5, 1)
    assert calls == [2]


def test_witness_loss_after_dispatch_ack_prevents_transport(ready):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    worker = launch(a, pytest.fail)

    def disconnect(point):
        if point == 'before_dispatch':
            a.witness.available = False

    a.checkpoint = disconnect
    with pytest.raises(Incompatible, match='Witness unavailable'):
        worker('request', 4, 1)
    a.witness.available = True
    assert a.inspect()['state']['spent'] == {'res': 1}
    with pytest.raises(Incompatible, match='already used'):
        worker('request', 4, 1)


def test_blocked_transport_releases_lock_and_grant_cannot_be_reused(ready, tmp_path):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    started, release = Event(), Event()
    calls = []

    def transport(units):
        calls.append(units)
        started.set()
        assert release.wait(15)
        return 'delivered'

    worker = launch(a, transport)
    other = opened(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(worker, 'request', 4, 2)
        try:
            assert started.wait(10)
            with closing(sqlite3.connect(a.path)) as db:
                launch_record, grant = [json.loads(row[0]) for row in db.execute(
                    'SELECT body FROM records WHERE version IN (4, 5) ORDER BY version')]
            assert other.witness.read(other.genesis) == (5, digest(grant), None)
            assert grant['decision_id'] == 'request' and grant['action'] == 'dispatch'
            assert grant['payload'] == dict(owner=dict(OWNER, operator='op', state='RUNNING'),
                                            launch_digest=digest(launch_record),
                                            reservation_id='res', allowance=3, units=2)
            with pytest.raises(Incompatible, match='already used'):
                worker('request', 4, 2)
            other.decide('stop', 5, 'stop', {})
            assert not request.done()
            with pytest.raises(Incompatible, match='Invalid worker owner'):
                worker('new-request', 6, 1)
            assert other.inspect()['state']['spent'] == {'res': 2}
            assert calls == [2]
        finally:
            release.set()
        assert request.result(timeout=5) == 'delivered'


@pytest.mark.parametrize('point,calls', [('before_dispatch', []), ('after_dispatch_admission', [2])])
def test_stop_orders_against_admission(ready, tmp_path, point, calls):
    a = ready
    actual = []
    a.decide('reserve', 2, 'reserve', RESERVATION)
    worker = launch(a, actual.append)
    other = opened(tmp_path)

    def stop(current):
        if current == point:
            other.decide('stop', 5, 'stop', {})

    a.checkpoint = stop
    if point == 'before_dispatch':
        with pytest.raises(Incompatible, match='Invalid worker owner'):
            worker('request', 4, 2)
    else:
        worker('request', 4, 2)
    with pytest.raises(Incompatible, match='Invalid worker owner'):
        worker('later', 6, 1)
    assert actual == calls
    assert other.inspect()['state']['spent'] == {'res': 2}


def test_timeout_keeps_charge_and_denies_replay(ready, tmp_path):
    a = ready
    calls = []
    a.decide('reserve', 2, 'reserve', RESERVATION)

    def transport(units):
        calls.append(units)
        raise TimeoutError('Unknown delivery')

    worker = launch(a, transport)
    with pytest.raises(TimeoutError, match='Unknown delivery'):
        worker('request', 4, 2)
    with pytest.raises(Incompatible, match='already used'):
        worker('request', 4, 2)
    with pytest.raises(Incompatible, match='exhausted'):
        worker('over-allowance', 5, 2)
    assert opened(tmp_path).inspect()['state']['spent'] == {'res': 2}
    assert calls == [2]


def test_expiry_between_charge_and_admission_keeps_charge(ready, monkeypatch):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    monkeypatch.setattr(control, 'monotonic', lambda: 1)
    worker = launch(a, pytest.fail)

    def expire(point):
        if point == 'before_dispatch':
            monkeypatch.setattr(control, 'monotonic', lambda: 61)

    a.checkpoint = expire
    with pytest.raises(Incompatible, match='expired'):
        worker('request', 4, 2)
    assert a.inspect()['state']['spent'] == {'res': 2}


@pytest.mark.parametrize('rollback', ['authority', 'witness'])
def test_rollback_and_lost_scratch_fail_closed(ready, tmp_path, rollback):
    a = ready
    path = a.path if rollback == 'authority' else a.witness.path
    backup = tmp_path / 'older.sqlite'
    shutil.copyfile(path, backup)
    scratch = tmp_path / 'scratch'
    scratch.write_text('simulated worker scratch')
    a.decide('reserve', 2, 'reserve', RESERVATION)
    scratch.unlink()
    assert opened(tmp_path).inspect()['state']['allocated'] == 5
    shutil.copyfile(backup, path)
    with pytest.raises(Incompatible):
        opened(tmp_path).recover()
    with pytest.raises(Incompatible):
        launch(opened(tmp_path), pytest.fail)


def test_missing_authority_and_unknown_owner(ready):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    worker = launch(a, pytest.fail)
    a.decide('unknown', 4, 'unknown', {'reason': 'Synthetic termination uncertain'})
    with pytest.raises(Incompatible):
        worker('denied', 5, 1)
    with pytest.raises(Incompatible):
        a.decide('takeover', 5, 'claim', dict(OWNER, epoch=2, operator='op', fencing_evidence='synthetic'))
    a.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        worker('missing', 5, 1)
    assert not a.path.exists()


def test_expected_version_serializes_writers(ready, tmp_path):
    def reserve(ident):
        try:
            opened(tmp_path).decide(ident, 2, 'reserve', RESERVATION)
            return True
        except Incompatible:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ['one', 'two'])) == [False, True]
    assert ready.inspect()['state']['allocated'] == 5


def test_publication_intent_and_no_refund(ready, tmp_path):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    launch(a, pytest.fail)
    a.decide('stop', 4, 'stop', {})
    target = dict(identity=dict(BINDING, attempt_id='a', epoch_id='1', generation_id='g'),
                  manifest_sha256='a' * 64, parent_generation=None)
    payload = {'target': target, 'expected_pointer': None}
    wrong = payload | {'target': target | {'identity': target['identity'] | {'epoch_id': '2'}}}
    with pytest.raises(Incompatible):
        a.decide('invalid-pub', 5, 'authorize_generation', wrong)
    a.decide('authorize', 5, 'authorize_generation', payload)
    a.decide('publish', 6, 'publish', {})
    a.decide('prepare', 7, 'prepare', {})
    a = opened(tmp_path)
    state = a.inspect()['state']
    assert state['publication']['state'] == 'PREPARED'
    assert state['publication']['target'] == target
    assert state['allocated'] == 5
    with pytest.raises(Incompatible):
        a.decide('fake-commit', 8, 'reconcile', {'observed_pointer': target, 'verified': True})
    with pytest.raises(Incompatible):
        a.decide('release', 8, 'release_stopped', {})


def test_release_does_not_refund(ready):
    a = ready
    a.decide('reserve', 2, 'reserve', RESERVATION)
    launch(a, pytest.fail)
    a.decide('stop', 4, 'stop', {})
    a.decide('release', 5, 'release_stopped', {})
    a.decide('second-owner', 6, 'claim',
             dict(OWNER, epoch=2, attempt='b', operator='op', fencing_evidence='synthetic'))
    with pytest.raises(Incompatible):
        a.decide('new-reservation', 7, 'reserve', dict(reservation_id='next', attempt='b', allowance=1))
    assert a.inspect()['state']['allocated'] == 5

