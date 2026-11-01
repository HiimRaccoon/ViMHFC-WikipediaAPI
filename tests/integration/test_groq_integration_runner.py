import copy
import json
import sqlite3
from pathlib import Path
import pytest

from tests.integration.test_groq_integration_policy import row, response


def settings():
    return {'max_requests': 61, 'max_total_tokens': 100000, 'rpm': 30, 'rpd': 1000,
            'tpm': 8000, 'tpd': 200000, 'max_attempts': 1, 'max_wait_seconds': 60,
            'max_runtime_seconds': 900, 'max_output_tokens': 512, 'timeout_seconds': 30,
            'model_id': 'qwen/qwen3.8-27b', 'free_tier_confirmed': True}


class Client:
    def __init__(self, value=None, error=None): self.calls = []; self.value = value or response(); self.error = error
    def complete(self, payload):
        self.calls.append(copy.deepcopy(payload))
        if self.error: raise self.error
        return {'body': {'model': payload['model'], 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.value)}}],
                         'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}},
                'headers': {}, 'http_status': 200, 'elapsed_seconds': .1, 'response_fingerprint': 'synthetic'}


def make(tmp_path, config, client, cfg=None):
    from vimfc.experiments.groq_classification.integration_runner import IntegratedClassifier
    from vimfc.experiments.groq_classification.store import ExperimentStore
    from vimfc.experiments.groq_classification.provider import select_model
    now = [1000.]
    cfg = cfg or settings()
    store = ExperimentStore(tmp_path / 'ledger.sqlite', {'integration': 'fixture'}, cfg, clock=lambda: now[0])
    classifier = IntegratedClassifier(store, client, select_model([cfg['model_id']], cfg['model_id']),
                                      config['classification'], cfg, clock=lambda: now[0],
                                      sleep=lambda n: now.__setitem__(0, now[0] + n),
                                      estimator=lambda payload: {'reserved_tokens': 500, 'actual_tokens': None, 'method': 'synthetic'})
    return classifier, store


def test_verified_and_untrusted_routes_never_call_groq(tmp_path, config):
    c, store = make(tmp_path, config, Client())
    try:
        assert c.classify(row(True))['classification_state'] == 'wikidata_verified_primary'
        untrusted = row(); untrusted['trusted_lead'] = False
        assert c.classify(untrusted)['classification_state'] == 'unresolved'
        assert not c.client.calls and store.summary()['physical_attempts'] == 0
    finally: store.__exit__()


def test_resume_and_http_before_checkpoint_crash_never_duplicate_post(tmp_path, config):
    from vimfc.experiments.groq_classification.integration_runner import integration_key
    from vimfc.experiments.groq_classification.integration_policy import build_integration_prompt
    c, store = make(tmp_path, config, Client())
    try:
        result = c.classify(row()); key = integration_key(row(), c.profile, build_integration_prompt(row(), c.rules), c.settings)
        assert result['llm_provisional_primary'] == 'Organization'
        assert c.classify(row()) == result and len(c.client.calls) == 1
        # Simulate publication lost after the provider receipt committed.
        store.db.execute('DELETE FROM results WHERE request_key=?', (key,))
        assert c.classify(row()) == result and len(c.client.calls) == 1
        assert store.summary()['physical_attempts'] == 1
    finally: store.__exit__()


def test_invalid_terminal_and_unknown_http_are_not_automatically_retried(tmp_path, config):
    from vimfc.experiments.groq_classification.provider import ProviderError
    from vimfc.experiments.groq_classification.store import Pause
    c, store = make(tmp_path, config, Client(value=response(article_subject='Other')))
    try:
        assert c.classify(row())['classification_state'] == 'unresolved'
        assert c.classify(row())['classification_state'] == 'unresolved'
        assert len(c.client.calls) == 1
    finally: store.__exit__()
    second = tmp_path / 'unknown'; second.mkdir()
    c, store = make(second, config, Client(error=ProviderError('network_error')))
    try:
        with pytest.raises(Pause): c.classify(row())
        with pytest.raises(Pause): c.classify(row())
        assert len(c.client.calls) == 1 and store.summary()['charged_tokens'] == 500
    finally: store.__exit__()


def test_finite_budget_cannot_be_reset_by_resume(tmp_path, config):
    from vimfc.experiments.groq_classification.store import Pause
    cfg = settings(); cfg['max_requests'] = 1
    c, store = make(tmp_path, config, Client(), cfg)
    try:
        c.classify(row())
        other = row(); other['evaluation']['page_id'] = 18
        with pytest.raises(Pause, match='request_budget_exhausted'): c.classify(other)
        assert len(c.client.calls) == 1
    finally: store.__exit__()


def test_readonly_ledger_backup_preserves_prior_usage_without_modifying_source(tmp_path):
    from vimfc.experiments.groq_classification.store import ExperimentStore, Pause
    from vimfc.experiments.groq_classification.integration_runner import clone_rate_ledger
    from vimfc.experiments.groq_classification.human_review import _sha
    source = tmp_path / 'old.sqlite'; cfg = settings()
    with ExperimentStore(source, {'old': 'scope'}, cfg, clock=lambda: 1000) as old:
        attempt = old.reserve('old-key', 7900, cfg['model_id'], 'chat', 'old')
        old.finish(attempt, {'status': 'synthetic'}, tokens=7900)
    before = _sha(source)
    target = tmp_path / 'copy.sqlite'
    clone_rate_ledger(source, target)
    assert _sha(source) == before
    with ExperimentStore(target, {'new': 'scope'}, cfg, clock=lambda: 1001) as new:
        assert new.summary()['physical_attempts'] == 0
        with pytest.raises(Pause, match='minute_rate_limit'):
            new.reserve('new-key', 500, cfg['model_id'], 'chat', 'new')
    with pytest.raises(FileExistsError): clone_rate_ledger(source, target)


@pytest.mark.parametrize('failure_mode', ['none', 'publication_crash', 'original_wal_write', 'deadline', 'new_manifest'])
def test_full_runner_uses_one_shared_meter_and_resume_reuses_results(tmp_path, config, monkeypatch, failure_mode):
    from vimfc.experiments.groq_classification import integration_runner as runner
    from vimfc.experiments.groq_classification.integration_shadow import freeze_shadow, load_shadow
    from vimfc.experiments.groq_classification.store import ExperimentStore
    from vimfc.experiments.groq_classification.human_review import _sha
    source_dir = tmp_path / 'origin'; source_dir.mkdir()
    original = source_dir / 'old.sqlite'
    with ExperimentStore(original, {'old': True}, settings()): pass
    origin_hash = _sha(original)
    dataset = tmp_path / 'packet'
    frame = [row()]
    if failure_mode == 'deadline':
        extra = row(); extra['evaluation']['page_id'] = 18; frame.append(extra)
    freeze_shadow(frame, {}, dataset, source={'legacy_ledger': str(original),
                  'pinned_source_files': {str(original): origin_hash}, 'immutable_source_paths': [str(source_dir)]}, config=config)
    data = load_shadow(dataset)
    cfg = {**settings(), 'version': 'wikidata-first-groq-provisional-1'}; cfg.pop('free_tier_confirmed')
    cfg['max_requests'] = 2  # metadata + one chat exhaust the complete scope
    cfg_path = tmp_path / 'settings.json'; cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    client = Client()
    writers = []
    def list_models():
        if failure_mode == 'original_wal_write':
            writer = ExperimentStore(original, {'concurrent': True}, settings())
            attempt = writer.reserve('concurrent-key', 7900, cfg['model_id'], 'chat', 'other-worker')
            writer.finish(attempt, {'synthetic': True}, tokens=7900)
            writers.append(writer)  # hold the new WAL open
        return {'models': [{'id': cfg['model_id']}], 'headers': {}, 'http_status': 200,
                'elapsed_seconds': .01, 'response_fingerprint': 'synthetic-models'}
    client.list_models = list_models
    class Context:
        def __enter__(self): return client
        def __exit__(self, *args): pass
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'GroqClient', lambda *a, **k: Context())
    monkeypatch.setattr(runner, 'load_credentials', lambda *a: 'synthetic-never-sent')
    monkeypatch.setattr(runner, 'load_free_tier_confirmation', lambda *a: {'confirmed': True})
    import vimfc.experiments.groq_classification.token_estimation as tokens
    monkeypatch.setattr(tokens, 'estimate_request', lambda *a: {'reserved_tokens': 500, 'method': 'synthetic', 'actual_tokens': None})
    checkpoint = runner.IntegratedClassifier._checkpoint
    if failure_mode == 'publication_crash':
        def crash(*args, **kwargs): raise RuntimeError('synthetic publication crash')
        monkeypatch.setattr(runner.IntegratedClassifier, '_checkpoint', crash)
    output = tmp_path / 'run'
    try: first = runner.run_integration(data, output, cfg_path, 'unused')
    finally:
        for writer in writers: writer.__exit__()
    if failure_mode == 'original_wal_write':
        assert not client.calls
        assert first['operations']['status'] == 'original_source_or_ledger_changed'
        assert first['operations']['original_ledger_unchanged'] is False
        return
    if failure_mode == 'new_manifest':
        changed = {**data, 'manifest': {**data['manifest'], 'manifest_id': 'another-dataset'}}
        with pytest.raises(ValueError, match='origin differs'):
            runner.run_integration(changed, tmp_path / 'new-run', cfg_path, 'unused')
        assert len(client.calls) == 1
        return
    if failure_mode == 'deadline':
        meter = tmp_path / 'data/experiments/groq-integration-rate-1/origin.json'
        value = json.loads(meter.read_text(encoding='utf-8')); value['deadline_unix'] = 0
        meter.write_text(json.dumps(value), encoding='utf-8')
        second = runner.run_integration(data, output, cfg_path, 'unused', resume=True)
        assert second['operations']['status'] == 'execution_runtime_budget'
        assert second['operations']['new_attempts_this_invocation'] == 0
        assert second['pending_live_count'] == 1 and len(client.calls) == 1
        return
    if failure_mode == 'publication_crash':
        assert first['operations']['status'] == 'EXPERIMENT_STOPPED'
        assert list((output / 'invocations').glob('*.json'))
        monkeypatch.setattr(runner.IntegratedClassifier, '_checkpoint', checkpoint)
    with sqlite3.connect(tmp_path / 'data/experiments/groq-integration-rate-1/ledger.sqlite') as db:
        db.execute("DELETE FROM results WHERE json_extract(record_json,'$.policy_version')=?", ('wikidata-first-groq-provisional-1',))
    second = runner.run_integration(data, output, cfg_path, 'unused', resume=True)
    assert first['counts']['llm_provisional_primary'] == (0 if failure_mode == 'publication_crash' else 1)
    assert second['counts']['llm_provisional_primary'] == 1
    assert len(client.calls) == 1
    assert first['operations']['scope_cumulative']['physical_attempts'] == 2
    assert second['operations']['new_attempts_this_invocation'] == 0
    assert second['operations']['cached_new_schema_at_start'] == 1
    assert _sha(original) == origin_hash


@pytest.mark.parametrize('key,value', [('max_requests', 62), ('max_total_tokens', 100001), ('rpm', 31),
                                      ('tpm', 8001), ('tpd', 200001), ('max_attempts', 2),
                                      ('max_runtime_seconds', 901), ('max_requests', True),
                                      ('model_id', 'openai/gpt-oss-20b')])
def test_live_envelope_refuses_cap_increase_and_model_fallback(key, value):
    from vimfc.experiments.groq_classification.integration_runner import validate_integration_settings
    cfg = {**settings(), 'version': 'wikidata-first-groq-provisional-1'}; cfg.pop('free_tier_confirmed')
    with pytest.raises(ValueError): validate_integration_settings({**cfg, key: value})


def test_usage_unknown_is_not_fabricated_as_zero_or_charged_twice():
    from vimfc.experiments.groq_classification.integration_runner import CounterUsage
    receipts = [(json.dumps({'body': {'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}}),),
                (json.dumps({'body': {}}),)]
    usage = CounterUsage(receipts)
    assert usage == {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'unknown_usage_responses': 1}


def test_model_discovery_429_retains_lower_provider_caps(tmp_path, config):
    from vimfc.experiments.groq_classification.integration_runner import integration_metadata
    from vimfc.experiments.groq_classification.provider import ProviderError
    from vimfc.experiments.groq_classification.store import Pause
    client = Client()
    def limited():
        raise ProviderError('rate_limited', status=429, headers={
            'x-ratelimit-remaining-requests': '0', 'x-ratelimit-reset-requests': '1d', 'x-ratelimit-limit-tokens': '100'})
    client.list_models = limited
    c, store = make(tmp_path, config, client)
    try:
        with pytest.raises(Pause): integration_metadata(client, store, 'fixture', settings(), c, lambda: None)
        assert store.get('rate_headers')['remaining_requests'] == 0
        assert store.get('rate_headers')['limit_tokens'] == 100
        with pytest.raises(Pause): store.reserve('another', 500, c.profile['model_id'], 'chat', 'fixture')
    finally: store.__exit__()
