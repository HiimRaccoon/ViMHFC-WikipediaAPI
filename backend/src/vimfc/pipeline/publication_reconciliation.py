"""Synthetic publication and explicit recovery for a single-threaded local fixture."""
from copy import deepcopy
from pathlib import Path

from . import generation_publication as publication
from .trusted_control import require


class PublicationCoordinator:
    def __init__(self, owner, root, *, target, prior, compatible):
        self.owner = owner
        self.authority = owner.authority
        self.root = Path(root)
        self.target = deepcopy(publication.pointer(target))
        self.prior = deepcopy(publication.pointer(prior)) if prior is not None else None
        self.compatible = deepcopy(compatible)

    def _intent(self, state, phase):
        require(state['publication'] == {
            'state': phase, 'target': self.target, 'expected_pointer': self.prior,
        }, 'Publication intent differs from trusted expectations')
        require(self.compatible.get('runtime') == state['runtime'], 'Publication runtime differs')
        committed = phase == 'RECONCILED_COMMITTED'
        require((state['current'], state['previous']) == (self.target, self.prior) if committed
                else state['current'] == self.prior, 'Authority pointer differs')

    def _store(self, action, payload):
        version = len(self.authority.records)
        self.authority.store(self.authority.propose(
            f'publication:{version}', version, action, payload))

    def publish(self, source, *, bundle, protected_inputs=(), fault=lambda _: None):
        """Start once, from an acknowledged generation authorization."""
        self._intent(self.owner._owned('STOPPED'), 'GENERATION_AUTHORIZED')
        require(publication.current(self.root) == self.prior, 'Unexpected prior pointer')
        self.owner._decide('publish', {})
        self._store('prepare', {})
        fault('before_intent_ack')
        self.authority.acknowledge()
        fault('after_intent_ack')
        self._intent(self.owner._owned('PUBLISHING'), 'PREPARED')
        publication.publish(source, self.root, bundle=bundle, compatible=self.compatible,
                            expectations={'authorized': True, 'target': self.target,
                                          'expected_pointer': self.prior},
                            synthetic=True, protected_inputs=protected_inputs, fault=fault)
        return self.reconcile(fault=fault)

    def _verify_pointer(self):
        evidence = publication.recover(self.root, expected_current=self.target,
                                       expected_previous=self.prior, synthetic=True)
        require(evidence['status'] == 'VERIFIED_CURRENT', evidence.get('reason', 'Pointer unverified'))
        manifest = publication.read_canonical(
            self.root / self.target['identity']['generation_id'] / 'manifest.json')
        require(manifest['compatibility'] == self.compatible, 'Generation compatibility differs')

    def reconcile(self, *, fault=lambda _: None):
        """Verify existing bytes and finish this intent, without writing to storage."""
        status = self.authority.status()
        require(status in ('ACKNOWLEDGED', 'PENDING_ACKNOWLEDGMENT'), 'Authority unverified')
        if status == 'PENDING_ACKNOWLEDGMENT':
            record = self.authority.records[-1]
            require(record['action'] == 'reconcile', 'Publication reconciliation not pending')
            state = record['state']
            owner = state['owner']
            require(owner['state'] == 'RECONCILED'
                    and (owner['attempt'], owner['epoch'], owner['job']) == self.owner._identity,
                    'Stale reconciliation owner')
            self._intent(state, 'RECONCILED_COMMITTED')
        else:
            state = self.owner._owned('PUBLISHING', 'RECONCILED')
            if state['owner']['state'] == 'PUBLISHING':
                self._intent(state, 'PREPARED')
                fault('before_reconciliation_store')
                self._verify_pointer()
                self._store('reconcile', {'observed_pointer': self.target, 'verified': True})
                fault('after_reconciliation_store')
            else:
                self._intent(state, 'RECONCILED_COMMITTED')

        if self.authority.status() == 'PENDING_ACKNOWLEDGMENT':
            fault('before_reconciliation_ack')
            self._verify_pointer()
            self.authority.acknowledge()
            fault('after_reconciliation_ack')
        self._intent(self.owner._owned('RECONCILED'), 'RECONCILED_COMMITTED')
        self._verify_pointer()
        return 'COMMITTED'

