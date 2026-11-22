"""Independent approval recovery without changing the witnessed binding or worker."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from vimfc.domain import Incompatible, canonical
from vimfc.pipeline import selection_epoch
from vimfc.pipeline.transactional_control import SQLiteWitness, TransactionalAuthority
from test_generation_publication import all_bytes
from test_selection_epoch import restored, activation, workspace


def fresh_gate(selection):
    ident, binding, runtime, budget, ceiling, debit = selection.authority._config
    authority = TransactionalAuthority(
        selection.authority.path, SQLiteWitness(selection.authority.witness.path),
        authority_id=ident, binding=binding, runtime=runtime, budget_id=budget,
        ceiling=ceiling, historical_debit=debit)
    return selection_epoch.SyntheticSelection(
        authority, selection.root, target=selection.target, compatible=selection.compatible,
        bundle=selection.bundle, trusted_policy=selection.trusted_policy)


def protected(selection):
    return (selection.authority.path.read_bytes(), selection.authority.witness.path.read_bytes(),
            all_bytes(selection.root), all_bytes(selection.bundle))


def spec_file(selection, root):
    with closing(sqlite3.connect(selection.root / 'pipeline.sqlite')) as db:
        config = json.loads(db.execute('SELECT config_json FROM pipeline_runs WHERE pipeline_run_id=?',
                            (selection.target['identity']['run_id'],)).fetchone()[0])
    path = root / 'trusted-fixture-inputs.json'
    path.write_text(canonical(dict(
        root=str(selection.root), authority=str(selection.authority.path),
        witness=str(selection.authority.witness.path), target=selection.target,
        compatible=selection.compatible, bundle=str(selection.bundle),
        control=selection.authority._config, config=config)), encoding='utf-8')
    return path


def _child(spec_path, boundary):
    def denied(*_args, **_kwargs):
        raise AssertionError('Network forbidden in restart fixture')

    socket.socket.connect = denied
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    ident, binding, runtime, budget, ceiling, debit = spec['control']
    authority = TransactionalAuthority(
        spec['authority'], SQLiteWitness(spec['witness']), authority_id=ident, binding=binding,
        runtime=runtime, budget_id=budget, ceiling=ceiling, historical_debit=debit)
    gate = selection_epoch.SyntheticSelection(
        authority, spec['root'], target=spec['target'], compatible=spec['compatible'], bundle=spec['bundle'],
        trusted_policy=lambda: selection_epoch.policy_fingerprint(spec['config'], runtime))
    if boundary in ('binding_after_commit', 'binding_after_ack'):
        stop_at = 'after_commit' if boundary == 'binding_after_commit' else 'after_ack'

        def stop(point):
            if point == stop_at:
                os._exit(71)

        authority.checkpoint = stop
        gate.bind('binding', 9)
        raise AssertionError('Binding fault boundary was missed')
    replay = gate._revalidate

    def interrupted_replay():
        result = replay()
        if boundary == 'after_replay':
            os._exit(71)
        return result

    gate._revalidate = interrupted_replay
    if boundary == 'during_replay':
        selector = selection_epoch.select_articles

        def interrupted_selector(*args, **kwargs):
            selector(*args, **kwargs)
            os._exit(71)

        selection_epoch.select_articles = interrupted_selector
    encoded = gate.recover_approval('binding')
    if boundary == 'after_approval':
        os._exit(71)
    assert gate.consume(encoded)['pilot_accepted'] is False
    spec_path.with_name('recovered-binding.json').write_text(encoded, encoding='utf-8')


def child(path, boundary):
    repo = Path(__file__).resolve().parents[2]
    code = ("import runpy,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "sys.path.insert(0,str(Path(sys.argv[2]).parent)); "
            "runpy.run_path(sys.argv[2])['_child'](Path(sys.argv[3]),sys.argv[4])")
    result = subprocess.run([sys.executable, '-B', '-c', code, str(repo / 'backend/src'),
                             __file__, str(path), boundary], capture_output=True, text=True, timeout=60)
    assert result.returncode == (0 if boundary == 'recover' else 71), result.stderr


def test_fresh_gate_repeated_recovery_preserves_every_protected_byte(restored, monkeypatch):
    original, _ = restored
    encoded = original.bind('binding', 9)
    before = protected(original)
    gate = fresh_gate(original)
    with pytest.raises(Incompatible, match='revalidation approval'):
        gate.consume(encoded)
    authority = gate.authority
    monkeypatch.setattr(authority, '_decide', lambda *_a, **_k: pytest.fail('Recovery wrote a decision'))
    monkeypatch.setattr(authority, '_launch', lambda *_a, **_k: pytest.fail('Recovery relaunched'))
    monkeypatch.setattr(authority, 'recover', lambda *_a, **_k: pytest.fail('Recovery acknowledged authority'))
    for _ in range(2):
        assert gate.recover_approval('binding') == encoded
        assert gate.consume(encoded) == json.loads((gate.root / 'selection.json').read_bytes())
        assert protected(gate) == before
    for ident in ('binding', 'second'):
        with pytest.raises(Incompatible, match='already bound'):
            original.bind(ident, 10)


@pytest.mark.parametrize('boundary', ['recover', 'during_replay', 'after_replay', 'after_approval'])
def test_process_restart_and_recovery_crashes(restored, tmp_path, boundary):
    gate, _ = restored
    encoded = gate.bind('binding', 9)
    path = spec_file(gate, tmp_path)
    before = protected(gate)
    child(path, boundary)
    assert protected(gate) == before
    if boundary != 'recover':
        child(path, 'recover')
    assert path.with_name('recovered-binding.json').read_text() == encoded
    assert protected(gate) == before


def test_pending_binding_requires_separate_explicit_witness_recovery(restored):
    original, _ = restored

    def crash(point):
        if point == 'after_commit':
            raise OSError('binding interrupted')

    original.authority.checkpoint = crash
    with pytest.raises(OSError, match='binding interrupted'):
        original.bind('binding', 9)
    gate = fresh_gate(original)
    before = protected(gate)
    with pytest.raises(Incompatible, match='Acknowledged active owner'):
        gate.recover_approval('binding')
    assert protected(gate) == before
    assert not gate._revalidated
    gate.authority.recover()
    acknowledged = protected(gate)
    assert gate.consume(gate.recover_approval('binding'))['pilot_accepted'] is False
    assert protected(gate) == acknowledged


@pytest.mark.parametrize('boundary', ['binding_after_commit', 'binding_after_ack'])
def test_binding_process_crash_before_receipt_can_recover_existing_decision(restored, tmp_path, boundary):
    original, _ = restored
    before_state = original.authority.inspect()['state']
    child(spec_file(original, tmp_path), boundary)
    gate = fresh_gate(original)
    if boundary == 'binding_after_commit':
        with pytest.raises(Incompatible, match='Acknowledged active owner'):
            gate.recover_approval('binding')
        gate.authority.recover()
    before = protected(gate)
    assert gate.consume(gate.recover_approval('binding'))['pilot_accepted'] is False
    assert gate.authority.inspect()['state'] == before_state
    assert protected(gate) == before


@pytest.mark.parametrize('field', ['checkpoint_sha256', 'selection_sha256'])
def test_recovery_requires_exact_witnessed_binding_even_for_valid_schema(restored, field):
    original, _ = restored
    asserted = original._revalidate()
    asserted[field] = '0' * 64
    original.authority._decide('binding', 9, 'bind_selection', asserted)
    gate = fresh_gate(original)
    before = protected(gate)
    with pytest.raises(Incompatible, match='differs from witnessed binding'):
        gate.recover_approval('binding')
    assert not gate._revalidated and protected(gate) == before


@pytest.mark.parametrize('decision', ['missing', 'activation', '', None])
def test_missing_or_wrong_decision_is_not_recoverable(restored, decision):
    original, _ = restored
    original.bind('binding', 9)
    gate = fresh_gate(original)
    before = protected(gate)
    with pytest.raises(Incompatible):
        gate.recover_approval(decision)
    assert not gate._revalidated and protected(gate) == before


def test_recovered_consumer_keeps_fast_path(restored, monkeypatch):
    original, _ = restored
    encoded = original.bind('binding', 9)
    gate = fresh_gate(original)
    assert gate.recover_approval('binding') == encoded
    monkeypatch.setattr(gate, '_revalidate', lambda: pytest.fail('Unexpected consumer replay'))
    monkeypatch.setattr(selection_epoch, 'select_articles', lambda *_a, **_k: pytest.fail('Unexpected selector replay'))
    assert gate.consume(encoded)['pilot_accepted'] is False


@pytest.mark.parametrize('damage', [
    'policy', 'missing-policy', 'owner', 'epoch', 'generation', 'witness', 'witness-mismatch', 'rollback', 'publication',
    'selection', 'checkpoint',
])
def test_invalid_recovery_never_grants_approval(restored, damage):
    original, _ = restored
    original.bind('binding', 9)
    gate = fresh_gate(original)
    a = gate.authority
    if damage == 'policy':
        policy = deepcopy(gate._policy())
        policy['identity'] = '0' * 64
        gate.trusted_policy = lambda: policy
    elif damage == 'missing-policy':
        gate.trusted_policy = None
    elif damage in ('owner', 'epoch', 'publication'):
        a.decide('recovery-stop', 10, 'stop', {})
        if damage == 'epoch':
            a.decide('recovery-release', 11, 'release_stopped', {})
            a.decide('claim-next', 12, 'claim', dict(operator='operator', job='next', attempt='next',
                     epoch=3, fencing_evidence='fixture'))
            a.decide('reserve-next', 13, 'reserve', dict(reservation_id='next', attempt='next', allowance=1))
            a.launch('launch-next', 14, attempt='next', epoch=3, job='next', reservation_id='next',
                     ttl=60, mock_transport=pytest.fail)
        elif damage == 'publication':
            target = deepcopy(gate.target)
            target['identity'].update(attempt_id='new', epoch_id='2', generation_id='next')
            a.decide('authorize', 11, 'authorize_generation', dict(target=target, expected_pointer=None))
            a.decide('publish', 12, 'publish', {})
            a.decide('prepare', 13, 'prepare', {})
    elif damage == 'generation':
        gate.target['identity']['generation_id'] = 'wrong'
    elif damage == 'witness':
        a.witness.available = False
    elif damage == 'witness-mismatch':
        with closing(sqlite3.connect(a.witness.path)) as db:
            db.execute('UPDATE witness SET head=?', ('0' * 64,))
            db.commit()
    elif damage == 'rollback':
        with closing(sqlite3.connect(a.path)) as db:
            db.execute('DELETE FROM records WHERE version=10')
            db.commit()
    else:
        name = 'selection.json' if damage == 'selection' else 'pipeline.sqlite'
        (gate.root / name).write_bytes(b'changed')
    before = protected(gate)
    with pytest.raises(Incompatible):
        gate.recover_approval('binding')
    assert not gate._revalidated
    assert protected(gate) == before


@pytest.mark.parametrize('change', ['policy', 'owner', 'selection'])
def test_change_during_recovery_prevents_local_approval(restored, change):
    original, _ = restored
    original.bind('binding', 9)
    gate = fresh_gate(original)
    replay = gate._revalidate

    def change_after_replay():
        result = replay()
        if change == 'owner':
            gate.authority.decide('recovery-stop', 10, 'stop', {})
        elif change == 'selection':
            (gate.root / 'selection.json').write_bytes(b'changed')
        else:
            policy = deepcopy(gate._policy())
            policy['identity'] = '0' * 64
            gate.trusted_policy = lambda: policy
        return result

    gate._revalidate = change_after_replay
    with pytest.raises(Incompatible):
        gate.recover_approval('binding')
    assert not gate._revalidated
