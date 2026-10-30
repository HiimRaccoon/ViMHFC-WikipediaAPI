"""Offline contract checks for the real HTTP adapter; no credentials are loaded."""
import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest


KEY = 'gsk_' + 'syntheticfixturekey1234567890'
MODEL = 'qwen/qwen3.8-27b'


def provider():
    from vimfc.experiments.groq_classification import provider as module
    return module


def payload(model=MODEL, mode='json_schema'):
    result = {'model': model, 'messages': [{'role': 'user', 'content': 'Return JSON.'}],
              'temperature': 0, 'max_completion_tokens': 100, 'stream': False,
              'response_format': {'type': mode}}
    if mode == 'json_schema':
        result['response_format']['json_schema'] = {
            'name': 'answer', 'strict': True,
            'schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}},
                       'required': ['answer'], 'additionalProperties': False}}
    return result


def transport(handler, models=None):
    def route(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'object': 'list', 'data': models or [
                {'id': MODEL, 'object': 'model', 'active': True, 'owned_by': 'Qwen'}]})
        return handler(request)
    return httpx.MockTransport(route)


def test_windows_dotenv_is_parsed_without_environment_mutation(tmp_path):
    module = provider()
    dotenv = tmp_path / '.env'
    dotenv.write_bytes(('\ufeff# local\r\nOTHER=value\r\nexport GROQ_API_KEY = "' + KEY + '" # secret\r\n').encode())
    environ = {}
    assert module.load_credentials(dotenv, environ) == KEY
    assert environ == {}
    assert module.load_credentials(dotenv, {'GROQ_API_KEY': KEY + 'override'}) == KEY + 'override'


def test_missing_credentials_do_not_make_requests(tmp_path):
    assert provider().load_credentials(tmp_path / 'absent', {}) is None
    with pytest.raises(provider().ProviderError) as error:
        provider().GroqClient(None)
    assert error.value.code == 'credentials_missing'


@pytest.mark.parametrize('value', ['invalid-sensitive-token', 'gsk_abc\nInjected: secret', '${ENV_SECRET}', 'gsk_'])
def test_invalid_credentials_are_never_reflected(value, tmp_path):
    module = provider()
    with pytest.raises(module.ProviderError) as error:
        module.load_credentials(tmp_path / 'absent', {'GROQ_API_KEY': value})
    assert value not in str(error.value)
    assert value not in repr(error.value)
    assert error.value.code == 'credentials_invalid'


def test_model_selection_requires_availability_and_explicit_fallback():
    module = provider()
    chosen = module.select_model([{'id': 'qwen/qwen3-32b'}, {'id': MODEL}])
    assert chosen['model_id'] == MODEL
    assert chosen['response_mode'] == 'json_schema'
    assert chosen['request_kwargs'] == {'temperature': 0, 'reasoning_effort': 'none', 'reasoning_format': 'hidden'}
    assert chosen['provenance']['checked_on'] == '2026-10-29'
    assert module.select_model([{'id': 'qwen/qwen3-32b'}])['response_mode'] == 'json_object'
    with pytest.raises(module.ProviderError, match='model_selection_required'):
        module.select_model([{'id': 'openai/gpt-oss-20b'}])
    fallback = module.select_model([{'id': 'openai/gpt-oss-20b'}], 'openai/gpt-oss-20b')
    assert fallback['request_kwargs'] == {'temperature': 0, 'reasoning_effort': 'low', 'include_reasoning': False}
    assert fallback['selection_reason'] == 'explicit_requested_model'
    with pytest.raises(module.ProviderError, match='model_unavailable'):
        module.select_model([], MODEL)
    with pytest.raises(module.ProviderError, match='model_unsupported'):
        module.select_model([{'id': 'other/unknown'}], 'other/unknown')


def test_completion_requires_actual_get_models_before_dispatch():
    module = provider()
    requests = []
    with module.GroqClient(KEY, transport=transport(lambda request: requests.append(request))) as client:
        with pytest.raises(module.ProviderError, match='models_not_verified'):
            client.complete(payload())
    assert requests == []


def test_official_endpoints_strict_dispatch_and_safe_receipts():
    module = provider()
    sent = []
    body = {'id': 'chat_fixture', 'object': 'chat.completion', 'model': MODEL,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"answer":"ok"}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
    def serve(request):
        sent.append(request)
        return httpx.Response(200, json=body, headers={
            'x-ratelimit-limit-requests': '1000', 'x-ratelimit-limit-tokens': '6000',
            'x-request-id': 'fixture-request', 'authorization': KEY, 'set-cookie': KEY})
    with module.GroqClient(KEY, transport=transport(serve)) as client:
        models = client.list_models()
        assert models['models'][0]['id'] == MODEL
        assert models['account_plan_verified'] is False
        request_payload = payload()
        request_payload.update(module.select_model(models['models'])['request_kwargs'])
        result = client.complete(request_payload)
    assert len(sent) == 1
    assert str(sent[0].url) == 'https://api.groq.com/openai/v1/chat/completions'
    assert sent[0].headers['authorization'] == 'Bearer ' + KEY
    assert json.loads(sent[0].content)['response_format']['json_schema']['strict'] is True
    assert json.loads(sent[0].content)['reasoning_effort'] == 'none'
    assert result['body'] == body
    assert result['http_status'] == 200
    assert result['elapsed_seconds'] >= 0
    assert len(result['response_fingerprint']) == 64
    assert result['headers'] == {'x-ratelimit-limit-requests': '1000', 'x-ratelimit-limit-tokens': '6000', 'x-request-id': 'fixture-request'}
    assert KEY not in repr(result)


@pytest.mark.parametrize('mutation,code', [
    ({'model': 'openai/gpt-oss-20b'}, 'model_unavailable'),
    ({'response_format': {'type': 'json_object'}}, 'response_mode_unsupported'),
    ({'tools': []}, 'tools_forbidden'),
    ({'stream': True}, 'streaming_forbidden'),
    ({'search_settings': {}}, 'tools_forbidden'),
])
def test_unsafe_or_unsupported_payload_never_reaches_chat(mutation, code):
    module = provider()
    seen = []
    with module.GroqClient(KEY, transport=transport(lambda request: seen.append(request))) as client:
        client.list_models()
        request = payload()
        request.update(mutation)
        with pytest.raises(module.ProviderError) as error:
            client.complete(request)
    assert error.value.code == code
    assert seen == []


def test_qwen_legacy_strict_mode_never_downgrades():
    module = provider()
    seen = []
    with module.GroqClient(KEY, transport=transport(lambda request: seen.append(request), [{'id': 'qwen/qwen3-32b'}])) as client:
        client.list_models()
        with pytest.raises(module.ProviderError, match='response_mode_unsupported'):
            client.complete(payload('qwen/qwen3-32b'))
    assert seen == []


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500, 503])
def test_http_errors_are_single_attempt_and_do_not_expose_response_or_key(status):
    module = provider()
    seen = []
    raw = json.dumps({'error': {'message': 'prompt and ' + KEY, 'code': KEY}}).encode()
    def serve(request):
        seen.append(request)
        return httpx.Response(status, content=raw, headers={
            'retry-after': '12.5', 'x-request-id': KEY, 'set-cookie': KEY})
    with module.GroqClient(KEY, transport=transport(serve)) as client:
        client.list_models()
        with pytest.raises(module.ProviderError) as error:
            client.complete(payload())
    assert len(seen) == 1
    assert error.value.status == status
    assert error.value.retry_after_seconds == 12.5
    assert error.value.response_hash == hashlib.sha256(raw).hexdigest()
    assert KEY not in repr(vars(error.value))
    assert KEY not in str(error.value)
    assert 'prompt and' not in str(error.value)


def test_network_failure_is_single_attempt_and_redacted():
    module = provider()
    seen = []
    def serve(request):
        seen.append(request)
        raise httpx.ReadTimeout('secret endpoint ' + KEY, request=request)
    with module.GroqClient(KEY, transport=transport(serve)) as client:
        client.list_models()
        with pytest.raises(module.ProviderError) as error:
            client.complete(payload())
    assert len(seen) == 1
    assert error.value.code == 'network_error'
    assert error.value.status is None
    assert KEY not in repr(vars(error.value))
    assert KEY not in str(error.value)
    assert error.value.__cause__ is None


def test_redirect_is_rejected_without_contacting_another_host():
    module = provider()
    requests = []
    def serve(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={'location': 'https://evil.invalid/collect'})
    with module.GroqClient(KEY, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(module.ProviderError, match='redirect_forbidden'):
            client.list_models()
    assert requests == ['https://api.groq.com/openai/v1/models']


def test_retry_after_dates_and_reset_units():
    module = provider()
    now = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)
    assert module.parse_retry_after('Thu, 29 Oct 2026 00:00:17 GMT', now) == 17
    assert module.parse_retry_after('Wed, 28 Oct 2026 23:59:59 GMT', now) == 0
    assert module.parse_retry_after('13.25', now) == 13.25
    assert module.parse_retry_after('invalid', now) is None
    assert module.parse_retry_after('-1', now) is None
    assert module.parse_reset_duration('2m59.56s') == pytest.approx(179.56)
    assert module.parse_reset_duration('1h2m3s') == 3723
    assert module.parse_reset_duration('1d') == 86400
    assert module.parse_reset_duration('oops') is None


@pytest.mark.parametrize('response', [httpx.Response(200, text='not JSON'), httpx.Response(200, json=[]), httpx.Response(200, json={'data': 'wrong'})])
def test_invalid_models_response_fails_closed(response):
    module = provider()
    with module.GroqClient(KEY, transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(module.ProviderError, match='invalid_response'):
            client.list_models()


@pytest.mark.parametrize('models', [[{'id': MODEL}, {'id': MODEL}], [{'id': ''}], [{'id': MODEL, 'active': 'true'}]])
def test_malformed_or_duplicate_models_cannot_verify_availability(models):
    module = provider()
    with module.GroqClient(KEY, transport=transport(lambda request: None, models)) as client:
        with pytest.raises(module.ProviderError, match='invalid_response'):
            client.list_models()


def test_mutating_model_receipt_cannot_change_authenticated_availability():
    module = provider()
    seen = []
    with module.GroqClient(KEY, transport=transport(lambda request: seen.append(request))) as client:
        result = client.list_models()
        result['models'].append({'id': 'openai/gpt-oss-20b'})
        with pytest.raises(module.ProviderError, match='model_unavailable'):
            client.complete(payload('openai/gpt-oss-20b'))
    assert seen == []


def test_models_receipt_does_not_assert_completion_permission():
    module = provider()
    with module.GroqClient(KEY, transport=transport(lambda request: None)) as client:
        assert client.list_models()['completion_access_verified'] is False


def test_missing_model_cannot_implicitly_select_at_dispatch():
    module = provider()
    seen = []
    with module.GroqClient(KEY, transport=transport(lambda request: seen.append(request))) as client:
        client.list_models()
        request = payload()
        request.pop('model')
        with pytest.raises(module.ProviderError, match='payload_invalid'):
            client.complete(request)
    assert seen == []


def test_mismatched_response_model_cannot_silently_change_selection():
    module = provider()
    with module.GroqClient(KEY, transport=transport(lambda request: httpx.Response(
            200, json={'model': 'other/model', 'choices': []}))) as client:
        client.list_models()
        with pytest.raises(module.ProviderError, match='model_response_mismatch'):
            client.complete(payload())


def test_echoed_key_is_removed_from_success_body_and_headers():
    module = provider()
    with module.GroqClient(KEY, transport=transport(lambda request: httpx.Response(200,
            json={'model': MODEL, 'message': KEY, KEY: ['prefix ' + KEY]},
            headers={'x-request-id': 'prefix ' + KEY}))) as client:
        client.list_models()
        result = client.complete(payload())
    assert KEY not in repr(result)
    assert result['body']['message'] == '[REDACTED]'
