"""Fixture-only activation from a pinned generation and independently checked evidence."""
from copy import deepcopy
from dataclasses import asdict, dataclass

from .backup_recovery import restore_backup
from .trusted_control import require


@dataclass(frozen=True)
class SyntheticTermination:
    operator: str
    job: str
    attempt: str
    epoch: int
    terminated: bool


class SyntheticActivation:
    """Trusted fixture setup supplies the generation pins and termination verifier."""

    def __init__(self, authority, *, expected_current, compatible, verify_termination,
                 expected_previous=None):
        self.authority = authority
        self.current = deepcopy(expected_current)
        self.previous = deepcopy(expected_previous)
        self.compatible = deepcopy(compatible)
        self.verify_termination = verify_termination

    def activate(self, decision_id, expected_version, *, source, output, bundle, evidence,
                 attempt, epoch, job, reservation_id, ttl, mock_transport, protected_inputs=()):
        require(type(evidence) is SyntheticTermination and evidence.terminated is True
                and type(evidence.epoch) is int and evidence.epoch > 0,
                'Verified synthetic termination required')
        require(callable(self.verify_termination) and self.verify_termination(evidence) is True,
                'Independent synthetic termination verification failed')
        receipt = restore_backup(source, output, expected_current=self.current,
                                 expected_previous=self.previous, compatible=self.compatible,
                                 bundle=bundle, authority=self.authority, synthetic=True,
                                 protected_inputs=protected_inputs)
        target = receipt['current']

        def check(model):
            state = model.inspect()['state']
            intent = state['publication']
            require(intent is None or intent['state'] == 'RECONCILED_COMMITTED',
                    'Unresolved publication intent')
            if state['current'] is not None:
                require((state['current'], state['previous']) == (target, receipt['previous']),
                        'Authority generation differs')
            previous_launch = next((r for r in reversed(model.records)
                                    if r['action'] in ('launch', 'activate')), None)
            require(previous_launch is not None, 'Previous launch is required')
            previous = previous_launch['state']['owner']
            require((evidence.operator, evidence.job, evidence.attempt, evidence.epoch)
                    == tuple(previous[k] for k in ('operator', 'job', 'attempt', 'epoch')),
                    'Termination evidence differs from previous worker')
            identity = target['identity']
            require(identity['attempt_id'] == previous['attempt'] and identity['epoch_id'] == str(previous['epoch'])
                    and epoch > previous['epoch'], 'Stale generation or owner epoch')

        worker = self.authority._launch(
            decision_id, expected_version, attempt=attempt, epoch=epoch, job=job,
            reservation_id=reservation_id, ttl=ttl, mock_transport=mock_transport,
            activation={'generation': target, 'synthetic_termination': asdict(evidence),
                        'selection_epoch_bound': False}, check=check)
        return receipt, worker

