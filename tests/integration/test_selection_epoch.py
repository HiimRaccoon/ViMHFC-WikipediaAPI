"""Synthetic selection replay and witnessed consumer admission."""
from contextlib import closing
import json
import sqlite3

import pytest

from vimfc.domain import Incompatible, canonical
from vimfc.pipeline import selection_epoch
from test_worker_activation import activation, workspace


@pytest.fixture
def restored(activation):
    gate, params, _ = activation
    receipt, _ = gate.activate('activation', 8, **params)
    with closing(sqlite3.connect(params['output'] / 'pipeline.sqlite')) as db:
        config = json.loads(db.execute('SELECT config_json FROM pipeline_runs WHERE pipeline_run_id=?',
                            (receipt['current']['identity']['run_id'],)).fetchone()[0])
    selection = selection_epoch.SyntheticSelection(
        gate.authority, params['output'], target=gate.current, compatible=gate.compatible, bundle=params['bundle'],
        trusted_policy=lambda: selection_epoch.policy_fingerprint(config, gate.compatible['runtime']))
    return selection, receipt


def test_revalidate_restored_selection_and_consume(restored):
    selection, receipt = restored
    path = selection.root / 'selection.json'
    original = path.read_bytes()
    assert receipt['selection'] == 'stale' and receipt['selection_epoch_bound'] is False
    with pytest.raises(Incompatible, match='binding required'):
        selection.consume()
    binding = selection.bind('binding', 9)
    payload = json.loads(binding)['binding']
    assert payload['activation']['epoch'] == 2 and payload['activation']['decision_id'] == 'activation'
    assert payload['generation'] == receipt['current']
    assert payload['checkpoint_sha256'] == receipt['checkpoint_hash']
    assert payload['policy_hashes'].keys() >= {'classification', 'extraction', 'ranking', 'quality', 'quotas', 'edge'}
    assert selection.consume(binding) == json.loads(original)
    assert path.read_bytes() == original
    for decision_id in ('binding', 'other'):
        with pytest.raises(Incompatible, match='already bound'):
            selection.bind(decision_id, 10)


@pytest.mark.parametrize('failure', ['tampered-file', 'tampered-binding', 'uncanonical', 'rewind', 'owner-change',
                                     'generation-change', 'witness'])
def test_consumer_denies_invalid_or_stale_binding(restored, failure):
    selection, _ = restored
    a = selection.authority
    binding = selection.bind('binding', 9)
    if failure == 'tampered-file':
        (selection.root / 'selection.json').write_bytes(b'{}')
    elif failure == 'tampered-binding':
        envelope = json.loads(binding)
        envelope['binding']['policy_hashes']['quotas'] = '0' * 64
        binding = canonical(envelope)
    elif failure == 'uncanonical':
        binding += '\n'
    elif failure == 'rewind':
        with closing(sqlite3.connect(a.path)) as db:
            db.execute('DELETE FROM records WHERE version = 10')
            db.commit()
    elif failure == 'owner-change':
        a.decide('stop-new', 10, 'stop', {})
        a.decide('release-new', 11, 'release_stopped', {})
        a.decide('next-owner', 12, 'claim', dict(operator='operator', job='next-job', attempt='next',
                       epoch=3, fencing_evidence='fixture'))
        a.decide('next-reserve', 13, 'reserve', dict(reservation_id='next-res', attempt='next', allowance=1))
        a.launch('next-launch', 14, attempt='next', epoch=3, job='next-job',
                 reservation_id='next-res', ttl=60, mock_transport=pytest.fail)
    elif failure == 'generation-change':
        selection.target['identity']['generation_id'] = 'other'
    else:
        a.witness.available = False
    with pytest.raises(Incompatible):
        selection.consume(binding)


def test_policy_replay_drift_prevents_binding(restored, monkeypatch):
    selection, _ = restored
    select = selection_epoch.select_articles

    def changed_policy(*args, **kwargs):
        return dict(select(*args, **kwargs), ranking_policy='future-policy')

    monkeypatch.setattr(selection_epoch, 'select_articles', changed_policy)
    with pytest.raises(Incompatible, match='policy replay'):
        selection.bind('binding', 9)
    assert selection.authority.inspect()['state']['owner']['epoch'] == 2


def test_interrupted_binding_is_unusable_until_witness_ack(restored):
    selection, _ = restored
    a = selection.authority

    def crash(point):
        if point == 'after_commit':
            raise OSError('interrupted before witness acknowledgment')

    a.checkpoint = crash
    with pytest.raises(OSError, match='interrupted'):
        selection.bind('binding', 9)
    with closing(sqlite3.connect(a.path)) as db:
        record = json.loads(db.execute('SELECT body FROM records WHERE version = 10').fetchone()[0])
    binding = canonical({'decision_id': 'binding', 'binding': record['payload']})
    with pytest.raises(Incompatible, match='Acknowledged active owner'):
        selection.consume(binding)
    a.recover()
    assert selection.consume(binding)['pilot_accepted'] is False


@pytest.mark.parametrize('change', ['policy', 'runtime', 'missing', 'malformed'])
def test_current_policy_change_after_binding_denies_consumer(restored, change):
    selection, _ = restored
    encoded = selection.bind('binding', 9)
    current = selection._policy()
    if change == 'missing':
        selection.trusted_policy = None
    else:
        if change == 'policy':
            current['hashes']['quotas'] = '0' * 64
        elif change == 'runtime':
            current['identity'] = '0' * 64
        else:
            current.pop('identity')
        selection.trusted_policy = lambda: current
    with pytest.raises(Incompatible):
        selection.consume(encoded)


def test_unchanged_consumer_avoids_full_replay(restored, monkeypatch):
    selection, _ = restored
    encoded = selection.bind('binding', 9)
    monkeypatch.setattr(selection_epoch, 'select_articles', lambda *_a, **_k: pytest.fail('Unexpected replay'))
    assert selection.consume(encoded) == selection.consume(encoded)


@pytest.mark.parametrize('damage', ['missing', 'extra', 'policy', 'checkpoint', 'epoch', 'identity'])
def test_malformed_authority_payload_is_rejected(restored, damage):
    selection, _ = restored
    payload = json.loads(selection.bind('binding', 9))['binding']
    if damage == 'missing':
        payload.pop('checkpoint_sha256')
    elif damage == 'extra':
        payload['verified'] = True
    elif damage == 'policy':
        payload['policy_hashes']['extra'] = 'a' * 64
    elif damage == 'checkpoint':
        payload['checkpoint_sha256'] = 'bad'
    elif damage == 'identity':
        payload['policy_identity'] = None
    else:
        payload['activation']['epoch'] = True
    with pytest.raises(Incompatible):
        selection.authority._decide('forged', 10, 'bind_selection', payload)
    with pytest.raises(Incompatible):
        selection.consume(canonical({'decision_id': 'forged', 'binding': payload}))


def test_witnessed_low_level_assertion_is_not_revalidation(restored):
    selection, _ = restored
    encoded = selection.bind('binding', 9)
    # A fresh gate has no independently retained revalidation approval.
    other = selection_epoch.SyntheticSelection(
        selection.authority, selection.root, target=selection.target,
        compatible=selection.compatible, bundle=selection.bundle, trusted_policy=selection.trusted_policy)
    with pytest.raises(Incompatible, match='revalidation approval'):
        other.consume(encoded)


def test_arbitrary_valid_low_level_payload_has_no_revalidation_approval(restored):
    selection, _ = restored
    current = selection._policy()
    manifest = selection_epoch.publication.read_canonical(selection.root / 'manifest.json')
    with selection_epoch._transaction(selection.authority.path) as db:
        active = selection._activation(selection.authority._load(db))
    payload = dict(format='synthetic-selection-epoch-1', generation=selection.target,
                   checkpoint_sha256=manifest['checkpoint']['checkpoint_hash'],
                   selection_sha256=manifest['artifacts']['selection.json']['sha256'],
                   policy_identity=current['identity'], policy_hashes=current['hashes'], activation=active)
    selection.authority._decide('assertion', 9, 'bind_selection', payload)
    with pytest.raises(Incompatible, match='revalidation approval'):
        selection.consume(canonical({'decision_id': 'assertion', 'binding': payload}))


def test_declared_policy_change_after_binding(restored, monkeypatch):
    selection, _ = restored
    encoded = selection.bind('binding', 9)
    monkeypatch.setattr(selection_epoch.annotation_edges, 'RANK_VERSION', 'changed-policy')
    with pytest.raises(Incompatible, match='current policy'):
        selection.consume(encoded)

