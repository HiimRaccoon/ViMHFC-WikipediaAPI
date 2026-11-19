"""Activation from a verified local generation, using synthetic termination evidence."""
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from vimfc.domain import Incompatible
from vimfc.pipeline import backup_recovery as backup
from vimfc.pipeline.transactional_control import SQLiteWitness, TransactionalAuthority
from vimfc.pipeline.worker_activation import SyntheticActivation, SyntheticTermination
from test_generation_publication import generation, publish, workspace


@pytest.fixture
def activation(tmp_path, workspace):
    bundle, work, identity = workspace
    old = SyntheticTermination('operator', 'old-job', 'old', 1, True)
    ws = bundle, work, dict(identity, attempt_id=old.attempt, epoch_id=str(old.epoch))
    source, compatible, expectations = generation(tmp_path, ws)
    publish(tmp_path, ws, source, compatible, expectations)
    target = expectations['target']
    shared = tmp_path / 'backup'
    backup.transfer_backup(tmp_path / 'storage', shared, expected_current=target, synthetic=True)
    authority = TransactionalAuthority(
        tmp_path / 'authority.sqlite', SQLiteWitness(tmp_path / 'witness.sqlite'),
        authority_id='fixture', budget_id='budget', ceiling=10, historical_debit=2,
        runtime=compatible['runtime'],
        binding={k: identity[k] for k in ('workspace_id', 'run_id', 'bundle_id')})
    authority.provision()
    authority.decide('init', 0, 'initialize', {})
    authority.decide('old-owner', 1, 'claim', dict(operator=old.operator, job=old.job,
                    attempt=old.attempt, epoch=old.epoch, fencing_evidence='fixture assertion'))
    authority.decide('old-reserve', 2, 'reserve', dict(reservation_id='old-res', attempt=old.attempt, allowance=3))
    authority.launch('old-launch', 3, attempt=old.attempt, epoch=old.epoch, job=old.job,
                     reservation_id='old-res', ttl=60, mock_transport=pytest.fail)
    authority.decide('stop', 4, 'stop', {})
    authority.decide('release', 5, 'release_stopped', {})
    authority.decide('new-owner', 6, 'claim', dict(operator='operator', job='new-job', attempt='new',
                    epoch=2, fencing_evidence='fixture assertion'))
    authority.decide('new-reserve', 7, 'reserve', dict(reservation_id='new-res', attempt='new', allowance=3))
    gate = SyntheticActivation(authority, expected_current=target, compatible=compatible,
                               verify_termination=lambda evidence: evidence is old)
    calls = []
    params = dict(source=shared, output=tmp_path / 'fresh', bundle=bundle, evidence=old,
                  attempt='new', epoch=2, job='new-job', reservation_id='new-res', ttl=60,
                  mock_transport=calls.append)
    return gate, params, calls


def test_verified_activation_uses_dispatch_gate_once(activation, tmp_path):
    gate, params, calls = activation
    receipt, worker = gate.activate('activation', 8, **params)
    assert receipt['verified'] and receipt['selection'] == 'stale'
    assert receipt['selection_epoch_bound'] is False
    assert calls == []
    worker('request', 9, 2)
    assert calls == [2]
    for request_id in ('activation', 'other-activation'):
        with pytest.raises(Incompatible, match='Invalid worker owner'):
            gate.activate(request_id, 10, **dict(params, output=tmp_path / request_id))
    assert calls == [2]
    assert gate.authority.inspect()['state']['allocated'] == 8


@pytest.mark.parametrize('unresolved', [False, True])
def test_older_pinned_generation_or_unresolved_intent_denies_successor(activation, tmp_path, unresolved):
    gate, params, calls = activation
    a = gate.authority
    gate.activate('activation', 8, **params)
    a.decide('stop-new', 9, 'stop', {})
    if unresolved:
        target = dict(gate.current, identity=dict(gate.current['identity'], generation_id='g2',
                                                  attempt_id='new', epoch_id='2'))
        a.decide('authorize', 10, 'authorize_generation', dict(target=target, expected_pointer=None))
        a.decide('publish', 11, 'publish', {})
        a.decide('prepare', 12, 'prepare', {})
    else:
        a.decide('release-new', 10, 'release_stopped', {})
        a.decide('next-owner', 11, 'claim', dict(operator='operator', job='next-job', attempt='next',
                       epoch=3, fencing_evidence='fixture assertion'))
        a.decide('next-reserve', 12, 'reserve', dict(reservation_id='next-res', attempt='next', allowance=1))
    evidence = SyntheticTermination('operator', 'new-job', 'new', 2, True)
    gate.verify_termination = lambda candidate: candidate is evidence
    with pytest.raises(Incompatible, match='Unresolved publication intent' if unresolved else 'Stale generation'):
        gate.activate('next-activation', 13, **dict(params, output=tmp_path / 'next',
                      evidence=evidence, attempt='next', epoch=3, job='next-job', reservation_id='next-res'))
    assert calls == []


@pytest.mark.parametrize('failure', [
    'missing', 'string', 'uncertain', 'unverified', 'wrong-prior', 'stale-owner', 'stale-generation',
    'corrupt', 'rollback', 'witness',
])
def test_activation_denial(activation, failure):
    gate, params, calls = activation
    authority = gate.authority
    if failure == 'missing':
        params['evidence'] = None
    elif failure == 'string':
        params['evidence'] = 'stopped'
    elif failure == 'uncertain':
        params['evidence'] = replace(params['evidence'], terminated=False)
    elif failure == 'unverified':
        params['evidence'] = replace(params['evidence'])
    elif failure == 'wrong-prior':
        params['evidence'] = replace(params['evidence'], job='other')
        gate.verify_termination = lambda _: True
    elif failure == 'stale-owner':
        params.update(attempt='old', epoch=1, job='old-job')
    elif failure == 'stale-generation':
        gate.current['identity']['generation_id'] = 'superseded'
    elif failure == 'corrupt':
        (params['source'] / 'g1' / 'workspace.json').write_bytes(b'bad')
    elif failure == 'rollback':
        # Restore an older history prefix while the witness retains its newer head.
        old_db = params['output'].with_suffix('.sqlite')
        with closing(sqlite3.connect(authority.path)) as db, closing(sqlite3.connect(old_db)) as old:
            db.backup(old)
            old.execute('DELETE FROM records WHERE version > 6')
            old.commit()
        shutil.copyfile(old_db, authority.path)
    else:
        authority.witness.available = False
    before = authority.path.read_bytes(), authority.witness.path.read_bytes()
    with pytest.raises(Incompatible):
        gate.activate('activation', 8, **params)
    assert calls == []
    assert (authority.path.read_bytes(), authority.witness.path.read_bytes()) == before

