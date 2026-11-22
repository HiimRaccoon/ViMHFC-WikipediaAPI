"""Offline physical-attempt accounting at the final HTTPX boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Event

import httpx
import pytest

from vimfc.domain import Incompatible, digest
from vimfc.pipeline import transactional_control as control
from vimfc.pipeline.budget_transport import SyntheticBudgetTransport, final_request


BINDING = dict(workspace_id='workspace', run_id='run', bundle_id='bundle')
OWNER = dict(attempt='attempt', epoch=1, job='job')
URL = 'https://vi.wikipedia.org/w/api.php?maxlag=5&action=query&pageids=1'


class Clock:
    value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def opened(root):
    return control.TransactionalAuthority(root / 'authority.sqlite', control.SQLiteWitness(root / 'witness.sqlite'),
        authority_id='authority', binding=BINDING, runtime='fixture', budget_id='budget', ceiling=10, historical_debit=2)


def prepared(root, allowance=8):
    a = opened(root)
    a.provision()
    a.decide('init', 0, 'initialize', {})
    a.decide('claim', 1, 'claim', dict(OWNER, operator='operator', fencing_evidence='synthetic'))
    a.decide('reserve', 2, 'reserve', dict(reservation_id='reservation', attempt='attempt', allowance=allowance))
    return a


def adapter(a, handler, **overrides):
    return SyntheticBudgetTransport(a, httpx.MockTransport(handler), **(dict(
        budget_id='budget', binding=BINDING, workspace_limit=10, invocation_id='invocation',
        invocation_limit=6, clock=Clock(), sleep=lambda _: None) | overrides))


def launch(gate):
    gate.launch('launch', 3, **OWNER, reservation_id='reservation')


def version(gate):
    return len(gate._model().records)


def send(gate, request_id='request', *, url=URL, expected=None):
    request = httpx.Request('GET', url)
    gate.authorize(request, request_id=request_id, expected_version=version(gate) if expected is None else expected)
    return gate.handle_request(request)


@pytest.fixture
def ready(tmp_path):
    a = prepared(tmp_path)
    calls, clock = [], Clock()

    def handler(request):
        view = a.inspect()
        assert view['eligible'] and sum(view['state']['spent'].values()) >= 1
        calls.append(final_request(request))
        return httpx.Response(200, json={'query': {'pages': []}})

    gate = adapter(a, handler, clock=clock, sleep=clock.sleep)
    launch(gate)
    return a, gate, calls, clock


def budget(gate):
    state = gate._model().inspect()['state']
    charged = state['historical_debit'] + sum(state.get('spent', {}).values())
    assert charged <= state['allocated'] <= state['ceiling']
    return charged


def test_real_httpx_boundary_and_complete_identity(ready):
    a, gate, calls, _ = ready
    with httpx.Client(transport=gate) as client:
        request = client.build_request('GET', URL)
        pin = final_request(request)
        gate.authorize(request, request_id='physical-1', expected_version=4)
        assert calls == [] and budget(gate) == 2
        assert client.send(request).status_code == 200
        record = gate._model().records[4]
        payload = record['payload']
        assert record['decision_id'] == 'physical-1' and payload['units'] == 1
        assert payload['owner'] == a.inspect()['state']['owner']
        assert payload['reservation_id'] == 'reservation' and payload['allowance'] == 8
        assert payload['launch_digest'] == digest(gate._model().records[3])
        assert payload['http_attempt']['policy'] == gate.policy
        assert payload['http_attempt']['request_sha256'] == digest(pin)
        assert calls == [pin] and budget(gate) == 3
        assert gate._model().records[-1]['action'] == 'http_outcome'


@pytest.mark.parametrize('change', ['method', 'scheme', 'host', 'port', 'path', 'query', 'duplicate-maxlag',
                                   'query-order', 'header', 'header-order', 'body'])
def test_final_request_changes_are_denied_without_charge_or_io(ready, change):
    _, gate, calls, _ = ready
    request = httpx.Request('GET', URL, headers=[('x-one', '1'), ('x-two', '2')] if change == 'header-order' else None)
    gate.authorize(request, request_id='request', expected_version=4)
    urls = dict(scheme=URL.replace('https:', 'http:'), host=URL.replace('vi.wikipedia.org', 'www.wikidata.org'),
                port=URL.replace('.org/', '.org:444/'), path=URL.replace('/w/api.php', '/other'),
                query=URL.replace('pageids=1', 'pageids=2'), **{'duplicate-maxlag': URL + '&maxlag=5'},
                **{'query-order': 'https://vi.wikipedia.org/w/api.php?action=query&pageids=1&maxlag=5'})
    changed = httpx.Request('POST' if change == 'method' else 'GET', urls.get(change, URL),
                            headers=(list(reversed(request.headers.raw)) if change == 'header-order'
                                     else {'x-altered': 'yes'} if change == 'header' else None),
                            content=b'changed' if change == 'body' else b'', extensions=request.extensions)
    with pytest.raises(Incompatible):
        gate.handle_request(changed)
    assert calls == [] and budget(gate) == 2


@pytest.mark.parametrize('url', [URL.replace('maxlag=5', 'maxlag=4'), URL.replace('https:', 'http:'),
                                URL.replace('vi.wikipedia.org', 'www.wikidata.org'), URL + '#fragment'])
def test_invalid_authorization_is_zero_io(ready, url):
    _, gate, calls, _ = ready
    with pytest.raises(Incompatible):
        gate.authorize(httpx.Request('GET', url), request_id='bad', expected_version=4)
    assert calls == [] and budget(gate) == 2


def test_http_host_header_cannot_select_another_host(ready):
    _, gate, calls, _ = ready
    with pytest.raises(Incompatible, match='Host header'):
        gate.authorize(httpx.Request('GET', URL, headers={'host': 'other.invalid'}), request_id='bad', expected_version=4)
    assert calls == [] and budget(gate) == 2


def test_direct_boundary_foreign_tokens_close_and_real_transport_denied(ready):
    a, gate, calls, _ = ready
    for token in (None, object(), []):
        with pytest.raises(Incompatible):
            gate.handle_request(httpx.Request('GET', URL, extensions={'synthetic_budget_token': token}))
    with pytest.raises(Incompatible, match='MockTransport'):
        SyntheticBudgetTransport(a, object(), budget_id='budget', binding=BINDING, workspace_limit=10,
                                 invocation_id='invocation', invocation_limit=2)
    request = httpx.Request('GET', URL)
    gate.authorize(request, request_id='x', expected_version=4)
    gate.close()
    with pytest.raises(Incompatible):
        gate.handle_request(request)
    assert calls == [] and budget(gate) == 2


def test_replacing_fake_transport_is_denied_before_charging(ready):
    _, gate, calls, _ = ready
    gate._fake = object()
    with pytest.raises(Incompatible):
        send(gate)
    assert calls == [] and budget(gate) == 2


@pytest.mark.parametrize('policy', [dict(budget_id='wrong'), dict(binding=BINDING | {'workspace_id': 'wrong'}),
                                  dict(binding=BINDING | {'run_id': 'wrong'}), dict(binding=BINDING | {'bundle_id': 'wrong'}),
                                  dict(workspace_limit=-1), dict(invocation_limit=-1)])
def test_policy_must_match_trusted_authority(tmp_path, policy):
    a, calls = prepared(tmp_path), []
    gate = adapter(a, calls.append, **policy)
    with pytest.raises(Incompatible):
        launch(gate)
    assert calls == []


def test_duplicate_tokens_and_lifetime_ids_never_dispatch_again(ready):
    _, gate, calls, _ = ready
    request = httpx.Request('GET', URL)
    gate.authorize(request, request_id='one', expected_version=4)
    gate.handle_request(request)
    with pytest.raises(Incompatible):
        gate.handle_request(request)
    for url in (URL, URL.replace('pageids=1', 'pageids=2')):
        with pytest.raises(Incompatible):
            send(gate, 'one', url=url, expected=4)
    assert len(calls) == 1 and budget(gate) == 3


@pytest.mark.parametrize('scope', ['invocation', 'workspace', 'reservation'])
def test_all_limits_are_durable_and_denials_are_zero_additional_io(tmp_path, scope):
    a = prepared(tmp_path, allowance=2 if scope == 'reservation' else 8)
    calls, clock = [], Clock()
    gate = adapter(a, lambda r: calls.append(r) or httpx.Response(200), clock=clock, sleep=clock.sleep,
                   invocation_limit=2 if scope == 'invocation' else 6,
                   workspace_limit=4 if scope == 'workspace' else 10)
    launch(gate)
    send(gate, 'one'); send(gate, 'two')
    gate.workspace.update(attempts=0, last_request=0)  # restored old scratch cannot grant more allowance
    with pytest.raises(Incompatible, match='limit|allowance'):
        send(gate, 'three')
    assert len(calls) == 2 and budget(gate) == 4


def test_durable_pacing_survives_restored_counters_and_clock_failures(ready):
    _, gate, calls, clock = ready
    send(gate, 'one')
    gate.workspace.update(attempts=0, last_request=0)
    send(gate, 'two')
    assert clock.value == 101 and len(calls) == 2
    clock.value = 50
    with pytest.raises(Incompatible, match='backwards'):
        send(gate, 'three')
    clock.value = float('nan')
    with pytest.raises(Incompatible, match='clock'):
        send(gate, 'four')
    clock.value = 102
    gate.workspace['attempts'] = 9
    with pytest.raises(Incompatible, match='cutover'):
        send(gate, 'five')
    assert len(calls) == 2 and budget(gate) == 4


@pytest.mark.parametrize('damage', ['stop', 'unknown', 'epoch', 'expiry', 'witness', 'authority-rollback', 'witness-rollback'])
def test_stale_or_unverified_authority_denies_io(ready, tmp_path, monkeypatch, damage):
    a, gate, calls, _ = ready
    old_authority, old_witness = a.path.read_bytes(), a.witness.path.read_bytes()
    send(gate, 'first')
    count = len(calls)
    if damage in ('stop', 'unknown'):
        a.decide(damage, version(gate), damage, {} if damage == 'stop' else {'reason': 'uncertain'})
    elif damage == 'epoch':
        a.decide('stop', version(gate), 'stop', {})
        a.decide('release', version(gate), 'release_stopped', {})
        a.decide('next-owner', version(gate), 'claim', dict(OWNER, attempt='next', epoch=2, operator='op', fencing_evidence='synthetic'))
    elif damage == 'expiry':
        monkeypatch.setattr(control, 'monotonic', lambda: float('inf'))
    elif damage == 'witness':
        a.witness.available = False
    elif damage == 'authority-rollback':
        a.path.write_bytes(old_authority)
    else:
        a.witness.path.write_bytes(old_witness)
    with pytest.raises(Incompatible):
        send(gate, 'denied')
    assert len(calls) == count


@pytest.mark.parametrize('point,accepted', [('before_dispatch', False), ('after_dispatch_admission', True)])
def test_stop_admission_race_is_explicit(ready, point, accepted):
    a, gate, calls, _ = ready

    def stop(current):
        if current == point:
            a.checkpoint = lambda _: None
            a.decide('stop', version(gate), 'stop', {})

    a.checkpoint = stop
    if accepted:
        # The handler normally insists on RUNNING; here a previously admitted call is allowed after STOPPED.
        gate._fake = httpx.MockTransport(lambda r: calls.append(final_request(r)) or httpx.Response(200))
        send(gate)
    else:
        with pytest.raises(Incompatible):
            send(gate)
    assert len(calls) == int(accepted) and budget(gate) == 3


def test_blocked_transport_does_not_hold_authority_write_lock(ready):
    a, gate, _, _ = ready
    entered, release = Event(), Event()

    def blocked(_request):
        entered.set()
        assert release.wait(15)
        return httpx.Response(200)

    gate._fake = httpx.MockTransport(blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(send, gate)
        try:
            assert entered.wait(10)
            stop = pool.submit(a.decide, 'stop', version(gate), 'stop', {})
            stop.result(timeout=5)
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 200
    assert a.inspect()['state']['owner']['state'] == 'STOPPED' and budget(gate) == 3


@pytest.mark.parametrize('point', ['after_commit', 'after_ack', 'before_dispatch'])
def test_mutation_between_charge_and_admission_is_zero_transport(ready, point):
    a, gate, calls, _ = ready
    request = httpx.Request('GET', URL)
    gate.authorize(request, request_id='request', expected_version=4)

    def mutate(current):
        if current == point:
            request.url = httpx.URL(URL.replace('pageids=1', 'pageids=2'))

    a.checkpoint = mutate
    with pytest.raises(Incompatible):
        gate.handle_request(request)
    assert calls == [] and budget(gate) == 3


def test_after_admission_mutation_cannot_change_frozen_io(ready):
    a, gate, calls, _ = ready
    request = httpx.Request('GET', URL)
    pin = final_request(request)
    gate.authorize(request, request_id='request', expected_version=4)
    a.checkpoint = lambda p: setattr(request, 'url', httpx.URL('https://evil.invalid')) if p == 'after_dispatch_admission' else None
    gate.handle_request(request)
    assert calls == [pin] and budget(gate) == 3


def test_httpx_hook_mutation_and_missing_authorization_cannot_bypass(ready):
    _, gate, calls, _ = ready
    with httpx.Client(transport=gate) as client:
        with pytest.raises(Incompatible):
            client.get(URL)
        request = client.build_request('GET', URL)
        gate.authorize(request, request_id='mutated-hook', expected_version=4)
        client.event_hooks['request'].append(lambda r: setattr(r, 'url', httpx.URL(URL + '&changed=yes')))
        with pytest.raises(Incompatible):
            client.send(request)
        assert calls == [] and budget(gate) == 2


def test_delayed_transport_uses_durable_completion_for_pacing(ready):
    _, gate, calls, clock = ready

    def delayed(request):
        calls.append(clock.value)
        clock.value += 10
        return httpx.Response(200)

    gate._fake = httpx.MockTransport(delayed)
    send(gate, 'one')
    gate.workspace.update(attempts=0, last_request=0)
    send(gate, 'two')
    assert calls == [100, 111] and budget(gate) == 4


def test_missing_outcome_blocks_new_attempt_even_with_a_new_id(ready):
    a, gate, calls, _ = ready

    def interrupt(point):
        if point == 'before_http_outcome':
            raise OSError('outcome interrupted')

    a.checkpoint = interrupt
    with pytest.raises(OSError):
        send(gate, 'one')
    a.checkpoint = lambda _: None
    with pytest.raises(Incompatible, match='Unresolved HTTP'):
        send(gate, 'two')
    assert len(calls) == 1 and budget(gate) == 3


@pytest.mark.parametrize('response', ['timeout', '429', '503', 'maxlag', 'continuation'])
def test_retry_and_continuation_require_fresh_ids_and_each_cost_one(ready, response):
    _, gate, calls, _ = ready

    def handler(request):
        calls.append(final_request(request))
        if response == 'timeout':
            raise httpx.ReadTimeout('uncertain delivery')
        if response == 'maxlag':
            return httpx.Response(200, json={'error': {'code': 'maxlag'}})
        if response == 'continuation':
            return httpx.Response(200, json={'continue': {'continue': 'next'}})
        return httpx.Response(int(response), headers={'Retry-After': '5'})

    gate._fake = httpx.MockTransport(handler)
    for ident in ('one', 'two'):
        if response == 'timeout':
            with pytest.raises(httpx.ReadTimeout):
                send(gate, ident)
        else:
            send(gate, ident, url=URL + ('&continue=next' if ident == 'two' and response == 'continuation' else ''))
        # Retry scheduling/cooldowns stay with the unchanged source policy, simulated explicitly here.
        gate.clock.value += 5
    assert len(calls) == 2 and budget(gate) == 4
    assert sum(r['action'] == 'http_outcome' for r in gate._model().records) == 2
    with pytest.raises(Incompatible):
        send(gate, 'one', expected=4)
    assert len(calls) == 2


def test_pinned_http_mode_cannot_downgrade_or_refund(ready):
    a, gate, calls, _ = ready
    with pytest.raises(Incompatible):
        gate._worker('unbound', 4, 1)
    send(gate)
    a.decide('stop', version(gate), 'stop', {})
    a.decide('release', version(gate), 'release_stopped', {})
    a.decide('next-owner', version(gate), 'claim', dict(OWNER, attempt='next', epoch=2, operator='op', fencing_evidence='synthetic'))
    with pytest.raises(Incompatible):
        a.decide('new-reservation', version(gate), 'reserve', dict(reservation_id='next', attempt='next', allowance=1))
    assert len(calls) == 1 and a.inspect()['state']['allocated'] == 10


def test_witness_loss_after_charge_ack_denies_io_but_keeps_charge(ready):
    a, gate, calls, _ = ready
    a.checkpoint = lambda p: setattr(a.witness, 'available', False) if p == 'after_ack' else None
    with pytest.raises(Incompatible):
        send(gate)
    a.witness.available = True
    assert calls == [] and budget(gate) == 3
    assert gate._model().status() == 'ACKNOWLEDGED'


@pytest.mark.parametrize('damage', ['units', 'digest', 'schema', 'policy'])
def test_unchecked_dispatch_metadata_is_not_a_valid_http_charge(ready, damage):
    _, gate, calls, _ = ready
    pin = final_request(httpx.Request('GET', URL))
    metadata = dict(policy=deepcopy(gate.policy), request=pin, request_sha256=digest(pin), at=100)
    if damage == 'digest':
        metadata['request_sha256'] = '0' * 64
    elif damage == 'schema':
        metadata['request'].pop('path')
        metadata['request_sha256'] = digest(metadata['request'])
    elif damage == 'policy':
        metadata['policy']['invocation_limit'] += 1
    with pytest.raises(Incompatible):
        gate._worker('forged', 4, 2 if damage == 'units' else 1, http_attempt=metadata)
    assert calls == [] and budget(gate) == 2


@pytest.mark.parametrize('change', ['invocation-reuse', 'workspace-policy', 'activation-bypass', 'valid'])
def test_next_epoch_keeps_workspace_and_lifetime_policy(tmp_path, change):
    a, clock, calls = prepared(tmp_path, allowance=4), Clock(), []
    handler = lambda r: calls.append(r) or httpx.Response(200)
    gate = adapter(a, handler, clock=clock, sleep=clock.sleep, workspace_limit=4)
    launch(gate)
    send(gate, 'first')
    a.decide('stop', version(gate), 'stop', {})
    a.decide('release', version(gate), 'release_stopped', {})
    a.decide('claim-next', version(gate), 'claim', dict(OWNER, attempt='next', epoch=2, operator='op', fencing_evidence='fixture'))
    a.decide('reserve-next', version(gate), 'reserve', dict(reservation_id='next', attempt='next', allowance=4))
    other = adapter(a, handler, clock=clock, sleep=clock.sleep, workspace_limit=10 if change == 'workspace-policy' else 4,
                    invocation_id='invocation' if change == 'invocation-reuse' else 'next-invocation')
    params = dict(attempt='next', epoch=2, job='job', reservation_id='next')
    if change == 'activation-bypass':
        policy = dict(other.policy, workspace_limit=10)
        record, _ = a._decide('unchecked-activation', version(gate), 'activate',
                             dict(reservation_id='next', activation={'fixture': 'unchecked'}, http_policy=policy))
        pin = final_request(httpx.Request('GET', URL))
        payload = dict(owner=record['state']['owner'], launch_digest=digest(record), reservation_id='next', allowance=4,
                       units=1, http_attempt=dict(policy=policy, request=pin, request_sha256=digest(pin), at=200))
        with pytest.raises(Incompatible, match='Pinned workspace'):
            a._decide('unchecked-dispatch', version(gate), 'dispatch', payload)
        assert len(calls) == 1
    elif change != 'valid':
        with pytest.raises(Incompatible):
            other.launch('launch-next', version(gate), **params)
        assert len(calls) == 1
    else:
        other.launch('launch-next', version(gate), **params)
        send(other, 'second')
        other.workspace.update(attempts=0, last_request=0)
        with pytest.raises(Incompatible):
            send(other, 'third')
        assert len(calls) == 2 and budget(other) == 4
    assert a.inspect()['state']['allocated'] == 10


def _child(root, point):
    socket.socket.connect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('Network forbidden'))
    a = opened(root)

    def handler(_request):
        with (root / 'calls').open('a') as stream:
            stream.write('one\n')
        if point == 'during_http_transport':
            os._exit(71)
        return httpx.Response(200)

    clock = Clock()
    gate = adapter(a, handler, clock=clock, sleep=clock.sleep)
    launch(gate)
    outcome = False

    def crash(current):
        nonlocal outcome
        if current == 'before_http_outcome':
            outcome = True
        effective = 'outcome_' + current if outcome and current in ('before_stage', 'before_commit', 'after_commit', 'before_ack', 'after_ack') else current
        if effective == point:
            os._exit(71)

    a.checkpoint = crash
    send(gate)
    raise AssertionError('Crash point missed')


@pytest.mark.parametrize('point', ['before_stage', 'before_commit', 'after_commit', 'before_ack', 'after_ack',
    'before_dispatch', 'after_dispatch_admission', 'during_http_transport', 'after_http_transport',
    'before_http_outcome', 'outcome_before_stage', 'outcome_before_commit', 'outcome_after_commit',
    'outcome_before_ack', 'outcome_after_ack', 'after_http_outcome'])
def test_process_crash_restart_never_replays_or_refunds(tmp_path, point):
    prepared(tmp_path)
    repo = Path(__file__).resolve().parents[2]
    code = ('import runpy,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            "runpy.run_path(sys.argv[2])['_child'](Path(sys.argv[3]),sys.argv[4])")
    result = subprocess.run([sys.executable, '-B', '-c', code, str(repo / 'backend/src'), __file__, str(tmp_path), point],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 71, result.stderr
    count = len((tmp_path / 'calls').read_text().splitlines()) if (tmp_path / 'calls').exists() else 0
    assert count == int(point in ('during_http_transport', 'after_http_transport', 'before_http_outcome',
                                  'outcome_before_stage', 'outcome_before_commit', 'outcome_after_commit',
                                  'outcome_before_ack', 'outcome_after_ack', 'after_http_outcome'))
    a, calls = opened(tmp_path), []
    gate = adapter(a, lambda r: calls.append(r) or httpx.Response(200), workspace=dict(attempts=0, last_request=0))
    if point in ('before_commit', 'outcome_before_commit'):
        with pytest.raises(Incompatible):
            a.inspect()  # witness staged a record whose authority transaction did not commit
        with closing(sqlite3.connect(a.path)) as db:
            state = json.loads(db.execute('SELECT body FROM records ORDER BY version DESC LIMIT 1').fetchone()[0])['state']
        assert sum(state.get('spent', {}).values()) == int(point == 'outcome_before_commit')
        assert state['allocated'] == 10
    else:
        state = a.inspect()['state']
        assert state['allocated'] == 10 and state['reservations']['reservation']['allowance'] == 8
        assert sum(state.get('spent', {}).values()) == int(point != 'before_stage')
        a.recover()  # explicit acknowledgment recovery never issues I/O or revives capabilities
    with pytest.raises(Incompatible):
        launch(gate)
    with pytest.raises(Incompatible):
        gate.handle_request(httpx.Request('GET', URL))
    assert calls == []
