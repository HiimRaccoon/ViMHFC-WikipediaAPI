"""Offline SQLite control model with an independently stored synthetic witness."""
import json
import sqlite3
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from time import monotonic

from vimfc.domain import digest
from .trusted_control import SyntheticAuthority, name, require


@contextmanager
def _transaction(path, *, provision=False):
    mode = 'rwc' if provision else 'rw'
    db = sqlite3.connect(f'{path.as_uri()}?mode={mode}', uri=True)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    finally:
        db.close()


def _noop(_point):
    pass


class SQLiteWitness:
    """Synthetic witness. Its database must survive authority and scratch restore."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.available = True

    def _read(self, db, genesis):
        require(self.available, 'Witness unavailable')
        row = db.execute('SELECT genesis, version, head, pending FROM witness WHERE id=1').fetchone()
        require(row is not None and row[0] == genesis, 'Witness binding differs')
        return row[1:]

    def read(self, genesis):
        with _transaction(self.path) as db:
            return self._read(db, genesis)

    def stage(self, genesis, record):
        with _transaction(self.path) as db:
            expected = (record['expected_version'], record['previous_digest'], None)
            require(self._read(db, genesis) == expected, 'Witness predecessor differs')
            db.execute('UPDATE witness SET pending=? WHERE id=1', (digest(record),))

    def acknowledge(self, genesis, record):
        target = digest(record)
        with _transaction(self.path) as db:
            current = self._read(db, genesis)
            if current == (record['version'], target, None):
                return
            expected = (record['expected_version'], record['previous_digest'], target)
            require(current == expected, 'Witness intent differs')
            db.execute('UPDATE witness SET version=?, head=?, pending=NULL WHERE id=1',
                       (record['version'], target))


class _Contract(SyntheticAuthority):
    def _apply(self, state, action, payload):
        if action == 'activate':
            return super()._apply(state, 'launch', {'reservation_id': payload['reservation_id']})
        if action == 'bind_selection':
            from .selection_contract import binding
            binding(payload)
            require(state['owner']['state'] == 'RUNNING'
                    and payload['activation']['epoch'] == state['owner']['epoch'],
                    'Invalid selection binding owner')
            activation = next((r for r in reversed(self.records) if r['action'] in ('launch', 'activate')), None)
            require(activation is not None and activation['action'] == 'activate'
                    and payload['activation']['decision_id'] == activation['decision_id']
                    and payload['activation']['record_digest'] == digest(activation)
                    and activation['state']['owner'] == state['owner']
                    and activation['payload']['activation']['generation'] == payload['generation'],
                    'Selection binding activation differs')
            require(not any(r['action'] == action and r['payload']['activation'] == payload['activation']
                            for r in self.records), 'Selection already bound for activation')
            return
        if action != 'dispatch':
            return super()._apply(state, action, payload)
        reservation_id, units = payload['reservation_id'], payload['units']
        reservation = state['reservations'].get(reservation_id)
        owner = state['owner']
        require(owner['state'] == 'RUNNING' and reservation is not None
                and reservation['launched'] and reservation['attempt'] == owner['attempt'],
                'Invalid request reservation')
        require(payload['owner'] == owner and payload['allowance'] == reservation['allowance'],
                'Dispatch grant binding differs')
        require(type(units) is int and units > 0, 'Invalid or exhausted request allowance')
        spent = state.setdefault('spent', {})
        total = spent.get(reservation_id, 0) + units
        require(total <= reservation['allowance'], 'Invalid or exhausted request allowance')
        spent[reservation_id] = total


class TransactionalAuthority:
    """Explicit provisioning, expected version writes, and no automatic recovery."""

    def __init__(self, path, witness, *, authority_id, binding, runtime, budget_id,
                 ceiling, historical_debit, checkpoint=_noop):
        self.path = Path(path).resolve()
        self.witness = witness
        self.checkpoint = checkpoint
        self._config = (authority_id, binding.copy(), runtime, budget_id, ceiling, historical_debit)
        self.genesis = digest(_Contract(*self._config, witness=None).initial)
        require(self.path != witness.path and
                not (self.path.exists() and witness.path.exists() and self.path.samefile(witness.path)),
                'Authority and witness require separate files')

    def provision(self):
        require(not self.path.exists() and not self.witness.path.exists(), 'Control files already exist')
        with _transaction(self.path, provision=True) as db:
            db.execute('CREATE TABLE records (version INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)')
            with _transaction(self.witness.path, provision=True) as witness_db:
                witness_db.execute('CREATE TABLE witness (id INTEGER PRIMARY KEY CHECK(id=1), '
                                   'genesis TEXT NOT NULL, version INTEGER NOT NULL, head TEXT, pending TEXT)')
                witness_db.execute('INSERT INTO witness VALUES (1, ?, 0, NULL, NULL)', (self.genesis,))

    def _load(self, db):
        version, head, pending = self.witness.read(self.genesis)

        class Head:
            def read(self):
                return version, head

        model = _Contract(*self._config, witness=Head())
        for number, body, record_hash in db.execute('SELECT version, body, hash FROM records ORDER BY version'):
            record = json.loads(body)
            require(number == record['version'], 'Authority row version differs')
            model.records.append(record)
            model.record_digests.append(record_hash)
            model.decisions[record['decision_id']] = {
                key: record[key] for key in ('expected_version', 'action', 'payload')}
        status = model.status()
        if pending is None:
            require(status == 'ACKNOWLEDGED', 'Authority head differs from witness')
        else:
            require(status == 'PENDING_ACKNOWLEDGMENT' and model.record_digests[-1] == pending,
                    'Authority differs from pending witness intent')
        return model

    def inspect(self):
        with _transaction(self.path) as db:
            return self._load(db).inspect()

    @staticmethod
    def _verified_reconciliation(model, verification):
        from .durable_publication import PublicationVerification
        require(type(verification) is PublicationVerification, 'Checked publication verification required')
        verification.verify(model)

    def recover(self, *, reconciliation=None):
        """Acknowledge only a verified committed record matching the staged digest."""
        with _transaction(self.path) as db:
            model = self._load(db)
            if model.status() == 'PENDING_ACKNOWLEDGMENT':
                if model.records[-1]['action'] == 'reconcile':
                    self._verified_reconciliation(model, reconciliation)
                self.witness.acknowledge(self.genesis, model.records[-1])

    def decide(self, decision_id, expected_version, action, payload):
        require(action not in ('launch', 'activate', 'dispatch', 'reconcile', 'bind_selection'),
                'Use the appropriate control boundary')
        record, _ = self._decide(decision_id, expected_version, action, payload)
        return record

    def _decide(self, decision_id, expected_version, action, payload, check=_noop, *, reconciliation=None):
        with _transaction(self.path) as db:
            model = self._load(db)
            require(model.status() == 'ACKNOWLEDGED', 'Witness acknowledgment required')
            check(model)
            if action == 'reconcile':
                self._verified_reconciliation(model, reconciliation)
            record = model.propose(decision_id, expected_version, action, payload)
            if decision_id in model.decisions:
                return record, False
            db.execute('INSERT INTO records VALUES (?, ?, ?)',
                       (record['version'], json.dumps(record), digest(record)))
            self.checkpoint('before_stage')
            self.witness.stage(self.genesis, record)
            self.checkpoint('before_commit')
        self.checkpoint('after_commit')
        with _transaction(self.path) as db:
            model = self._load(db)
            require(model.record_digests[-1] == digest(record), 'Decision no longer current')
            self.checkpoint('before_ack')
            if action == 'reconcile':
                self._verified_reconciliation(model, reconciliation)
            self.witness.acknowledge(self.genesis, record)
            self.checkpoint('after_ack')
        return record, True

    @staticmethod
    def _owned(model, identity, state):
        view = model.inspect()
        owner = view['state']['owner']
        require(view['eligible'] and owner['state'] == state
                and (owner['attempt'], owner['epoch'], owner['job']) == identity, 'Invalid worker owner')

    def launch(self, decision_id, expected_version, *, attempt, epoch, job,
               reservation_id, ttl, mock_transport):
        return self._launch(decision_id, expected_version, attempt=attempt, epoch=epoch, job=job,
                            reservation_id=reservation_id, ttl=ttl, mock_transport=mock_transport)

    def _launch(self, decision_id, expected_version, *, attempt, epoch, job,
                reservation_id, ttl, mock_transport, activation=None, check=_noop):
        name(attempt)
        name(job)
        name(reservation_id)
        require(type(epoch) is int and epoch > 0, 'Invalid worker epoch')
        require(type(ttl) in (int, float) and 0 < ttl <= 86400 and isfinite(ttl)
                and callable(mock_transport), 'Invalid mock worker configuration')
        identity = (attempt, epoch, job)
        deadline = monotonic() + ttl

        def validate(model):
            self._owned(model, identity, 'CLAIMED')
            check(model)

        payload = {'reservation_id': reservation_id}
        if activation is not None:
            payload['activation'] = activation
        record, fresh = self._decide(decision_id, expected_version,
                                     'launch' if activation is None else 'activate', payload, validate)
        require(fresh, 'Launch already used')
        launch_digest = digest(record)
        owner = record['state']['owner']
        allowance = record['state']['reservations'][reservation_id]['allowance']

        def authorize(model):
            self._owned(model, identity, 'RUNNING')
            require(monotonic() < deadline and model.decisions.get(decision_id) is not None
                    and model.record_digests[record['expected_version']] == launch_digest,
                    'Worker authority expired or absent')

        with _transaction(self.path) as db:
            self.checkpoint('before_worker_issue')
            authorize(self._load(db))

        def request(request_id, version, units):
            require(type(units) is int and units > 0, 'Invalid request units')

            payload = {'owner': owner, 'launch_digest': launch_digest, 'reservation_id': reservation_id,
                       'allowance': allowance, 'units': units}
            grant, fresh_grant = self._decide(request_id, version, 'dispatch', payload, authorize)
            require(fresh_grant, 'Request already used')
            self.checkpoint('before_dispatch')
            # Admission is ordered against stop by the authority transaction.
            # After admission the mock call is in flight, even if stop wins the next transaction.
            with _transaction(self.path) as db:
                model = self._load(db)
                authorize(model)
                require(model.record_digests[-1] == digest(grant), 'Dispatch grant no longer current')
            self.checkpoint('after_dispatch_admission')
            result = mock_transport(grant['payload']['units'])
            self.checkpoint('after_dispatch')
            return result

        return request

