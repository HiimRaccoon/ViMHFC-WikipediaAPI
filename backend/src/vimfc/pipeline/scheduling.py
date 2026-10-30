"""Serial, persisted candidate deferrals. Scheduling is not classification input."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import time

from vimfc.domain import SourceUnavailable, canonical, now, at_or_before, InvalidConfig, Incompatible


SCHEDULER_VERSION = 'host-aware-1'


def transient_source_code(code):
    """Match HTTP retry policy without assuming only common 5xx codes exist."""
    if isinstance(code, str) and code.startswith('http_'):
        try:
            status = int(code[5:])
            return status == 429 or status >= 500
        except ValueError:
            return False
    return code in {'maxlag', 'host_cooldown', 'ratelimited', 'readonly', 'network_timeout', 'proxy_error',
                    'stage_cooldown', 'stage_lease', 'host_circuit_open', 'internal_api_error_DBConnectionError'}


def failure_record(page_id, error):
    retry_at = error.retry_at
    if error.retryable and not retry_at:
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace('+00:00', 'Z')
    return {'page_id': page_id, 'status': 'deferred' if error.retryable else 'source_failed',
            'error': str(error), 'retryable': error.retryable, 'retry_at': retry_at,
            'error_code': error.code, 'error_details': error.details}


class CandidateScheduler:
    def __init__(self, store, run_id, *, max_wait_seconds=0, retry_rounds=0,
                 sleep=time.sleep, clock=None, progress=None,host_guard=None,restore_quarantine=None):
        if not 0 <= max_wait_seconds <= 3600 or not 0 <= retry_rounds <= 5:
            raise InvalidConfig('Scheduler wait must be 0..3600 seconds; retry rounds 0..5')
        self.store, self.run_id = store, run_id
        self.max_wait_seconds, self.retry_rounds = max_wait_seconds, retry_rounds
        self.sleep, self.clock, self.progress = sleep, clock or (lambda: datetime.now(timezone.utc)), progress
        self.attempted, self.visits, self.waited = set(), 0, 0.0
        self.host_guard=host_guard
        self.restore_quarantine=restore_quarantine
        self.host_pauses={}

    def host_blocked(self,record):
        site=record.get('error_details',{}).get('site')
        if not site or not self.host_guard:return False
        state=self.host_guard()
        if state.get('site')==site and state.get('blocked'):
            self.host_pauses[site]=state
            return True
        return False

    def stamp(self):
        return self.clock().isoformat().replace('+00:00', 'Z')

    def run(self, candidates, work, observe=None, *, initial_records=None):
        records = dict(initial_records or {})
        self.checkpoint_restored = len(records)
        pending = [c for c in candidates if c['page_id'] not in records]
        for round_number in range(self.retry_rounds + 1):
            for candidate in pending:
                page = candidate['page_id']
                row = self.store.db.execute('SELECT * FROM candidate_work WHERE run_id=? AND page_id=?', (self.run_id, page)).fetchone()
                invoked = False
                previous=json.loads(row['record_json']) if row else {}
                if row and row['status']=='quarantined_integrity':
                    if not self.restore_quarantine:raise Incompatible('Quarantine restore requires explicit reviewed validation')
                    record=self.restore_quarantine(page,previous)
                    self.checkpoint_restored+=1
                elif row and row['status']=='deferred' and self.host_blocked(previous):
                    # Preserve candidate/stage attempts and deadlines. The host
                    # gate supplies the later effective retry time separately.
                    record=previous
                elif row and row['status'] == 'deferred' and row['next_retry_at'] and not at_or_before(row['next_retry_at'], self.stamp()):
                    record = json.loads(row['record_json'])
                else:
                    invoked = True
                    self.attempted.add(page)
                    self.visits += 1
                    try:
                        record = work(candidate)
                    except SourceUnavailable as error:
                        record = failure_record(page, error)
                    except KeyboardInterrupt:
                        record = failure_record(page, SourceUnavailable('Interrupted candidate; resume preserved stages', retryable=True, code='interrupted', retry_at=self.stamp()))
                        self.save(record, invoked=True)
                        raise
                    if record.get('status') == 'pending_source':
                        record = {**record, 'status': 'deferred'}
                    self.save(record, invoked=True)
                records[page] = record
                if observe:
                    observe(candidate, record, records, invoked)
            pending = [c for c in candidates if records[c['page_id']].get('status') == 'deferred'
                       and not self.host_blocked(records[c['page_id']])]
            if not pending or round_number == self.retry_rounds:
                break
            deadlines = [records[c['page_id']].get('retry_at') for c in pending]
            if not all(deadlines):
                break
            deadline = min(deadlines, key=lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')))
            seconds = max(0.0, (datetime.fromisoformat(deadline.replace('Z', '+00:00')) - self.clock()).total_seconds())
            if seconds > self.max_wait_seconds - self.waited:
                break
            if seconds:
                if self.progress:
                    self.progress({'event': 'source_wait', 'run_id': self.run_id, 'retry_at': deadline,
                                   'wait_seconds': seconds, 'deferred': len(pending)})
                # Short waits allow cancellation and keep the terminal responsive.
                remaining = seconds
                while remaining > 0:
                    chunk = min(5.0, remaining)
                    self.sleep(chunk)
                    self.waited += chunk
                    remaining -= chunk
        return [records[c['page_id']] for c in candidates]

    def save(self, record, *, invoked):
        status = record.get('status')
        status = status if status in ('deferred', 'source_failed','quarantined_integrity') else 'processed'
        # Store only queue metadata, never another copy of entity/lead payloads.
        brief = {k: record[k] for k in ('page_id', 'status', 'error', 'retryable', 'retry_at', 'error_code', 'error_details','case_id') if k in record}
        self.store.db.execute('''INSERT INTO candidate_work VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(run_id,page_id) DO UPDATE SET status=excluded.status,record_json=excluded.record_json,
            next_retry_at=excluded.next_retry_at,attempt_count=candidate_work.attempt_count+excluded.attempt_count,updated_at=excluded.updated_at''',
            (self.run_id, record['page_id'], status, canonical(brief), record.get('retry_at') if status == 'deferred' else None, int(invoked), now()))

    def report(self, records):
        states = Counter(r.get('status') for r in records)
        result={'attempted': len(self.attempted),
                'processed': len(records) - states['deferred'] - states['source_failed'] - states['quarantined_integrity'],
                'deferred': states['deferred'], 'source_failed': states['source_failed']}
        if states['quarantined_integrity']:result['quarantined_integrity']=states['quarantined_integrity']
        return result
