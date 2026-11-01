"""Finite, checkpointed integration inference; original ledgers remain read-only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid

from vimfc.domain import digest
from .classifier import GroqAssistedClassifier, validate_free_confirmation
from .human_review import ROOT, _bytes, _new, _read, _safe_output, _sha
from .integration_policy import (MODEL, VERSION, build_integration_prompt, resolve_classification,
                                 response_format, validate_integration_response)
from .integration_shadow import replay_shadow
from .integration_report import write_shadow_report
from .provider import GroqClient, ProviderError, load_credentials, load_free_tier_confirmation, select_model
from .store import ExperimentStore, Pause


def integration_key(row, profile, prompt, settings):
    return digest({'format': VERSION, 'page_id': row['evaluation']['page_id'],
                   'input_fingerprint': row['input_fingerprint'], 'model_id': profile['model_id'],
                   'prompt_hash': prompt['prompt_hash'], 'schema_hash': prompt['schema_hash'],
                   'parameters': {**profile['request_kwargs'], 'max_completion_tokens': settings['max_output_tokens']}})


def clone_rate_ledger(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists(): raise FileExistsError('Do not reset or overwrite an integration ledger')
    if source == target: raise ValueError('Original ledger cannot be the write target')
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as old:
        if old.execute("SELECT 1 FROM state WHERE key='lease'").fetchone():
            raise Pause('original_worker_lease_active')
        with sqlite3.connect(target) as new:
            old.backup(new)


def integration_metadata(client, store, invocation, settings, classifier, dispatch_guard):
    if store.pending(): raise Pause('uncertain_request_requires_review')
    attempt = classifier._reserve('models:' + invocation, 0, 'models')
    try: dispatch_guard()
    except Exception:
        store.finish(attempt, {'code': 'dispatch_guard_refused'}, tokens=0, error=True)
        raise
    try:
        result = client.list_models()
    except ProviderError as error:
        store.finish(attempt, {'code': error.code, 'http_status': error.status, 'headers': error.headers,
                               'response_hash': error.response_hash}, tokens=0, error=True, uncertain=error.status is None)
        # Restrictions from failed metadata calls are as binding as chat headers.
        store.headers(error.headers)
        if error.retry_after_seconds: store.cooldown(error.retry_after_seconds, error.code)
        raise Pause(error.code, time.time() + error.retry_after_seconds if error.retry_after_seconds else None) from None
    store.finish(attempt, result, tokens=0)
    store.headers(result['headers'])
    return result


class IntegratedClassifier(GroqAssistedClassifier):
    """Reuse native reservation/rate gates, with a separately versioned schema."""
    def classify(self, row, *, dispatch_guard=None):
        if row['evaluation']['baseline_outcome'] == 'completed' or not row['trusted_lead']:
            return resolve_classification(row)
        self._check_runtime()
        prompt = build_integration_prompt(row, self.rules)
        key = integration_key(row, self.profile, prompt, self.settings)
        cached = self.store.cached(key)
        if cached is not None:
            checked = validate_integration_response(cached['response_content'], row)
            if cached['finish_reason'] != 'stop':
                checked = {'status': 'invalid_integration_response', 'prediction': None}
            if (cached['policy_version'] != VERSION or cached['input_fingerprint'] != row['input_fingerprint']
                    or cached['page_id'] != row['evaluation']['page_id']
                    or cached['status'] != checked['status'] or cached['prediction'] != checked['prediction']):
                raise ValueError('Integration cached response validation differs')
            return self._resolved(row, cached)
        previous = self.store.successful_response(key)
        if previous:
            record = self._checkpoint(row, key, prompt, previous['response'])
            return self._resolved(row, record)
        validate_free_confirmation(self.settings.get('free_tier_confirmed'))
        if self.store.pending(): raise Pause('uncertain_request_requires_review')
        payload = {'model': MODEL, 'messages': prompt['messages'], **self.profile['request_kwargs'],
                   'max_completion_tokens': self.settings['max_output_tokens'], 'response_format': response_format()}
        from .token_estimation import estimate_request
        estimate = (self.estimator or estimate_request)(payload)
        # This guard is evaluated after any pacing waits too, immediately before
        # the reservation/dispatch, so no new call follows source divergence.
        if dispatch_guard: dispatch_guard()
        attempt = self._reserve(key, estimate['reserved_tokens'], 'chat')
        if dispatch_guard:
            try: dispatch_guard()
            except Exception:
                self.store.finish(attempt, {'code': 'dispatch_guard_refused'}, tokens=0, error=True)
                raise
        try:
            response = self.client.complete(payload)
        except ProviderError as error:
            unknown = error.status is None or error.code in ('invalid_response', 'model_response_mismatch')
            self.store.finish(attempt, {'code': error.code, 'http_status': error.status, 'headers': error.headers,
                                      'response_hash': error.response_hash}, error=True, uncertain=unknown)
            self.store.headers(error.headers)
            if error.retry_after_seconds: self.store.cooldown(error.retry_after_seconds, error.code)
            raise Pause('uncertain_request_requires_review' if unknown else error.code) from None
        usage = response['body'].get('usage')
        valid_usage = (isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                       for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                       and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])
        response = {**response, 'token_estimate': estimate,
                    'free_tier_confirmation': validate_free_confirmation(self.settings['free_tier_confirmed'])}
        stop = None
        try: self.store.finish(attempt, response, tokens=usage['total_tokens'] if valid_usage else None)
        except Pause as error: stop = error
        self.store.headers(response['headers'])
        record = self._checkpoint(row, key, prompt, response)
        if stop: raise stop
        if not valid_usage: raise Pause('usage_unavailable_stopped_after_checkpoint')
        return self._resolved(row, record)

    def _checkpoint(self, row, key, prompt, response):
        body = response['body']
        try:
            choice, = body['choices']; content = choice['message']['content']; finish = choice['finish_reason']
        except (TypeError, KeyError, ValueError): content, finish = '', 'invalid'
        checked = validate_integration_response(content, row)
        if finish != 'stop': checked = {'status': 'invalid_integration_response', 'prediction': None, 'validation_errors': ['finish_reason']}
        record = {'policy_version': VERSION, 'request_key': key, 'page_id': row['evaluation']['page_id'],
                  'input_fingerprint': row['input_fingerprint'], 'model_id': MODEL, 'prompt_hash': prompt['prompt_hash'],
                  'schema_hash': prompt['schema_hash'], 'response_content': content, 'finish_reason': finish,
                  'response_fingerprint': response['response_fingerprint'], 'response': body,
                  'usage': body.get('usage'), 'headers': response['headers'], 'token_estimate': response.get('token_estimate'), **checked}
        self.store.save_result(key, record)
        return record

    @staticmethod
    def _resolved(row, record):
        result = resolve_classification(row, record['prediction'], origin='integration_cache', unresolved_reason=record['status'])
        return {**result, 'request_key': record['request_key']}


def validate_integration_settings(cfg):
    fields = {'version', 'model_id', 'max_requests', 'max_total_tokens', 'rpm', 'rpd', 'tpm', 'tpd',
              'max_attempts', 'max_wait_seconds', 'max_runtime_seconds', 'max_output_tokens', 'timeout_seconds'}
    if set(cfg) != fields or cfg['version'] != VERSION or cfg['model_id'] != MODEL:
        raise ValueError('Unsupported integration configuration')
    caps = {'max_requests': 61, 'max_total_tokens': 100000, 'rpm': 30, 'rpd': 1000, 'tpm': 8000, 'tpd': 200000,
            'max_attempts': 1, 'max_wait_seconds': 60, 'max_runtime_seconds': 900, 'max_output_tokens': 512, 'timeout_seconds': 30}
    for key, cap in caps.items():
        if type(cfg[key]) is not int or not 0 < cfg[key] <= cap: raise ValueError('Invalid finite budget: ' + key)
    return cfg


def run_integration(data, output, config_path, env_file, *, resume=False):
    started = time.time(); invocation = uuid.uuid4().hex
    cfg = validate_integration_settings(_read(config_path))
    source = data['manifest']['source']; original = Path(source['legacy_ledger'])
    protected = [data['root'], *source['immutable_source_paths']]
    output = Path(output).resolve()
    owner = {'format': VERSION, 'dataset_manifest_id': data['manifest']['manifest_id'], 'settings': cfg}
    if output.exists():
        if not resume or _read(output / 'integration-owner.json') != owner:
            raise ValueError('Existing integration output requires matching owner/config and explicit resume')
    else:
        _safe_output(output, protected)
        _new(output / 'integration-owner.json', _bytes(owner))
    pinned = source['pinned_source_files']
    if any(_sha(p) != h for p, h in pinned.items()): raise ValueError('Pinned original source changed')
    stats = {p: (Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in pinned if not p.endswith('-wal')}
    databases = [Path(p) for p in pinned if Path(p).suffix == '.sqlite']
    empty_sha = __import__('hashlib').sha256(b'').hexdigest()
    authorization_deadline = started + cfg['max_runtime_seconds']

    def sidecars_unchanged():
        for database in databases:
            wal = Path(str(database) + '-wal')
            actual = _sha(wal) if wal.is_file() and wal.stat().st_size else empty_sha
            if actual != pinned.get(str(wal), empty_sha): return False
        return True

    def guard():
        if time.time() >= authorization_deadline: raise Pause('execution_runtime_budget')
        if (not sidecars_unchanged() or any(not Path(p).exists() or
                (Path(p).stat().st_size, Path(p).stat().st_mtime_ns) != state for p, state in stats.items())):
            raise Pause('original_source_or_ledger_changed')

    # One shared integration ledger per checkout, not one per output directory.
    # Different invocations therefore retain account windows and share a lease.
    ledger_path = ROOT / 'data/experiments/groq-integration-rate-1/ledger.sqlite'
    meter_owner = ledger_path.parent / 'origin.json'
    meter_identity = {'policy_version': VERSION, 'original_ledger': str(original.resolve()),
                      'original_ledger_sha256': pinned[str(original.resolve())],
                      'dataset_manifest_id': data['manifest']['manifest_id'], 'settings': cfg}
    if not ledger_path.exists():
        if resume: raise ValueError('Missing integration ledger on resume; no budget reset')
        if meter_owner.exists(): raise ValueError('Shared meter ledger missing; manual reconciliation required')
        _safe_output(ledger_path.parent, protected)
        _new(meter_owner, _bytes({'identity': meter_identity, 'started_at_unix': started,
                                  'deadline_unix': authorization_deadline}))
        clone_rate_ledger(original, ledger_path)
    elif not meter_owner.is_file() or _read(meter_owner).get('identity') != meter_identity:
        raise ValueError('Shared meter origin differs; no quota reset')
    authorization_deadline = _read(meter_owner)['deadline_unix']
    # Changing output/config cannot create fresh per-dataset request headroom.
    scope = {'policy_version': VERSION, 'dataset_manifest_id': data['manifest']['manifest_id']}
    limits = {**cfg, 'free_tier_confirmed': load_free_tier_confirmation(Path(env_file))['confirmed']}
    status = 'COMPLETE'; retry = None
    profile = select_model([MODEL], MODEL)
    class GuardedStore(ExperimentStore):
        def reserve(self, *args, **kwargs):
            guard()
            return super().reserve(*args, **kwargs)

    with GuardedStore(ledger_path, scope, limits) as store:
        with store.lease():
            offline_classifier = IntegratedClassifier(store, None, profile, data['manifest']['config']['classification'], limits)
            recovered_http = set()
            def collected(recover=True):
                values = {}
                for row in data['rows']:
                    if row['evaluation']['baseline_outcome'] == 'completed' or not row['trusted_lead']: continue
                    key = integration_key(row, profile, build_integration_prompt(row, data['manifest']['config']['classification']), limits)
                    cached = store.cached(key)
                    if cached is None and recover:
                        previous = store.successful_response(key)
                        if previous is not None:
                            cached = offline_classifier._checkpoint(row, key,
                                build_integration_prompt(row, data['manifest']['config']['classification']), previous['response'])
                            recovered_http.add(key)
                    if cached is not None: values[row['evaluation']['page_id']] = cached
                return values
            before = store.summary(); initial_cache = collected(); initial = replay_shadow(data, initial_cache)
            targets = set(initial['pending_page_ids'])
            error_type = None
            try:
                if targets:
                    validate_free_confirmation(limits['free_tier_confirmed'])
                    key = load_credentials(Path(env_file))
                    if key is None: raise Pause('credentials_missing')
                    from .token_estimation import estimate_request
                    first = next(r for r in data['rows'] if r['evaluation']['page_id'] in targets)
                    prompt = build_integration_prompt(first, data['manifest']['config']['classification'])
                    estimate_request({'model': MODEL, 'messages': prompt['messages'], **profile['request_kwargs'],
                                      'max_completion_tokens': cfg['max_output_tokens'], 'response_format': response_format()})
                    guard()
                    with GroqClient(key, timeout=cfg['timeout_seconds']) as client:
                        metadata_classifier = IntegratedClassifier(store, client, profile, data['manifest']['config']['classification'], limits)
                        metadata_classifier.started = started; metadata_classifier.invocation_id = invocation
                        models = integration_metadata(client, store, invocation, limits, metadata_classifier, guard)
                        actual = select_model(models['models'], MODEL)
                        if actual['request_kwargs'] != profile['request_kwargs']: raise Pause('provider_profile_changed')
                        classifier = IntegratedClassifier(store, client, profile, data['manifest']['config']['classification'], limits)
                        classifier.started = started; classifier.invocation_id = invocation
                        checked = 0
                        for row in data['rows']:
                            if row['evaluation']['page_id'] not in targets: continue
                            classifier.classify(row, dispatch_guard=guard)
                            checked += 1
                            print(json.dumps({'event': 'checkpoint', 'processed_new_schema': checked,
                                              'new_scope_attempts': store.summary()['physical_attempts']}), flush=True)
            except (Pause, ProviderError) as error:
                status = error.code; retry = getattr(error, 'retry_at', None)
            except (Exception, KeyboardInterrupt) as error:
                status = 'EXPERIMENT_STOPPED'; error_type = type(error).__name__
            summary = store.summary()
            report = replay_shadow(data, collected(recover=False))
            receipts = store.db.execute("SELECT receipt_json FROM attempts WHERE scope_id=? AND endpoint='chat' AND status='succeeded'", (store.scope_id,)).fetchall()
            usage = CounterUsage(receipts)
            report['operations'] = {'status': status, 'invocation_id': invocation, 'retry_at_unix': retry,
                                    'budget': cfg, 'scope_cumulative': summary,
                                    'new_attempts_this_invocation': summary['physical_attempts'] - before['physical_attempts'],
                                    'cached_new_schema_at_start': len(initial_cache),
                                    'saved_http_responses_recovered_without_post': len(recovered_http),
                                    'shared_rate_ledger': str(ledger_path),
                                    'elapsed_seconds': time.time() - started, 'provider_usage': usage,
                                    'authorization_deadline_unix': authorization_deadline,
                                    'free_tier_user_confirmed': limits['free_tier_confirmed'],
                                    'actual_invoice_cost_usd': None, 'estimated_free_plan_spend_usd': 0 if limits['free_tier_confirmed'] else None,
                                    'paid_list_price_equivalent_usd': (usage['prompt_tokens'] * .8 + usage['completion_tokens'] * 4) / 1000000,
                                    'pricing_source': 'https://console.groq.com/docs/models',
                                    'list_price_is_not_actual_charge': True, 'error_type': error_type,
                                    'original_ledger_unchanged': _sha(original) == pinned[str(original.resolve())] and sidecars_unchanged()}
            _new(output / 'invocations' / (invocation + '.json'), _bytes(report['operations']))
            report_root = output / 'reports' / invocation
            write_shadow_report(report, report_root)
            return report


def CounterUsage(receipts):
    totals = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'unknown_usage_responses': 0}
    for (text,) in receipts:
        usage = json.loads(text).get('body', {}).get('usage')
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']:
            totals['unknown_usage_responses'] += 1; continue
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'): totals[key] += usage[key]
    return totals
