"""Offline contract tests; witness and fencing evidence are synthetic."""
from copy import deepcopy

import pytest

from vimfc.domain import Incompatible
from vimfc.pipeline.trusted_control import FakeWitness, SyntheticAuthority


@pytest.fixture
def model():
    witness = FakeWitness()
    authority = SyntheticAuthority('authority', {'workspace_id': 'w', 'run_id': 'r', 'bundle_id': 'b'}, 'runtime', 'budget', 10, 2, witness)
    return authority, witness


def decision(authority, action, payload, ident=None):
    version = len(authority.records)
    proposal = authority.propose(ident or str(version), version, action, payload)
    authority.store(proposal)
    authority.acknowledge()
    return proposal


def initialize(a):
    return decision(a, 'initialize', {})


def claim(a, attempt='a', epoch=1):
    return decision(a, 'claim', {'operator': 'op', 'job': 'job', 'attempt': attempt, 'epoch': epoch, 'fencing_evidence': 'manual evidence'})


def reserve(a, reservation='res', attempt='a', allowance=3):
    return decision(a, 'reserve', {'reservation_id': reservation, 'attempt': attempt, 'allowance': allowance})


def target():
    return {'identity': {'workspace_id': 'w', 'run_id': 'r', 'bundle_id': 'b', 'attempt_id': 'a', 'epoch_id': '1', 'generation_id': 'g'}, 'manifest_sha256': 'a' * 64, 'parent_generation': None}


def test_initial_and_decision_replay(model):
    a, w = model
    assert a.inspect()['eligible'] is False
    proposal = a.propose('init', 0, 'initialize', {})
    assert a.records == []
    a.store(proposal)
    assert a.inspect()['status'] == 'PENDING_ACKNOWLEDGMENT'
    assert not a.inspect()['eligible']
    with pytest.raises(Incompatible): a.propose('next', 1, 'initialize', {})
    a.acknowledge(); w.acknowledge(proposal)
    assert a.inspect()['eligible']
    assert a.store(proposal) == proposal
    with pytest.raises(Incompatible): a.propose('init', 0, 'unknown', {'reason': 'changed'})
    with pytest.raises(Incompatible): a.propose('stale', 0, 'initialize', {})


def test_witness_failure_and_missing(model):
    a, w = model
    a.store(a.propose('init', 0, 'initialize', {}))
    w.fail_ack = True
    with pytest.raises(Incompatible): a.acknowledge()
    assert not a.inspect()['eligible']
    w.fail_ack = False; w.available = False
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'
    with pytest.raises(Incompatible): a.acknowledge()
    w.available = True; a.acknowledge()
    assert a.inspect()['eligible']
    a.witness = None
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'


def test_pending_record_corruption_cannot_be_acknowledged(model):
    a, witness = model
    a.store(a.propose('init', 0, 'initialize', {}))
    a.records[0]['state']['ceiling'] = 999
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'
    with pytest.raises(Incompatible): a.acknowledge()
    assert witness.head == (0, None)


def test_broken_chain_schema_and_rollback(model):
    a, w = model
    initialize(a)
    proposal = a.propose('claim', 1, 'claim', {'operator': 'op', 'job': 'job', 'attempt': 'a', 'epoch': 1, 'fencing_evidence': 'proof'})
    for field, value in [('schema', 2), ('previous_digest', 'bad')]:
        bad = deepcopy(proposal); bad[field] = value
        with pytest.raises(Incompatible): a.store(bad)
    a.store(proposal); a.acknowledge()
    a.records.pop()
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'
    with pytest.raises(Incompatible): a.propose('next', 1, 'initialize', {})
    a.records.append(proposal); a.records[0]['previous_digest'] = 'bad'
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'


def test_claim_reservation_and_lost_scratch(model):
    a, _ = model
    initialize(a)
    with pytest.raises(Incompatible): decision(a, 'launch', {'reservation_id': 'r'})
    with pytest.raises(Incompatible): decision(a, 'claim', {'operator': 'op', 'job': 'j', 'attempt': 'a', 'epoch': 1, 'fencing_evidence': ''})
    claim(a)
    with pytest.raises(Incompatible): reserve(a, allowance=9)
    reserve(a)
    with pytest.raises(Incompatible): reserve(a)
    decision(a, 'launch', {'reservation_id': 'res'})
    with pytest.raises(Incompatible): decision(a, 'launch', {'reservation_id': 'res'})
    decision(a, 'unknown', {'reason': 'scratch lost'})
    state = a.inspect()['state']
    assert state['allocated'] == 5 and state['reservations']['res']['launched']
    assert state['owner']['state'] == 'OWNER_UNKNOWN'
    assert a.inspect()['status'] == 'RECONCILIATION_REQUIRED' and not a.inspect()['eligible']
    with pytest.raises(Incompatible): claim(a, 'other', 2)


def test_publication_and_owner_lifecycle(model):
    a, _ = model
    initialize(a); claim(a); reserve(a); decision(a, 'launch', {'reservation_id': 'res'})
    with pytest.raises(Incompatible): decision(a, 'prepare', {})
    decision(a, 'stop', {})
    with pytest.raises(Incompatible): decision(a, 'publish', {})
    decision(a, 'authorize_generation', {'target': target(), 'expected_pointer': None})
    decision(a, 'publish', {})
    decision(a, 'prepare', {})
    with pytest.raises(Incompatible): decision(a, 'reconcile', {'observed_pointer': target(), 'verified': False})
    with pytest.raises(Incompatible): decision(a, 'reconcile', {'observed_pointer': None, 'verified': True})
    pending = a.propose('commit', len(a.records), 'reconcile', {'observed_pointer': target(), 'verified': True})
    a.store(pending)
    assert not a.inspect()['eligible']
    a.acknowledge()
    assert a.inspect()['state']['current'] == target()
    decision(a, 'release', {})
    with pytest.raises(Incompatible): claim(a, 'a', 2)
    with pytest.raises(Incompatible): claim(a, 'b', 1)
    claim(a, 'b', 2)
    assert a.inspect()['state']['allocated'] == 5
    reserve(a, 'res2', 'b'); decision(a, 'launch', {'reservation_id': 'res2'}); decision(a, 'stop', {})
    repeated = target(); repeated['identity'].update(attempt_id='b', epoch_id='2'); repeated['parent_generation'] = 'g'
    with pytest.raises(Incompatible): decision(a, 'authorize_generation', {'target': repeated, 'expected_pointer': target()})


def test_witness_rejects_stale_and_workspace_clone_cannot_refund(model):
    a, witness = model
    initial = initialize(a)
    claim(a); reserve(a)
    snapshot = deepcopy(a.inspect()['state'])
    snapshot['allocated'] = 2
    snapshot['reservations'].clear()
    assert a.inspect()['state']['allocated'] == 5
    assert a.inspect()['state']['reservations']['res']['allowance'] == 3
    with pytest.raises(Incompatible): witness.acknowledge(initial)
    assert a.inspect()['eligible']
    clone = SyntheticAuthority('authority', {'workspace_id': 'w', 'run_id': 'r', 'bundle_id': 'b'}, 'runtime', 'budget', 10, 2, witness)
    assert clone.inspect()['status'] == 'AUTHORITY_UNVERIFIED'
    with pytest.raises(Incompatible): initialize(clone)


def test_witness_ahead_and_recovery_requires_review(model):
    a, witness = model
    initialize(a)
    witness.head = (2, 'other')
    assert a.inspect()['status'] == 'AUTHORITY_UNVERIFIED'
    with pytest.raises(Incompatible): claim(a)
    with pytest.raises(Incompatible): a.acknowledge()


def test_strict_decisions_and_generation_binding(model):
    a, _ = model
    with pytest.raises(Incompatible): claim(a)
    initialize(a); claim(a); reserve(a); decision(a, 'launch', {'reservation_id': 'res'}); decision(a, 'stop', {})
    bad = target(); bad['identity']['workspace_id'] = 'different'
    with pytest.raises(Incompatible): decision(a, 'authorize_generation', {'target': bad, 'expected_pointer': None})
    with pytest.raises(Incompatible): decision(a, 'authorize_generation', {'target': target(), 'expected_pointer': None, 'extra': True})
    with pytest.raises(Incompatible): decision(a, 'reconcile', {'observed_pointer': target(), 'verified': True})


def test_historical_generation_id_cannot_be_reused(model):
    a, _ = model
    initialize(a)
    for epoch, generation in ((1, 'g'), (2, 'g2')):
        attempt = 'a' if epoch == 1 else 'b'
        claim(a, attempt, epoch); reserve(a, 'res' + str(epoch), attempt, 1)
        decision(a, 'launch', {'reservation_id': 'res' + str(epoch)}); decision(a, 'stop', {})
        prior = a.inspect()['state']['current']
        current = target(); current['identity'].update(attempt_id=attempt, epoch_id=str(epoch), generation_id=generation)
        current['parent_generation'] = prior['identity']['generation_id'] if prior else None
        decision(a, 'authorize_generation', {'target': current, 'expected_pointer': prior})
        decision(a, 'publish', {}); decision(a, 'prepare', {})
        decision(a, 'reconcile', {'observed_pointer': current, 'verified': True}); decision(a, 'release', {})
    claim(a, 'c', 3); reserve(a, 'res3', 'c', 1)
    decision(a, 'launch', {'reservation_id': 'res3'}); decision(a, 'stop', {})
    prior = a.inspect()['state']['current']
    reused = target(); reused['identity'].update(attempt_id='c', epoch_id='3'); reused['parent_generation'] = 'g2'
    with pytest.raises(Incompatible): decision(a, 'authorize_generation', {'target': reused, 'expected_pointer': prior})

