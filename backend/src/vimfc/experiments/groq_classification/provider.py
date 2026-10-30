"""One-attempt, official Groq REST adapter with secret-safe receipts.

The durable experiment controller owns reservations, pacing and retry decisions.
Model availability and rate-limit headers do not establish account billing tier.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx


_MODELS_URL = 'https://api.groq.com/openai/v1/models'
_CHAT_URL = 'https://api.groq.com/openai/v1/chat/completions'
_KEY = re.compile(r'gsk_[A-Za-z0-9_-]{16,508}\Z')
_KEY_IN_TEXT = re.compile(r'gsk_[A-Za-z0-9_-]+')
_SAFE_HEADERS = frozenset({
    'date', 'retry-after', 'x-request-id', 'x-groq-id',
    'x-ratelimit-limit-requests', 'x-ratelimit-limit-tokens',
    'x-ratelimit-remaining-requests', 'x-ratelimit-remaining-tokens',
    'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens',
})
_DOCS = {
    'checked_on': '2026-10-29',
    'structured_outputs': 'https://console.groq.com/docs/structured-outputs',
    'reasoning': 'https://console.groq.com/docs/reasoning',
    'rate_limits': 'https://console.groq.com/docs/rate-limits',
    'api_reference': 'https://console.groq.com/docs/api-reference',
}
_PROFILES = {
    'qwen/qwen3.8-27b': ('json_schema', {'reasoning_effort': 'none', 'reasoning_format': 'hidden'}),
    # Older Qwen is allowed only if the authenticated models endpoint lists it.
    'qwen/qwen3-32b': ('json_object', {'reasoning_format': 'hidden'}),
    'openai/gpt-oss-20b': ('json_schema', {'reasoning_effort': 'low', 'include_reasoning': False}),
    'openai/gpt-oss-120b': ('json_schema', {'reasoning_effort': 'low', 'include_reasoning': False}),
}


class ProviderError(RuntimeError):
    """A safe error code and bounded receipt; never raw HTTP/network messages."""

    def __init__(self, code: str, *, status: int | None = None,
                 retry_after_seconds: float | None = None,
                 headers: dict | None = None, response_hash: str | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.http_status = status
        self.retry_after_seconds = retry_after_seconds
        self.headers = headers or {}
        self.response_hash = response_hash


def _credential(value: str | None) -> str | None:
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ProviderError('credentials_invalid')
    return value


def load_credentials(env_path: Path = Path('api/.env'),
                     environ: Mapping[str, str] | None = None) -> str | None:
    """Read only GROQ_API_KEY, with environment precedence and no interpolation.

    A small literal dotenv subset handles BOM, CRLF, export, quotes and comments.
    Unrelated assignments are ignored; duplicate or malformed key assignments fail
    closed. No environment mutation, subprocess execution or credential logging.
    """
    source = os.environ if environ is None else environ
    if 'GROQ_API_KEY' in source:
        return _credential(source['GROQ_API_KEY'])
    read_failed = False
    try:
        text = Path(env_path).read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        read_failed = True
    if read_failed:
        raise ProviderError('credentials_file_unreadable')
    value = None
    found = False
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?GROQ_API_KEY\s*=(.*)$', line)
        if not match:
            continue
        if found:
            raise ProviderError('credentials_duplicate')
        found = True
        literal = match.group(1).strip()
        if literal.startswith(('"', "'")):
            quote = literal[0]
            end = literal.find(quote, 1)
            if end < 0 or (literal[end + 1:].strip() and not literal[end + 1:].strip().startswith('#')):
                raise ProviderError('credentials_invalid')
            value = literal[1:end]
        else:
            value = re.split(r'\s+#', literal, maxsplit=1)[0].strip()
    return _credential(value)


def load_free_tier_confirmation(env_path: Path = Path('api/.env'),
                                environ: Mapping[str, str] | None = None) -> dict:
    """User attestation only; never infer billing tier from credentials/models."""
    source = os.environ if environ is None else environ
    name = 'GROQ_FREE_TIER_CONFIRMED'
    if name in source:
        value = source[name]
        basis = 'environment'
    else:
        try:
            lines = Path(env_path).read_text(encoding='utf-8-sig').splitlines()
        except FileNotFoundError:
            lines = []
        values = []
        for line in lines:
            match = re.match(r'^\s*(?:export\s+)?GROQ_FREE_TIER_CONFIRMED\s*=(.*)$', line)
            if match:
                literal = re.split(r'\s+#', match.group(1), maxsplit=1)[0].strip()
                if literal.startswith(('"', "'")):
                    quote = literal[0]
                    if len(literal) < 2 or literal[-1] != quote:
                        raise ProviderError('free_confirmation_invalid')
                    literal = literal[1:-1]
                values.append(literal)
        if len(values) > 1:
            raise ProviderError('free_confirmation_duplicate')
        value = values[0] if values else ''
        basis = 'dotenv' if values else 'missing'
    return {'confirmed': isinstance(value, str) and value.strip().lower() == 'true',
            'setting': name, 'source': basis, 'provider_billing_attestation': False}


def _available_ids(models: list) -> set[str]:
    return {entry if isinstance(entry, str) else entry['id']
            for entry in models
            if isinstance(entry, str) or (isinstance(entry, dict)
                and isinstance(entry.get('id'), str) and entry.get('active', True) is True)}


def select_model(models: list, requested: str | None = None) -> dict:
    """Select a documented profile from an authenticated model-list receipt.

    Prefer strict Qwen, then available legacy Qwen JSON mode. Other models require
    explicit selection so a Qwen failure never silently changes model or mode.
    The client independently verifies availability before each POST.
    """
    available = _available_ids(models)
    model_id = requested
    if model_id is None:
        model_id = next((item for item in ('qwen/qwen3.8-27b', 'qwen/qwen3-32b') if item in available), None)
        if model_id is None:
            raise ProviderError('model_selection_required')
    if model_id not in available:
        raise ProviderError('model_unavailable')
    if model_id not in _PROFILES:
        raise ProviderError('model_unsupported')
    mode, reasoning = _PROFILES[model_id]
    return {'model_id': model_id, 'response_mode': mode, 'temperature': 0,
            'request_kwargs': {'temperature': 0, **reasoning},
            'selection_reason': 'explicit_requested_model' if requested else 'preferred_available_qwen',
            'provenance': dict(_DOCS)}


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse delay-seconds or RFC HTTP-date; invalid values remain unknown."""
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = max(0.0, (instant - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def parse_reset_duration(value: str | None) -> float | None:
    """Parse Groq duration headers such as 2m59.56s, without assuming RPM."""
    if not value or not re.fullmatch(r'(?:\d+(?:\.\d+)?(?:ms|d|h|m|s))+', value):
        return None
    multipliers = {'ms': .001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
    result = sum(float(number) * multipliers[unit]
                 for number, unit in re.findall(r'(\d+(?:\.\d+)?)(ms|d|h|m|s)', value))
    return result if math.isfinite(result) else None


def _redact(value, key: str):
    if isinstance(value, str):
        return _KEY_IN_TEXT.sub('[REDACTED]', value.replace(key, '[REDACTED]'))
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, dict):
        return {_redact(name, key): _redact(item, key) for name, item in value.items()}
    return value


class GroqClient:
    """Fixed-host synchronous adapter; every operation makes at most one request."""

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30):
        validated = _credential(api_key)
        if validated is None:
            raise ProviderError('credentials_missing')
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ProviderError('timeout_invalid')
        self._key = validated
        self._models: list | None = None
        self._client = httpx.Client(transport=transport, timeout=timeout,
                                    follow_redirects=False, trust_env=False,
                                    headers={'Authorization': 'Bearer ' + validated,
                                             'Accept': 'application/json'})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self._client.close()

    def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        if url not in (_MODELS_URL, _CHAT_URL):
            raise ProviderError('host_forbidden')
        started = time.monotonic()
        network_failed = False
        try:
            response = self._client.request(method, url, json=payload)
        except httpx.RequestError:
            network_failed = True
        # Raise outside the except block, avoiding a retained secret-bearing cause.
        if network_failed:
            raise ProviderError('network_error')
        elapsed = time.monotonic() - started
        headers = {name: _redact(value, self._key)[:512]
                   for name, value in response.headers.items() if name in _SAFE_HEADERS}
        fingerprint = hashlib.sha256(response.content).hexdigest()
        status = response.status_code
        error_options = {'status': status, 'headers': headers, 'response_hash': fingerprint,
                         'retry_after_seconds': parse_retry_after(headers.get('retry-after'))}
        if 300 <= status < 400:
            raise ProviderError('redirect_forbidden', **error_options)
        if status < 200 or status >= 300:
            raise ProviderError('rate_limited' if status == 429 else 'http_error', **error_options)
        invalid = False
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            invalid = True
        if invalid or not isinstance(body, dict):
            raise ProviderError('invalid_response', **error_options)
        return {'body': _redact(body, self._key), 'headers': headers,
                'http_status': status, 'elapsed_seconds': elapsed,
                'response_fingerprint': fingerprint, 'account_plan_verified': False}

    def list_models(self) -> dict:
        # A failed refresh invalidates older availability evidence.
        self._models = None
        result = self._request('GET', _MODELS_URL)
        models = result.pop('body').get('data')
        if not isinstance(models, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_./-]{1,256}', item['id'])
                or ('active' in item and not isinstance(item['active'], bool))
                for item in models) or len({item['id'] for item in models}) != len(models):
            raise ProviderError('invalid_response', status=result['http_status'],
                                headers=result['headers'], response_hash=result['response_fingerprint'])
        # Returned receipts are caller-owned; mutating them cannot grant access.
        self._models = [{'id': item['id'], 'active': item.get('active', True)} for item in models]
        return {'models': models, 'completion_access_verified': False, **result}

    def complete(self, payload: dict) -> dict:
        if self._models is None:
            raise ProviderError('models_not_verified')
        if not isinstance(payload, dict) or not isinstance(payload.get('model'), str):
            raise ProviderError('payload_invalid')
        profile = select_model(self._models, payload.get('model'))
        forbidden = {'tools', 'tool_choice', 'functions', 'function_call',
                     'parallel_tool_calls', 'disable_tool_validation', 'search_settings',
                     'web_search_options', 'compound_custom', 'include_domains', 'exclude_domains',
                     'documents', 'enabled_tools'}
        if forbidden.intersection(payload):
            raise ProviderError('tools_forbidden')
        if payload.get('stream', False) is not False:
            raise ProviderError('streaming_forbidden')
        response_format = payload.get('response_format', {})
        if not isinstance(response_format, dict) or response_format.get('type') != profile['response_mode']:
            raise ProviderError('response_mode_unsupported')
        if profile['response_mode'] == 'json_schema':
            schema = response_format.get('json_schema', {})
            if not isinstance(schema, dict) or schema.get('strict') is not True:
                raise ProviderError('response_mode_unsupported')
        result = self._request('POST', _CHAT_URL, payload)
        if result['body'].get('model') != payload['model']:
            raise ProviderError('model_response_mismatch', status=result['http_status'],
                                headers=result['headers'], response_hash=result['response_fingerprint'])
        return result
