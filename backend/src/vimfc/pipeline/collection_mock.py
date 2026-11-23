"""Explicit offline collection fixture; never a production transport selector."""
import os
import uuid

import httpx

from vimfc.domain import Incompatible
from vimfc.source.mediawiki import MediaWikiClient
from .budget_transport import SyntheticBudgetTransport


def require_offline():
    if os.environ.get('VIMFC_OFFLINE', '').casefold() not in ('1', 'true', 'yes'):
        raise Incompatible('Synthetic collection requires VIMFC_OFFLINE=1')


class SyntheticCollection(httpx.BaseTransport):
    """One invocation, an independently launched budget gate, and no alternate mounts."""

    def __init__(self, gate):
        if type(gate) is not SyntheticBudgetTransport or type(gate._fake) is not httpx.MockTransport:
            raise Incompatible('Explicit synthetic budget gate with MockTransport required')
        self.gate = gate
        self._bound = False
        self._guard = None

    def client(self, store, header, max_requests, guard, config):
        require_offline()
        binding = dict(workspace_id=header['binding_id'], run_id=header['context']['run_id'],
                       bundle_id=header['bundle_id'])
        if (self._bound or self.gate.policy['binding'] != binding
                or self.gate.policy['workspace_limit'] != header['request_limit']
                or self.gate.policy['invocation_limit'] != max_requests):
            raise Incompatible('Synthetic collection binding or limits differ; fresh invocation required')
        self._bound = True
        self._store, self._guard = store, guard
        self._counters(before=True)
        return MediaWikiClient(config['contact'], user_agent=config.get('user_agent') or 'ViMFC/0.1',
                               attempts=1, transport=self)

    def _counters(self, *, before):
        state = self.gate._model().inspect()['state']
        durable = state['historical_debit'] + sum(state.get('spent', {}).values())
        db = self._store.db
        local = int(db.execute("SELECT value FROM collection_control WHERE key='attempts'").fetchone()[0])
        row = db.execute("SELECT value FROM collection_control WHERE key='last_request'").fetchone()
        last = float(row[0]) if row else 0
        if before:
            if local < 0 or local > durable:
                raise Incompatible('Collection counter exceeds durable accounting; review cutover')
            self.gate.workspace.update(attempts=local, last_request=last)
        # A restored collection checkpoint cannot replenish lifetime spending.
        with self._store.transaction():
            db.execute("UPDATE collection_control SET value=? WHERE key='attempts'", (str(max(local, durable)),))

    def handle_request(self, request):
        require_offline()
        if self._guard is None or type(self.gate._fake) is not httpx.MockTransport:
            raise Incompatible('Bound collection guard and MockTransport required')
        self._counters(before=True)
        self._guard(request)
        self.gate.authorize(request, request_id='collection-' + uuid.uuid4().hex,
                            expected_version=len(self.gate._model().records))
        try:
            response = self.gate.handle_request(request)
        except BaseException as error:
            try:
                self._counters(before=False)
            except BaseException as sync_error:
                error.add_note(f'Counter synchronization also failed: {type(sync_error).__name__}: {sync_error}')
            raise
        self._counters(before=False)
        return response

    def close(self):
        self._guard = None
        self.gate.close()
