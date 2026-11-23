"""Exercise the real historical collector behind durable, fake-only admission."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from vimfc.domain import Incompatible, SourceUnavailable
from vimfc.pipeline import incremental_collection as collection
from vimfc.pipeline.budget_transport import SyntheticBudgetTransport
from vimfc.pipeline.collection_mock import SyntheticCollection
from vimfc.pipeline.transactional_control import TransactionalAuthority, SQLiteWitness
from tests.integration.test_incremental_collection import prepared, factory
from tests.integration.test_budget_transport import Clock, OWNER


def authority(root, header):
    binding = dict(workspace_id=header['binding_id'], run_id=header['context']['run_id'], bundle_id=header['bundle_id'])
    return TransactionalAuthority(root / 'authority.sqlite', SQLiteWitness(root / 'witness.sqlite'),
        authority_id='collection', binding=binding, runtime='fixture', budget_id='budget',
        ceiling=20, historical_debit=0)


def setup(tmp_path, config, monkeypatch, *, allowance=10, limit=10, request_limit=None):
    monkeypatch.setenv('VIMFC_OFFLINE', '1')
    if request_limit is None:
        _, bundle, work, header = prepared(tmp_path, config)
    else:
        from tests.integration.test_incremental_collection import inputs
        from vimfc.pipeline.collection_bundle import export_bundle
        context, inventory, registry = inputs(tmp_path, config)
        bundle, work = tmp_path / 'portable bundle', tmp_path / 'linux-workspace'
        export_bundle(context, inventory, registry, bundle)
        header = collection.prepare_collection(bundle, work, target=20, request_limit=request_limit)
    clock = Clock()
    monkeypatch.setattr(collection, 'time', SimpleNamespace(time=clock, sleep=clock.sleep, monotonic=time.monotonic))
    a = authority(tmp_path, header)
    a.provision()
    a.decide('init', 0, 'initialize', {})
    a.decide('claim', 1, 'claim', dict(OWNER, operator='operator', fencing_evidence='synthetic'))
    a.decide('reserve', 2, 'reserve', dict(reservation_id='reservation', attempt='attempt', allowance=allowance))
    calls = []
    original = factory(calls)()
    handler = original.client._transport.handler
    original.client.close()

    def checked(request):
        records = gate._model().records
        assert records[-1]['action'] == 'dispatch' and a.inspect()['eligible']
        assert spent(a) == len(calls) + 1
        return handler(request)

    gate = SyntheticBudgetTransport(a, httpx.MockTransport(checked), budget_id='budget', binding=a._config[1],
        workspace_limit=header['request_limit'], invocation_id='invocation', invocation_limit=limit,
        clock=clock, sleep=clock.sleep)
    gate.launch('launch', 3, **OWNER, reservation_id='reservation', ttl=60)
    return a, gate, calls, bundle, work, header


def spent(a):
    state = a.inspect()['state']
    return state['historical_debit'] + sum(state.get('spent', {}).values())


def run(gate, bundle, work, **kwargs):
    return collection.collect_batch(bundle, work, mode='acquire', through=5, max_requests=kwargs.pop('max_requests', 10),
                                    synthetic_budget=SyntheticCollection(gate), **kwargs)


def test_real_collector_lookup_content_and_cached_resume(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    result = run(gate, bundle, work)
    assert len(calls) == spent(a) == result['summary']['physical_requests'] == 4
    assert sum('rvstart' in p for p in calls) == sum('revids' in p for p in calls) == 2
    assert len([r for r in gate._model().records if r['action'] == 'http_outcome']) == 4
    assert len({r['decision_id'] for r in gate._model().records if r['action'] == 'dispatch'}) == 4
    # No client or new capability is needed for a fully cached checkpoint.
    result = run(gate, bundle, work)
    assert result['summary']['physical_requests'] == 0 and spent(a) == 4


@pytest.mark.parametrize('damage', ['binding', 'limit', 'network', 'factory', 'offline', 'missing-hook'])
def test_configuration_and_unbound_dispatch_fail_closed(tmp_path, config, monkeypatch, damage):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    fixture = SyntheticCollection(gate)
    kwargs = {}
    if damage == 'binding': gate.policy['binding'] = dict(gate.policy['binding'], run_id='foreign')
    if damage == 'limit': gate.policy['invocation_limit'] = 11
    if damage == 'network': kwargs['allow_network'] = True
    if damage == 'factory': kwargs['client_factory'] = factory(calls)
    if damage == 'offline': monkeypatch.delenv('VIMFC_OFFLINE')
    with pytest.raises(Incompatible):
        if damage == 'missing-hook':
            fixture.handle_request(httpx.Request('GET', 'https://vi.wikipedia.org/w/api.php?maxlag=5'))
        else:
            collection.collect_batch(bundle, work, mode='acquire', through=5, max_requests=10,
                                     synthetic_budget=fixture, **kwargs)
    assert calls == [] and spent(a) == 0


@pytest.mark.parametrize('kind', ['timeout', 'maxlag', '429', '503'])
def test_real_retry_and_durable_cooldown(tmp_path, config, monkeypatch, kind):
    a, gate, calls, bundle, work, header = setup(tmp_path, config, monkeypatch)
    def fail(request):
        calls.append(dict(request.url.params))
        if kind == 'timeout': raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200, json={'error': {'code': 'maxlag'}}) if kind == 'maxlag' else httpx.Response(int(kind), headers={'Retry-After': '5'})
    gate._fake = httpx.MockTransport(fail)
    with pytest.raises(SourceUnavailable): run(gate, bundle, work)
    assert spent(a) == len(calls) == 1
    # Source failure/cooldown checkpoint refuses an immediate invocation before HTTP.
    new = SyntheticBudgetTransport(a, httpx.MockTransport(fail), budget_id='budget', binding=a._config[1],
        workspace_limit=header['request_limit'], invocation_id='not-launched', invocation_limit=10)
    with pytest.raises(SourceUnavailable): run(new, bundle, work)
    assert spent(a) == len(calls) == 1


@pytest.mark.parametrize('damage', ['stop', 'epoch', 'unresolved', 'duplicate', 'reservation', 'restored'])
def test_denials_do_not_reach_mock(tmp_path, config, monkeypatch, damage):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch, allowance=1 if damage in ('reservation', 'restored') else 10)
    if damage in ('reservation', 'restored'):
        with pytest.raises(Incompatible): run(gate, bundle, work)
        assert len(calls) == spent(a) == 1
        if damage == 'restored':
            with sqlite3.connect(work / 'pipeline.sqlite') as db:
                db.execute("UPDATE collection_control SET value='0' WHERE key='attempts'")
            # Fresh process has no old capability, even with restored counters.
            fresh = SyntheticBudgetTransport(authority(tmp_path, json.loads((work / 'workspace.json').read_text())),
                httpx.MockTransport(lambda r: pytest.fail('unaccounted I/O')), **gate.policy)
            with pytest.raises(Incompatible): run(fresh, bundle, work)
            assert spent(a) == 1
        return
    if damage in ('stop', 'epoch'):
        a.decide('stop', 4, 'stop', {})
        if damage == 'epoch':
            a.decide('release', 5, 'release_stopped', {})
            a.decide('next', 6, 'claim', dict(OWNER, attempt='next', epoch=2, operator='op', fencing_evidence='fixture'))
    elif damage == 'duplicate':
        monkeypatch.setattr('vimfc.pipeline.collection_mock.uuid.uuid4', lambda: SimpleNamespace(hex='duplicate'))
    else:
        a.checkpoint = lambda p: (_ for _ in ()).throw(KeyboardInterrupt()) if p == 'before_dispatch' else None
    with pytest.raises((Incompatible, KeyboardInterrupt)):
        run(gate, bundle, work)
    assert len(calls) == int(damage == 'duplicate')
    assert spent(a) == int(damage in ('duplicate', 'unresolved'))


def test_continuation_retry_and_send_without_hooks_still_charge(tmp_path, config, monkeypatch):
    a, gate, calls, _, work, header = setup(tmp_path, config, monkeypatch)
    from vimfc.storage.pipeline_store import PipelineStore
    responses = [httpx.Response(503), httpx.Response(200, json={'continue': {'continue': 'next'}}),
                 httpx.Response(200, json={'query': {}})]
    gate._fake = httpx.MockTransport(lambda r: calls.append(dict(r.url.params)) or responses.pop(0))
    fixture = SyntheticCollection(gate)
    with PipelineStore(work / 'pipeline.sqlite') as store:
        def guard(request):
            with store.transaction():
                store.db.execute("UPDATE collection_control SET value=CAST(value AS INT)+1 WHERE key='attempts'")
        with fixture.client(store, header, 10, guard, {'contact': 'fixture@example.com'}) as client:
            client.attempts = 2
            client.sleep = gate.sleep
            assert len(list(client.continued('viwiki', {'action': 'query'}))) == 2
            assert len(calls) == spent(a) == 3 and calls[-1]['continue'] == 'next'
            client.client.event_hooks['request'].clear()
            gate._fake = httpx.MockTransport(lambda r: calls.append(dict(r.url.params)) or httpx.Response(200))
            request = client.client.build_request('GET', 'https://vi.wikipedia.org/w/api.php?maxlag=5')
            assert client.client.send(request).status_code == 200
            assert len(calls) == spent(a) == 4


def next_gate(a, gate):
    version = len(gate._model().records)
    a.decide('stop-next', version, 'stop', {})
    a.decide('release-next', version + 1, 'release_stopped', {})
    a.decide('claim-next', version + 2, 'claim', dict(OWNER, attempt='next', epoch=2, operator='op', fencing_evidence='fixture'))
    a.decide('reserve-next', version + 3, 'reserve', dict(reservation_id='next', attempt='next', allowance=10))
    fresh = SyntheticBudgetTransport(a, gate._fake, **dict(gate.policy, invocation_id='next'),
                                    clock=gate.clock, sleep=gate.sleep)
    fresh.launch('launch-next', version + 4, attempt='next', epoch=2, job='job', reservation_id='next')
    return fresh


def test_restored_checkpoint_continues_in_new_epoch_without_refund(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, header = setup(tmp_path, config, monkeypatch)
    from vimfc.storage.pipeline_store import PipelineStore
    with PipelineStore(work / 'pipeline.sqlite') as store:
        prefix = next(i for i, c in enumerate(store.candidates(header['context']['run_id']), 1) if c['page_id'] in (4, 5))
    collection.collect_batch(bundle, work, mode='acquire', through=prefix, max_requests=10,
                             synthetic_budget=SyntheticCollection(gate))
    assert spent(a) == len(calls) == 2
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        db.execute("UPDATE collection_control SET value='0' WHERE key IN ('attempts','last_request')")
    fresh = next_gate(a, gate)
    result = run(fresh, bundle, work)
    assert result['summary']['cumulative_requests'] == spent(a) == len(calls) == 4


def test_unresolved_charge_blocks_even_fresh_owner(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    a.checkpoint = lambda p: (_ for _ in ()).throw(KeyboardInterrupt()) if p == 'before_dispatch' else None
    with pytest.raises(KeyboardInterrupt): run(gate, bundle, work)
    a.checkpoint = lambda _: None
    fresh = next_gate(a, gate)
    with pytest.raises(Incompatible, match='Unresolved'):
        run(fresh, bundle, work)
    assert spent(a) == 1 and calls == []


def test_collector_move_log_continuations_are_separate_charges(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        db.execute("DELETE FROM identity_resolutions WHERE lookup_title LIKE '__move_index__:%'")
    original = gate._fake.handler
    def handler(request):
        if request.url.params.get('list') != 'logevents': return original(request)
        calls.append(dict(request.url.params))
        assert spent(a) == len(calls)
        payload = {'query': {'logevents': []}}
        if 'lecontinue' not in request.url.params:
            payload['continue'] = {'continue': '-||', 'lecontinue': 'cursor'}
        return httpx.Response(200, json=payload)
    gate._fake = httpx.MockTransport(handler)
    run(gate, bundle, work)
    assert len(calls) == spent(a) == 6
    logs = [p for p in calls if p.get('list') == 'logevents']
    assert len(logs) == 2 and logs[1]['lecontinue'] == 'cursor'


def test_hook_mutation_cannot_redirect_mock(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    original = SyntheticCollection.client
    def client(self, *args):
        result = original(self, *args)
        result.client.event_hooks['request'].append(lambda r: setattr(r, 'url', httpx.URL('https://evil.invalid')))
        return result
    monkeypatch.setattr(SyntheticCollection, 'client', client)
    with pytest.raises(Incompatible): run(gate, bundle, work)
    assert spent(a) == 0 and calls == []


@pytest.mark.parametrize('scope', ['workspace', 'invocation', 'counter'])
def test_original_collection_limits_remain_binding(tmp_path, config, monkeypatch, scope):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch,
        request_limit=3 if scope == 'workspace' else None, limit=1 if scope == 'invocation' else 10)
    if scope == 'counter':
        with sqlite3.connect(work / 'pipeline.sqlite') as db:
            db.execute("UPDATE collection_control SET value='7' WHERE key='attempts'")
    with pytest.raises(Incompatible):
        run(gate, bundle, work, max_requests=1 if scope == 'invocation' else 10)
    assert spent(a) == len(calls) == {'workspace': 3, 'invocation': 1, 'counter': 0}[scope]


def test_non_mock_transport_is_never_constructed_or_dispatched(tmp_path, config, monkeypatch):
    _, gate, calls, _, _, _ = setup(tmp_path, config, monkeypatch)
    class Other(httpx.BaseTransport):
        def handle_request(self, request): pytest.fail('alternate transport')
    gate._fake = Other()
    with pytest.raises(Incompatible, match='MockTransport'): SyntheticCollection(gate)
    assert calls == []


def test_denied_charge_keeps_conservative_workspace_overcount(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch, allowance=1)
    with pytest.raises(Incompatible): run(gate, bundle, work)
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        local = int(db.execute("SELECT value FROM collection_control WHERE key='attempts'").fetchone()[0])
    assert local == 2 and spent(a) == len(calls) == 1
    with pytest.raises(Incompatible): run(gate, bundle, work)
    assert spent(a) == len(calls) == 1


def test_timeout_classification_survives_counter_sync_failure(tmp_path, config, monkeypatch):
    a, gate, calls, bundle, work, _ = setup(tmp_path, config, monkeypatch)
    original = SyntheticCollection._counters
    def sync(self, *, before):
        if not before: raise OSError('synthetic counter sync failure')
        return original(self, before=before)
    monkeypatch.setattr(SyntheticCollection, '_counters', sync)
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('original synthetic transport failure')
    gate._fake = httpx.MockTransport(handler)
    with pytest.raises(SourceUnavailable, match='original synthetic transport failure') as caught:
        run(gate, bundle, work)
    assert caught.value.code == 'network_timeout' and caught.value.retryable
    assert caught.value.collection_receipt['status'] == 'retryable'
    assert spent(a) == len(calls) == 1


@pytest.mark.parametrize('failure', ['timeout', 'admission', 'interrupt', 'success'])
def test_boundary_preserves_primary_error_and_sync_diagnostic(tmp_path, config, monkeypatch, failure):
    a, gate, calls, _, work, header = setup(tmp_path, config, monkeypatch)
    from vimfc.storage.pipeline_store import PipelineStore
    fixture = SyntheticCollection(gate)
    primary = {'timeout': httpx.ReadTimeout('primary'), 'admission': Incompatible('primary'),
               'interrupt': KeyboardInterrupt('primary')}.get(failure)
    def dispatch(request):
        if primary is not None: raise primary
        return httpx.Response(200)
    secondary = OSError('sync failed')
    monkeypatch.setattr(gate, 'handle_request', dispatch)
    with PipelineStore(work / 'pipeline.sqlite') as store:
        with fixture.client(store, header, 10, lambda r: None, {'contact': 'fixture@example.com'}) as client:
            original = fixture._counters
            def sync(*, before):
                if not before: raise secondary
                return original(before=before)
            monkeypatch.setattr(fixture, '_counters', sync)
            with pytest.raises(type(primary) if primary is not None else OSError) as caught:
                fixture.handle_request(client.client.build_request('GET', 'https://vi.wikipedia.org/w/api.php?maxlag=5'))
            assert caught.value is (primary if primary is not None else secondary)
            if primary is not None:
                assert 'OSError: sync failed' in primary.__notes__[0]
    assert spent(a) == 0 and calls == []


def _crash(root, point):
    socket.socket.connect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('Network forbidden'))
    os.environ['VIMFC_OFFLINE'] = '1'
    header = json.loads((root / 'linux-workspace/workspace.json').read_text())
    a = authority(root, header)
    def handler(request):
        with (root / 'calls').open('a') as stream: stream.write('one\n')
        return httpx.Response(200)
    gate = SyntheticBudgetTransport(a, httpx.MockTransport(handler), budget_id='budget', binding=a._config[1],
        workspace_limit=header['request_limit'], invocation_id='child', invocation_limit=10)
    gate.launch('launch', 3, **OWNER, reservation_id='reservation')
    a.checkpoint = lambda p: os._exit(71) if p == point else None
    run(gate, root / 'portable bundle', root / 'linux-workspace')


@pytest.mark.parametrize('point', ['after_ack', 'before_dispatch', 'after_http_transport', 'before_http_outcome'])
def test_collection_process_exit_restart_preserves_spending(tmp_path, config, monkeypatch, point):
    monkeypatch.setenv('VIMFC_OFFLINE', '1')
    _, bundle, work, header = prepared(tmp_path, config)
    a = authority(tmp_path, header); a.provision()
    a.decide('init', 0, 'initialize', {})
    a.decide('claim', 1, 'claim', dict(OWNER, operator='op', fencing_evidence='synthetic'))
    a.decide('reserve', 2, 'reserve', dict(reservation_id='reservation', attempt='attempt', allowance=10))
    code = "import runpy,sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd())); sys.path.insert(0,str(Path('backend/src').resolve())); runpy.run_path(sys.argv[1])['_crash'](Path(sys.argv[2]),sys.argv[3])"
    env = dict(os.environ, PYTHONPATH=str(Path('backend/src').resolve()))
    child = subprocess.run([sys.executable, '-B', '-c', code, __file__, str(tmp_path), point], env=env,
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == 71, child.stderr
    fresh_a = authority(tmp_path, header)
    assert spent(fresh_a) == 1
    assert (tmp_path / 'calls').exists() == (point in ('after_http_transport', 'before_http_outcome'))
    fresh = SyntheticBudgetTransport(fresh_a, httpx.MockTransport(lambda r: pytest.fail('replayed HTTP')),
        budget_id='budget', binding=fresh_a._config[1], workspace_limit=header['request_limit'],
        invocation_id='restart', invocation_limit=10)
    with pytest.raises(SourceUnavailable, match='lease'): run(fresh, bundle, work)
    assert spent(fresh_a) == 1
    # Expire only the synthetic stage lease; admission still refuses the old capability.
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        db.execute("UPDATE stage_results SET lease_until='2000-01-01T00:00:00Z'")
    with pytest.raises(Incompatible): run(fresh, bundle, work)
    assert spent(fresh_a) == 1
