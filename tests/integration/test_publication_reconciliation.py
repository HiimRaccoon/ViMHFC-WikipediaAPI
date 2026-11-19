from copy import deepcopy

import pytest

from vimfc.domain import Incompatible
from vimfc.pipeline import generation_publication as p, local_snapshot as s
from vimfc.pipeline.publication_reconciliation import PublicationCoordinator
from vimfc.pipeline.trusted_control import FakeWitness, SyntheticAuthority, SyntheticOwnership
from test_local_snapshot import workspace
from test_trusted_control import decision


def prepare(tmp_path, workspace, authority=None, epoch=1):
    bundle, work, identity = workspace
    identity = dict(identity, generation_id=f'g{epoch}', epoch_id=str(epoch), attempt_id=f'a{epoch}')
    binding = {key: identity[key] for key in ('workspace_id', 'run_id', 'bundle_id')}
    if authority is None:
        authority = SyntheticAuthority('a', binding, 'fixture', 'budget', 10, 2, FakeWitness())
        decision(authority, 'initialize', {})
    owner = SyntheticOwnership(authority, attempt=f'a{epoch}', epoch=epoch, job=f'job{epoch}')
    owner.claim(operator='op', fencing_evidence='fixture')
    owner.reserve(f'r{epoch}', 3)
    owner.launch(f'r{epoch}')(1)
    owner.stop(terminated=True)
    source = tmp_path / f'source{epoch}'
    result = s.snapshot(work, source, bundle=bundle, identity=identity, runtime='fixture',
                        stopped_writer=True, synthetic=True)
    prior = authority.inspect()['state']['current']
    target = dict(result['trusted'], parent_generation=prior['identity']['generation_id'] if prior else None)
    decision(authority, 'authorize_generation', {'target': target, 'expected_pointer': prior})
    coordinator = PublicationCoordinator(owner, tmp_path / 'published', target=target,
                                         prior=prior, compatible=result['compatible'])
    return coordinator, source, bundle


@pytest.mark.parametrize(('boundary', 'pointer_replaced'), [
    ('before_intent_ack', False), ('after_intent_ack', False),
    ('before_promotion', False), ('after_promotion', False),
    ('before_pointer_replace', False), ('after_pointer_replace', True),
    ('during_receipt', True), ('before_reconciliation_store', True),
    ('after_reconciliation_store', True), ('before_reconciliation_ack', True),
    ('after_reconciliation_ack', True),
])
def test_explicit_reconciliation_after_crash(tmp_path, workspace, boundary, pointer_replaced):
    c, source, bundle = prepare(tmp_path, workspace)
    authority = c.authority
    before = authority.inspect()['state']

    def crash(point):
        if point == boundary and (point != 'during_receipt' or p.current(c.root) == c.target):
            raise OSError(point)

    with pytest.raises(OSError, match=boundary):
        c.publish(source, bundle=bundle, fault=crash)
    assert p.current(c.root) == (c.target if pointer_replaced else None)
    with pytest.raises(Incompatible):
        c.publish(source, bundle=bundle)
    if boundary in ('after_reconciliation_store', 'before_reconciliation_ack', 'before_intent_ack'):
        assert authority.status() == 'PENDING_ACKNOWLEDGMENT'
    if pointer_replaced:
        assert c.reconcile() == 'COMMITTED'
        version = len(authority.records)
        assert c.reconcile() == 'COMMITTED'
        assert len(authority.records) == version
    else:
        with pytest.raises(Incompatible):
            c.reconcile()
    after = authority.inspect()['state']
    assert after['owner'] | {'state': before['owner']['state']} == before['owner']
    for key in ('allocated', 'ceiling', 'historical_debit', 'reservations', 'attempts'):
        assert after[key] == before[key]


def test_two_generations_preserve_ancestry_and_budget(tmp_path, workspace):
    first, source, bundle = prepare(tmp_path, workspace)
    assert first.publish(source, bundle=bundle) == 'COMMITTED'
    authority = first.authority
    decision(authority, 'release', {})
    second, source, bundle = prepare(tmp_path, workspace, authority, epoch=2)
    assert second.publish(source, bundle=bundle) == 'COMMITTED'
    state = authority.inspect()['state']
    assert (state['current'], state['previous']) == (second.target, first.target)
    assert state['allocated'] == 8
    assert set(state['reservations']) == {'r1', 'r2'}
    with pytest.raises(Incompatible):
        first.reconcile()


@pytest.mark.parametrize('mismatch', ['bytes', 'pointer', 'expectation', 'witness', 'owner'])
def test_receipt_cannot_acknowledge_mismatched_pending_commit(tmp_path, workspace, mismatch):
    c, source, bundle = prepare(tmp_path, workspace)

    def crash(point):
        if point == 'after_reconciliation_store':
            raise OSError(point)

    with pytest.raises(OSError):
        c.publish(source, bundle=bundle, fault=crash)
    receipt = next(c.root.glob('attempt-*.json'))
    assert p.read_canonical(receipt)['state'] == 'COMMITTED'
    target = deepcopy(c.target)
    owner = c.owner
    if mismatch == 'bytes':
        (c.root / 'g1' / 'workspace.json').write_bytes(b'corrupt')
    elif mismatch == 'pointer':
        (c.root / 'latest.json').unlink()
    elif mismatch == 'expectation':
        target['manifest_sha256'] = 'f' * 64
    elif mismatch == 'witness':
        c.authority.witness.available = False
    else:
        owner = SyntheticOwnership(c.authority, attempt='a1', epoch=1, job='wrong')
    observer = PublicationCoordinator(owner, c.root, target=target, prior=None, compatible=c.compatible)
    head = c.authority.witness.head
    with pytest.raises(Incompatible):
        observer.reconcile()
    assert c.authority.witness.head == head


def test_authority_assertion_alone_cannot_prove_commit(tmp_path, workspace):
    c, _, _ = prepare(tmp_path, workspace)
    decision(c.authority, 'publish', {})
    decision(c.authority, 'prepare', {})
    decision(c.authority, 'reconcile', {'observed_pointer': c.target, 'verified': True})
    with pytest.raises(Incompatible):
        c.reconcile()

