"""Offline HTTPX admission model. Only an explicitly supplied MockTransport may run."""
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from threading import Lock
import time

import httpx

from vimfc.domain import digest
from .transactional_control import _transaction
from .trusted_control import fields, name, require


def finite(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def final_request(request):
    """Pin encoded URL too: duplicate query ordering and percent aliases cannot change."""
    url = request.url
    require(request.method == 'GET' and url.scheme == 'https' and url.host == 'vi.wikipedia.org'
            and url.port in (None, 443) and url.raw_path.split(b'?')[0] == b'/w/api.php'
            and not url.userinfo and not url.fragment and request.content == b'', 'HTTP request policy differs')
    require(url.params.get_list('maxlag') == ['5'], 'Exactly one maxlag=5 required')
    require(request.headers.get_list('host') in (['vi.wikipedia.org'], ['vi.wikipedia.org:443']),
            'HTTP Host header differs')
    return dict(method=request.method, scheme=url.scheme, host=url.host, port=url.port or 443,
                path=url.path, query=sorted(list(url.params.multi_items())),
                raw_url=str(url), headers=[(k.hex(), v.hex()) for k, v in request.headers.raw],
                body_sha256=sha256(request.content).hexdigest())


def apply_launch(model, state, payload):
    fields(payload, ('reservation_id', 'http_policy'))
    policy = payload['http_policy']
    fields(policy, ('budget_id', 'binding', 'workspace_limit', 'invocation_id', 'invocation_limit'))
    name(policy['invocation_id'])
    require(policy['budget_id'] == state['budget_id'] and policy['binding'] == state['binding'], 'HTTP identity differs')
    require(all(type(policy[k]) is int and policy[k] >= 0 for k in ('workspace_limit', 'invocation_limit')),
            'Invalid HTTP limits')
    pinned = {k: policy[k] for k in ('budget_id', 'binding', 'workspace_limit')}
    require(state.get('http_policy', pinned) == pinned, 'Pinned workspace policy differs')
    require(not any(r['action'] == 'launch' and r['payload'].get('http_policy', {}).get('invocation_id')
                    == policy['invocation_id'] for r in model.records), 'Invocation already used')
    state['http_policy'] = deepcopy(pinned)


def apply_attempt(model, state, payload):
    fields(payload, ('owner', 'launch_digest', 'reservation_id', 'allowance', 'units', 'http_attempt'))
    require('http_policy' in state and payload['units'] == 1, 'One HTTP attempt unit required')
    attempt = payload['http_attempt']
    fields(attempt, ('policy', 'request', 'request_sha256', 'at'))
    launch = next((r for r in reversed(model.records) if r['action'] in ('launch', 'activate')), None)
    require(launch is not None and digest(launch) == payload['launch_digest']
            and launch['payload'].get('http_policy') == attempt['policy'], 'HTTP launch binding differs')
    policy = attempt['policy']
    require({k: policy[k] for k in ('budget_id', 'binding', 'workspace_limit')} == state['http_policy'],
            'Pinned workspace policy differs')
    require(digest(attempt['request']) == attempt['request_sha256'] and finite(attempt['at']), 'Invalid request pin')
    pin = attempt['request']
    fields(pin, ('method', 'scheme', 'host', 'port', 'path', 'query', 'raw_url', 'headers', 'body_sha256'))
    try:
        reconstructed = httpx.Request(pin['method'], pin['raw_url'],
                                      headers=[(bytes.fromhex(k), bytes.fromhex(v)) for k, v in pin['headers']])
        require(digest(final_request(reconstructed)) == digest(pin), 'Noncanonical request pin')
    except (ValueError, TypeError, httpx.InvalidURL) as error:
        raise ValueError('Malformed request pin') from error
    charges = [r for r in model.records if r['action'] == 'dispatch' and 'http_attempt' in r['payload']]
    outcomes = [r for r in model.records if r['action'] == 'http_outcome']
    resolved = {r['payload']['request_id'] for r in outcomes}
    require(all(r['decision_id'] in resolved for r in charges), 'Unresolved HTTP attempt requires operator disposition')
    used = sum(r['payload']['http_attempt']['policy']['invocation_id'] == policy['invocation_id'] for r in charges)
    require(used < policy['invocation_limit'], 'Invocation request limit exhausted')
    require(state['historical_debit'] + sum(state.get('spent', {}).values()) < policy['workspace_limit'],
            'Workspace request limit exhausted')
    require(not charges or attempt['at'] >= charges[-1]['payload']['http_attempt']['at'] + 1,
            'Durable request pacing differs')
    require(not outcomes or attempt['at'] >= outcomes[-1]['payload']['result']['at'] + 1,
            'Durable completion pacing differs')


def apply_outcome(model, payload):
    fields(payload, ('request_id', 'grant_digest', 'result'))
    grant = next((r for r in model.records if r['decision_id'] == payload['request_id']), None)
    require(grant is not None and grant['action'] == 'dispatch' and 'http_attempt' in grant['payload']
            and digest(grant) == payload['grant_digest'], 'Outcome grant differs')
    require(not any(r['action'] == 'http_outcome' and r['payload']['request_id'] == payload['request_id']
                    for r in model.records), 'Outcome already recorded')
    result = payload['result']
    fields(result, ('kind', 'status', 'body_sha256', 'at'))
    require(result['kind'] in ('response', 'timeout', 'error'), 'Invalid HTTP outcome')
    require(finite(result['at']) and result['at'] >= grant['payload']['http_attempt']['at'], 'Invalid outcome clock')
    if result['kind'] == 'response':
        require(type(result['status']) is int and 100 <= result['status'] <= 599
                and type(result['body_sha256']) is str and len(result['body_sha256']) == 64, 'Invalid response outcome')
    else:
        require(result['status'] is None and result['body_sha256'] is None, 'Invalid exception outcome')


class SyntheticBudgetTransport(httpx.BaseTransport):
    """Fresh launch and process-local tokens only; never adopt a restored worker capability."""

    def __init__(self, authority, fake, *, budget_id, binding, workspace_limit,
                 invocation_id, invocation_limit, workspace=None, clock=time.time, sleep=time.sleep):
        require(type(fake) is httpx.MockTransport, 'Explicit MockTransport required')
        self.authority, self._fake = authority, fake
        self.policy = deepcopy(dict(budget_id=budget_id, binding=binding, workspace_limit=workspace_limit,
                                    invocation_id=invocation_id, invocation_limit=invocation_limit))
        self.workspace = workspace if workspace is not None else dict(attempts=0, last_request=0)
        self.clock, self.sleep = clock, sleep
        self._tokens, self._lock, self._worker, self._closed = {}, Lock(), None, False

    def launch(self, decision_id, expected_version, *, attempt, epoch, job, reservation_id, ttl=60):
        require(not self._closed and self._worker is None, 'Transport already launched or closed')
        self._worker = self.authority._launch(
            decision_id, expected_version, attempt=attempt, epoch=epoch, job=job,
            reservation_id=reservation_id, ttl=ttl, mock_transport=self._dispatch, http_policy=self.policy)

    def authorize(self, request, *, request_id, expected_version):
        name(request_id)
        require(not self._closed and self._worker is not None and type(expected_version) is int, 'Fresh transport launch required')
        pin = final_request(request)
        token = object()
        self._tokens[token] = (request_id, expected_version, pin)
        request.extensions['synthetic_budget_token'] = token
        return request

    def _model(self):
        with _transaction(self.authority.path) as db:
            return self.authority._load(db)

    def handle_request(self, request):
        with self._lock:
            token = request.extensions.pop('synthetic_budget_token', None)
            require(not self._closed and type(token) is object and token in self._tokens,
                    'Missing or reused process-local admission token')
            request_id, version, pin = self._tokens.pop(token)
            require(final_request(request) == pin, 'Final request changed')
            model = self._model()
            state = model.inspect()['state']
            local = self.workspace
            require(type(local.get('attempts')) is int and local['attempts'] >= 0
                    and finite(local.get('last_request')), 'Invalid workspace counters')
            durable = state['historical_debit'] + sum(state.get('spent', {}).values())
            require(local['attempts'] <= durable, 'Workspace counter exceeds durable accounting; review cutover')
            require(max(local['attempts'], durable) < self.policy['workspace_limit'], 'Workspace request limit exhausted')
            charges = [r for r in model.records if r['action'] == 'dispatch' and 'http_attempt' in r['payload']]
            outcomes = [r for r in model.records if r['action'] == 'http_outcome']
            last = max(local['last_request'], charges[-1]['payload']['http_attempt']['at'] if charges else 0,
                       outcomes[-1]['payload']['result']['at'] if outcomes else 0)
            stamp = self.clock()
            require(finite(stamp), 'Invalid admission clock')
            delay = max(0, last + 1 - stamp)
            require(delay <= 5, 'Clock moved backwards; inspect before resume')
            if delay:
                self.sleep(delay)
            stamp = self.clock()
            require(finite(stamp) and stamp >= last + 1, 'Pacing deadline not reached')
            metadata = dict(policy=deepcopy(self.policy), request=pin, request_sha256=digest(pin), at=stamp)

            def check(_model):
                require(not self._closed and type(self._fake) is httpx.MockTransport
                        and final_request(request) == pin and self.clock() >= stamp,
                        'Request or clock changed before admission')

            # Freeze actual bytes before admission; mutations after admission cannot redirect in-flight I/O.
            self._pending = (request_id, version, httpx.Request(
                request.method, request.url, headers=request.headers.raw, content=request.content))
            try:
                return self._worker(request_id, version, 1, http_attempt=metadata, check=check)
            finally:
                self._pending = None

    def _dispatch(self, units):
        require(units == 1 and self._pending is not None and type(self._fake) is httpx.MockTransport,
                'Missing HTTP admission or fake transport')
        request_id, version, frozen = self._pending
        grant = self._model().records[version]
        self.workspace.update(attempts=max(self.workspace['attempts'],
            grant['state']['historical_debit'] + sum(grant['state']['spent'].values())),
            last_request=grant['payload']['http_attempt']['at'])
        # This callback runs outside the authority write transaction, as in the existing mock gate.
        try:
            response = self._fake.handle_request(frozen)
            response.read()
        except Exception as error:
            result = dict(kind='timeout' if isinstance(error, httpx.TimeoutException) else 'error',
                          status=None, body_sha256=None)
            self._outcome(request_id, grant, result)
            raise
        self.authority.checkpoint('after_http_transport')
        self._outcome(request_id, grant, dict(kind='response', status=response.status_code,
                                            body_sha256=sha256(response.content).hexdigest()))
        return response

    def _outcome(self, request_id, grant, result):
        self.authority.checkpoint('before_http_outcome')
        result['at'] = self.clock()
        model = self._model()
        self.authority._decide('http-outcome:' + request_id, len(model.records), 'http_outcome',
                               dict(request_id=request_id, grant_digest=digest(grant), result=result))
        self.authority.checkpoint('after_http_outcome')

    def close(self):
        self._closed = True
        self._tokens.clear()
        self._fake.close()
