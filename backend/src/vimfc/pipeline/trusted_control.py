"""In-memory synthetic control model. It supplies no production security or durability."""
from copy import deepcopy

from vimfc.domain import Incompatible, digest
from .generation_publication import pointer


def require(condition, message):
    if not condition:
        raise Incompatible(message)


def fields(value, keys):
    require(type(value) is dict and set(value) == set(keys), 'Malformed fields')


def name(value):
    require(type(value) is str and bool(value.strip()), 'Nonempty string required')


class FakeWitness:
    """Independent in-memory witness; callers must not treat it as a real trust anchor."""
    def __init__(self):
        self.head = (0, None)
        self.available = True
        self.fail_ack = False

    def read(self):
        require(self.available, 'Witness unavailable')
        return self.head

    def acknowledge(self, record):
        require(self.available and not self.fail_ack, 'Witness acknowledgment failed')
        head = (record['version'], digest(record))
        if self.head == head:
            return
        require(self.head == (record['version'] - 1, record['previous_digest']), 'Witness predecessor differs')
        self.head = head


class SyntheticAuthority:
    def __init__(self, authority_id, binding, runtime, budget_id, ceiling, historical_debit, witness):
        for item in (authority_id, runtime, budget_id): name(item)
        fields(binding, ('workspace_id', 'run_id', 'bundle_id'))
        for item in binding.values(): name(item)
        require(type(ceiling) is int and type(historical_debit) is int and 0 <= historical_debit <= ceiling, 'Invalid budget')
        self.witness = witness
        self.records = []
        self.record_digests = []
        self.decisions = {}
        self.initial = {'schema': 1, 'authority_id': authority_id, 'binding': deepcopy(binding),
                        'runtime': runtime, 'budget_id': budget_id, 'ceiling': ceiling,
                        'historical_debit': historical_debit, 'allocated': historical_debit,
                        'owner': {'state': 'UNOWNED', 'epoch': 0, 'attempt': None, 'operator': None, 'job': None},
                        'attempts': [], 'reservations': {}, 'current': None, 'previous': None,
                        'publication': None}

    def status(self):
        try:
            head = self.witness.read()
            require(len(self.records) == len(self.record_digests), 'Authority record count differs')
            prior = None
            for number, record in enumerate(self.records, 1):
                require(set(record) == {'schema', 'version', 'decision_id', 'expected_version', 'previous_digest', 'action', 'payload', 'state'}, 'Malformed record')
                require(record['schema'] == 1 and record['version'] == number and record['expected_version'] == number - 1 and record['previous_digest'] == prior, 'Broken authority chain')
                prior = digest(record)
                require(prior == self.record_digests[number - 1], 'Authority record altered')
            if head == (len(self.records), prior):
                return 'ACKNOWLEDGED'
            if self.records and head == (len(self.records) - 1, self.records[-1]['previous_digest']):
                return 'PENDING_ACKNOWLEDGMENT'
        except (Incompatible, AttributeError, TypeError, KeyError, ValueError):
            pass
        return 'AUTHORITY_UNVERIFIED'

    def inspect(self):
        status = self.status()
        state = deepcopy(self.records[-1]['state'] if self.records else self.initial)
        if status == 'ACKNOWLEDGED' and state['owner']['state'] == 'OWNER_UNKNOWN':
            status = 'RECONCILIATION_REQUIRED'
        return {'status': status, 'state': state,
                'eligible': bool(self.records) and status == 'ACKNOWLEDGED'}

    def propose(self, decision_id, expected_version, action, payload):
        name(decision_id); name(action)
        require(type(expected_version) is int and type(payload) is dict, 'Invalid decision')
        request = {'expected_version': expected_version, 'action': action, 'payload': deepcopy(payload)}
        if decision_id in self.decisions:
            require(self.decisions[decision_id] == request, 'Decision ID reused')
            require(self.status() != 'AUTHORITY_UNVERIFIED', 'Authority unverified')
            return deepcopy(self.records[expected_version])
        require(self.status() == 'ACKNOWLEDGED', 'Authority unverified or pending')
        require(expected_version == len(self.records), 'Stale authority version')
        state = deepcopy(self.records[-1]['state'] if self.records else self.initial)
        require(bool(self.records) or action == 'initialize', 'Initialization required')
        self._apply(state, action, payload)
        return {'schema': 1, 'version': expected_version + 1, 'decision_id': decision_id,
                'expected_version': expected_version, 'previous_digest': digest(self.records[-1]) if self.records else None,
                'action': action, 'payload': deepcopy(payload), 'state': state}

    def store(self, record):
        fields(record, ('schema', 'version', 'decision_id', 'expected_version', 'previous_digest', 'action', 'payload', 'state'))
        require(record['schema'] == 1, 'Unsupported schema')
        expected = self.propose(record['decision_id'], record['expected_version'], record['action'], record['payload'])
        require(record == expected, 'Record or predecessor differs')
        if record['decision_id'] not in self.decisions:
            self.records.append(deepcopy(record))
            self.record_digests.append(digest(record))
            self.decisions[record['decision_id']] = {'expected_version': record['expected_version'], 'action': record['action'], 'payload': deepcopy(record['payload'])}
        return deepcopy(record)

    def acknowledge(self):
        require(self.status() in ('ACKNOWLEDGED', 'PENDING_ACKNOWLEDGMENT') and self.records, 'Authority unverified')
        self.witness.acknowledge(self.records[-1])

    def _apply(self, state, action, payload):
        owner = state['owner']; status = owner['state']
        if action == 'initialize':
            fields(payload, ()); require(not self.records, 'Already initialized')
        elif action == 'claim':
            fields(payload, ('operator', 'job', 'attempt', 'epoch', 'fencing_evidence'))
            for key in ('operator', 'job', 'attempt', 'fencing_evidence'): name(payload[key])
            require(status in ('UNOWNED', 'RELEASED') and type(payload['epoch']) is int and payload['epoch'] > owner['epoch'] and payload['attempt'] not in state['attempts'], 'Invalid owner claim')
            state['attempts'].append(payload['attempt'])
            state['owner'] = {'state': 'CLAIMED', **{k: payload[k] for k in ('operator', 'job', 'attempt', 'epoch')}}
        elif action == 'reserve':
            fields(payload, ('reservation_id', 'attempt', 'allowance'))
            name(payload['reservation_id'])
            require(status == 'CLAIMED' and payload['attempt'] == owner['attempt'] and payload['reservation_id'] not in state['reservations'] and type(payload['allowance']) is int and payload['allowance'] > 0 and state['allocated'] + payload['allowance'] <= state['ceiling'], 'Invalid reservation')
            require(all(r['attempt'] != payload['attempt'] for r in state['reservations'].values()), 'Attempt already reserved')
            state['allocated'] += payload['allowance']
            state['reservations'][payload['reservation_id']] = {'attempt': payload['attempt'], 'allowance': payload['allowance'], 'launched': False}
        elif action == 'launch':
            fields(payload, ('reservation_id',))
            reservation = state['reservations'].get(payload['reservation_id'])
            require(status == 'CLAIMED' and reservation is not None and reservation['attempt'] == owner['attempt'] and not reservation['launched'], 'Invalid launch')
            reservation['launched'] = True; owner['state'] = 'RUNNING'
        elif action in ('stop', 'publish', 'release'):
            fields(payload, ())
            transitions = {'stop': ('RUNNING', 'STOPPED'), 'publish': ('STOPPED', 'PUBLISHING'), 'release': ('RECONCILED', 'RELEASED')}
            before, after = transitions[action]
            require(status == before, 'Invalid owner transition')
            if action == 'publish':
                require(state['publication'] is not None and state['publication']['state'] == 'GENERATION_AUTHORIZED', 'Generation not authorized')
            owner['state'] = after
        elif action == 'authorize_generation':
            fields(payload, ('target', 'expected_pointer'))
            require(status == 'STOPPED' and payload['expected_pointer'] == state['current'], 'Invalid generation authorization')
            target = pointer(payload['target']); binding = state['binding']
            require(all(target['identity'][k] == binding[k] for k in binding) and target['identity']['attempt_id'] == owner['attempt'] and target['identity']['epoch_id'] == str(owner['epoch']) and target['parent_generation'] == (state['current']['identity']['generation_id'] if state['current'] else None) and (state['current'] is None or target['identity']['generation_id'] != state['current']['identity']['generation_id']), 'Generation binding differs')
            require(not any(record['action'] == 'authorize_generation' and record['payload']['target']['identity']['generation_id'] == target['identity']['generation_id'] for record in self.records), 'Generation ID already authorized')
            state['publication'] = {'state': 'GENERATION_AUTHORIZED', 'target': deepcopy(target), 'expected_pointer': deepcopy(state['current'])}
        elif action == 'prepare':
            fields(payload, ())
            require(status == 'PUBLISHING' and state['publication'] and state['publication']['state'] == 'GENERATION_AUTHORIZED', 'Invalid publication preparation')
            state['publication']['state'] = 'PREPARED'
        elif action == 'reconcile':
            fields(payload, ('observed_pointer', 'verified'))
            publication = state['publication']
            require(status == 'PUBLISHING' and publication and publication['state'] == 'PREPARED' and payload['verified'] is True and payload['observed_pointer'] == publication['target'], 'Invalid reconciliation')
            state['previous'] = state['current']; state['current'] = deepcopy(publication['target'])
            publication['state'] = 'RECONCILED_COMMITTED'; owner['state'] = 'RECONCILED'
        elif action == 'unknown':
            fields(payload, ('reason',)); name(payload['reason'])
            owner['state'] = 'OWNER_UNKNOWN'
            if state['publication']: state['publication']['state'] = 'UNKNOWN_REQUIRES_OPERATOR_REVIEW'
        else:
            raise Incompatible('Unsupported decision')

