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
    selection = selection_epoch.SyntheticSelection(
        gate.authority, params['output'], target=gate.current, compatible=gate.compatible, bundle=params['bundle'])
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

