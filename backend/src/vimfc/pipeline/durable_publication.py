"""Durable publication for private local fixtures with separately verified quiescence."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from . import generation_publication as publication
from . import local_snapshot as snapshot
from .transactional_control import _transaction
from .trusted_control import Incompatible, require


def _noop(_point):
    pass


class OperatorDispositionRequired(Incompatible):
    pass


@dataclass(frozen=True)
class QuiescenceEvidence:
    operator: str
    job: str
    attempt: str
    epoch: int


class FixtureQuiescence:
    """Trusted fixture setup verifies all admitted work and writers are drained."""

    def __init__(self, evidence, verifier):
        self.evidence = evidence
        self.verifier = verifier

    def verify(self, state):
        require(type(self.evidence) is QuiescenceEvidence and callable(self.verifier),
                'Independent fixture quiescence required')
        require(type(self.evidence.epoch) is int and self.evidence.epoch > 0, 'Invalid quiescence epoch')
        owner = state['owner']
        require((self.evidence.operator, self.evidence.job, self.evidence.attempt, self.evidence.epoch)
                == tuple(owner[k] for k in ('operator', 'job', 'attempt', 'epoch')),
                'Quiescence owner differs')
        require(self.verifier(self.evidence) is True, 'Fixture is not quiescent')


def capture_generation(authority, source, output, *, bundle, generation_id, quiescence,
                       protected_inputs=()):
    require(type(quiescence) is FixtureQuiescence, 'Independent fixture quiescence required')
    before = authority.inspect()
    require(before['eligible'] and before['state']['owner']['state'] == 'STOPPED',
            'Acknowledged stopped owner required')
    quiescence.verify(before['state'])
    owner = before['state']['owner']
    identity = dict(before['state']['binding'], generation_id=generation_id,
                    attempt_id=owner['attempt'], epoch_id=str(owner['epoch']))
    result = snapshot.snapshot(source, output, bundle=bundle, identity=identity,
                               runtime=before['state']['runtime'], stopped_writer=True, synthetic=True,
                               protected_inputs=(authority.path, authority.witness.path, *protected_inputs))
    quiescence.verify(before['state'])
    require(authority.inspect() == before, 'Authority changed during capture')
    return result


class PublicationVerification:
    """Pinned file verification, including a fresh local audit copy for SQLite checks."""

    def __init__(self, root, *, target, prior, compatible, bundle, identity, quiescence):
        self.root = Path(root)
        self.target = deepcopy(publication.pointer(target))
        self.prior = deepcopy(publication.pointer(prior)) if prior is not None else None
        self.compatible = deepcopy(compatible)
        self.bundle = Path(bundle)
        self.identity = identity
        self.quiescence = quiescence

    def intent(self, model, phase):
        state = model.inspect()['state']
        owner = state['owner']
        expected_owner = 'RECONCILED' if phase == 'RECONCILED_COMMITTED' else 'PUBLISHING'
        require(owner['state'] == expected_owner and
                (owner['attempt'], owner['epoch'], owner['job']) == self.identity,
                'Publication owner differs')
        require(state['runtime'] == self.compatible['runtime'] and
                all(self.target['identity'][k] == v for k, v in state['binding'].items()),
                'Publication runtime or binding differs')
        require(state['publication'] == dict(state=phase, target=self.target, expected_pointer=self.prior),
                'Publication intent differs')
        require((state['current'], state['previous']) == (self.target, self.prior) if phase == 'RECONCILED_COMMITTED'
                else state['current'] == self.prior, 'Authority ancestry differs')
        require(type(self.quiescence) is FixtureQuiescence, 'Independent fixture quiescence required')
        self.quiescence.verify(state)

    def verify(self, model):
        status = model.status()
        require(status in ('ACKNOWLEDGED', 'PENDING_ACKNOWLEDGMENT'), 'Authority unverified')
        intent = model.inspect()['state']['publication']
        require(type(intent) is dict, 'Publication intent missing')
        phase = intent['state']
        require(phase in ('GENERATION_AUTHORIZED', 'PREPARED', 'RECONCILED_COMMITTED'), 'Publication not prepared')
        self.intent(model, phase)
        observed = publication.current(self.root)
        if observed == self.prior:
            raise OperatorDispositionRequired('Pointer remains prior; explicit operator disposition required')
        require(phase in ('PREPARED', 'RECONCILED_COMMITTED'), 'Publication not prepared')
        if status == 'PENDING_ACKNOWLEDGMENT':
            require(model.records[-1]['action'] == 'reconcile', 'Unexpected pending decision')
        require(observed == self.target, 'Publication pointer differs')
        evidence = publication.recover(self.root, expected_current=self.target,
                                       expected_previous=self.prior, synthetic=True)
        require(evidence['status'] == 'VERIFIED_CURRENT', evidence.get('reason', 'Generation unverified'))
        # Existing archive validators only open SQLite in a fresh local audit copy.
        with tempfile.TemporaryDirectory(prefix='publication-audit-', dir=self.root.parent) as temporary:
            local = Path(temporary) / 'generation'
            shutil.copytree(self.root / self.target['identity']['generation_id'], local)
            snapshot.verify_generation(local, trusted=self.target, bundle=self.bundle, compatible=self.compatible)
        require(publication.current(self.root) == self.target, 'Pointer changed during verification')
        publication.verify_bytes(self.root / self.target['identity']['generation_id'], self.target)
        self.quiescence.verify(model.inspect()['state'])


class DurablePublication:
    def __init__(self, authority, root, *, target, prior, compatible, bundle, attempt, epoch, job,
                 quiescence, fault=_noop):
        self.authority = authority
        self.verification = PublicationVerification(
            root, target=target, prior=prior, compatible=compatible, bundle=bundle,
            identity=(attempt, epoch, job), quiescence=quiescence)
        self.fault = fault

    def _decision(self, action, payload, check):
        with _transaction(self.authority.path) as db:
            version = len(self.authority._load(db).records)
        return self.authority._decide(
            f'durable-publication:{version}', version, action, payload, check,
            reconciliation=self.verification if action == 'reconcile' else None)

    def publish(self, source, *, protected_inputs=()):
        v = self.verification
        view = self.authority.inspect()
        state = view['state']
        require(view['eligible'] and state['owner']['state'] == 'STOPPED', 'Acknowledged authorization required')
        require((state['owner']['attempt'], state['owner']['epoch'], state['owner']['job']) == v.identity,
                'Publication owner differs')
        require(state['publication'] == dict(state='GENERATION_AUTHORIZED', target=v.target, expected_pointer=v.prior)
                and state['current'] == v.prior, 'Acknowledged authorization differs')
        require(type(v.quiescence) is FixtureQuiescence, 'Independent fixture quiescence required')
        v.quiescence.verify(state)
        require(publication.current(v.root) == v.prior, 'Unexpected prior pointer')

        def start(model):
            current = model.inspect()
            require(current == view, 'Authorization changed')
            v.quiescence.verify(current['state'])

        self._decision('publish', {}, start)
        self._decision('prepare', {}, lambda model: v.intent(model, 'GENERATION_AUTHORIZED'))
        self.fault('after_prepared_ack')
        with _transaction(self.authority.path) as db:
            model = self.authority._load(db)
            require(model.status() == 'ACKNOWLEDGED', 'Prepared acknowledgment required')
            v.intent(model, 'PREPARED')
        def publication_boundary(point):
            self.fault(point)
            with _transaction(self.authority.path) as db:
                current = self.authority._load(db)
                require(current.status() == 'ACKNOWLEDGED', 'Prepared acknowledgment required')
                v.intent(current, 'PREPARED')

        publication.publish(
            source, v.root, bundle=v.bundle, compatible=v.compatible,
            expectations=dict(authorized=True, target=v.target, expected_pointer=v.prior), synthetic=True,
            protected_inputs=(self.authority.path, self.authority.witness.path, *protected_inputs),
            fault=publication_boundary)
        return self.recover()

    def recover(self):
        v = self.verification
        with _transaction(self.authority.path) as db:
            model = self.authority._load(db)
            v.verify(model)
            status = model.status()
            phase = model.inspect()['state']['publication']['state']
        if status == 'PENDING_ACKNOWLEDGMENT':
            self.fault('before_reconciliation_ack')
            self.authority.recover(reconciliation=v)
            self.fault('after_reconciliation_ack')
        elif phase == 'PREPARED':
            self.fault('before_reconciliation_store')
            self._decision('reconcile', dict(observed_pointer=v.target, verified=True), lambda _: None)
            self.fault('after_reconciliation_ack')
        with _transaction(self.authority.path) as db:
            model = self.authority._load(db)
            require(model.status() == 'ACKNOWLEDGED', 'Trusted commit not acknowledged')
            v.intent(model, 'RECONCILED_COMMITTED')
            v.verify(model)
        return 'COMMITTED'

