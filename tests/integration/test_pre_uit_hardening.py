import importlib.util
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from vimfc.domain import Incompatible, SourceUnavailable
from vimfc.pipeline import incremental_collection as collection
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.integration.test_incremental_collection import prepared, factory, SNAP, HORIZON, TEXT


def receipt(work):
    return json.loads((work / 'last-invocation.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('error,kind', [
    (SourceUnavailable('gone', code='history_unavailable'), 'missing'),
    (SourceUnavailable('namespace', code='article_namespace_excluded'), 'unsupported'),
    (SourceUnavailable('Candidate is not historical wikitext'), 'unsupported'),
    (SourceUnavailable('moved', code='namespace_review_required'), 'needs_review'),
    (SourceUnavailable('Current title lookup has no recorded server-time horizon'), 'needs_review'),
    (SourceUnavailable('HTTP 403', code='http_403'), 'needs_review'),
    (SourceUnavailable('unrecognized response', code='unknown_response'), 'needs_review'),
])
def test_normal_unavailability_does_not_poison_other_candidates(tmp_path, config, monkeypatch, error, kind):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    check = collection._checked_acquisition
    first = []
    def unavailable(provider, candidate):
        if not first:
            first.append(candidate['page_id'])
            raise error
        return check(provider, candidate)
    monkeypatch.setattr(collection, '_checked_acquisition', unavailable)
    result = collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    progress = result['summary']
    assert progress['checkpoint_states'][kind] == 1 and progress['remaining_to_process'] == 4
    assert progress['remaining_to_acquire'] == 0 and progress['remaining_in_prefix'] >= 4
    assert progress['missing'] == int(kind == 'missing')
    assert len(progress['blockers']) == int(kind == 'needs_review')
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert not db.execute("SELECT 1 FROM collection_control WHERE key='fatal'").fetchone()
    result = collection.collect_batch(bundle, work, mode='process', through=5)
    assert len(result['selection']['articles']) == 4
    assert receipt(work)['remaining_to_process'] == 0


@pytest.mark.parametrize('error', [
    SourceUnavailable('opaque', code='source_hash_mismatch', retryable=True),
    SourceUnavailable('Returned revision identity mismatch', code='history_unavailable'),
    SourceUnavailable('Source returned a future revision'),
    SourceUnavailable('Ambiguous historical move chain'),
    SourceUnavailable('Conflicting raw objects for one revision'),
    Incompatible('Checked raw identity/hash differs'),
])
def test_integrity_disposition_overrides_missing_and_retryable_flags(error):
    assert collection.failure_kind(error) == 'integrity_fatal'


def test_wrapped_integrity_cause_is_preserved():
    error = SourceUnavailable('Cannot verify owner')
    error.__cause__ = SourceUnavailable('opaque', code='invalid_historical_cache')
    assert collection.failure_kind(error) == 'integrity_fatal'


@pytest.mark.parametrize('page,title', [(True, 'A'), (1, '')])
def test_malformed_ref_cannot_alias_a_valid_page_id(page, title):
    raw = SimpleNamespace(ref=SimpleNamespace(site='viwiki', page_id=page, title=title,
        snapshot=SNAP, revision_id=101, revision_timestamp=SNAP))
    provider = SimpleNamespace(_acquire_raw=lambda _: raw)
    with pytest.raises(Incompatible, match='identity'):
        collection._checked_acquisition(provider, {'page_id': 1})


def test_unsupported_model_cannot_hide_identity_or_hash_contradiction(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    acquire = HistoricalProvider._acquire_raw
    def wrong_identity(self, candidate):
        raw = acquire(self, candidate)
        return replace(raw, content_model='json', ref=replace(raw.ref, page_id=999999))
    monkeypatch.setattr(HistoricalProvider, '_acquire_raw', wrong_identity)
    with pytest.raises(Incompatible, match='identity'):
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    assert receipt(work)['status'] == 'integrity_fatal'
    assert receipt(work)['unsupported'] == 0


@pytest.mark.parametrize('status,retry_after', [(429, '120'), (503, '120'), (503, 'Wed, 01 Jan 2031 00:00:00 GMT')])
def test_http_stop_respects_durable_retry_after_then_resumes(tmp_path, config, monkeypatch, status, retry_after):
    _, bundle, work, _ = prepared(tmp_path, config)
    calls = []
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    def response(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': retry_after}, text='synthetic unavailable')
    client = lambda: MediaWikiClient('fixture@example.com', attempts=1, transport=httpx.MockTransport(response))
    with pytest.raises(SourceUnavailable):
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=client)
    first = receipt(work)
    assert first['status'] == 'retryable' and first['expected_stop'] and first['exit_code'] == 75
    assert first['physical_requests'] == 1 and first['retryable'] == 1
    assert first['stop_reason']['retry_at'] and not first['blockers']
    with pytest.raises(SourceUnavailable):
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=client)
    assert len(calls) == 1 and receipt(work)['physical_requests'] == 0
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert not db.execute("SELECT 1 FROM collection_control WHERE key='fatal'").fetchone()
        assert db.execute("SELECT retry_at FROM api_cooldowns WHERE site='viwiki'").fetchone()[0] >= first['stop_reason']['retry_at']
        # Advance only synthetic fixture deadlines; production code never clears cooldowns.
        db.execute("UPDATE api_cooldowns SET retry_at='2000-01-01T00:00:00Z'")
        db.execute("UPDATE stage_results SET next_retry_at='2000-01-01T00:00:00Z' WHERE status='deferred'")
        db.execute("UPDATE source_failures SET retry_at='2000-01-01T00:00:00Z' WHERE retryable=1")
    result = collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    assert result['summary']['remaining_to_acquire'] == 0
    assert result['summary']['remaining_to_process'] == 5


@pytest.mark.parametrize('scope', ['invocation', 'collection'])
def test_budget_receipt_is_expected_and_not_a_source_failure(tmp_path, config, monkeypatch, scope):
    _, bundle, work, header = prepared(tmp_path, config)
    if scope == 'collection':
        with sqlite3.connect(work / 'pipeline.sqlite') as db:
            db.execute("UPDATE collection_control SET value=? WHERE key='attempts'", (str(header['request_limit']),))
    calls = []
    with pytest.raises(collection.CollectionBudget) as caught:
        collection.collect_batch(bundle, work, mode='acquire', through=5, max_requests=0,
                                 allow_network=True, client_factory=factory(calls))
    result = receipt(work)
    assert result == caught.value.collection_receipt
    assert result['status'] == 'budget_exhausted' and result['budget_scope'] == scope
    assert result['expected_stop'] and result['exit_code'] == 5 and not result['blockers']
    assert result['physical_requests'] == 0 and not calls
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert not db.execute("SELECT 1 FROM collection_control WHERE key='fatal'").fetchone()
        assert not db.execute("SELECT 1 FROM stage_results WHERE lease_until IS NOT NULL").fetchone()


def test_committed_progress_survives_interrupt_before_python_counter_update(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    assert receipt(work)['remaining_to_process'] == 5
    save = collection._save
    def interrupted(store, status, record):
        save(store, status, record)
        if status == 'processed':
            raise KeyboardInterrupt()
    monkeypatch.setattr(collection, '_save', interrupted)
    with pytest.raises(KeyboardInterrupt):
        collection.collect_batch(bundle, work, mode='process', through=5)
    result = receipt(work)
    assert result['status'] == 'interrupted' and result['eligible_supply'] == 1
    assert result['remaining_to_process'] == 4 and result['remaining_to_acquire'] == 0
    monkeypatch.setattr(collection, '_save', save)
    selection = collection.collect_batch(bundle, work, mode='process', through=5)['selection']
    assert len(selection['articles']) == 5 and receipt(work)['remaining_in_prefix'] == 0


def test_orphan_raw_after_interrupt_is_reused_without_repeating_successful_http(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    put = RawCache.put
    def orphan(cache, site, revision, value):
        put(cache, site, revision, value)
        raise KeyboardInterrupt('After blob publication, before source DB reference')
    monkeypatch.setattr(RawCache, 'put', orphan)
    calls = []
    with pytest.raises(KeyboardInterrupt):
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory(calls))
    assert receipt(work)['status'] == 'interrupted'
    previous = len(calls)
    monkeypatch.setattr(RawCache, 'put', put)
    result = collection.collect_batch(bundle, work, mode='acquire', through=5, batch_size=1, allow_network=True, client_factory=factory(calls))
    assert result['summary']['physical_requests'] == 0 and len(calls) == previous


def test_selection_is_published_under_writer_lock(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    publish = collection._publish_json
    def locked(path, value):
        if path.name == 'selection.json':
            with pytest.raises((Incompatible, OSError)):
                collection.collect_batch(bundle, work, mode='process', through=5)
        return publish(path, value)
    monkeypatch.setattr(collection, '_publish_json', locked)
    result = collection.collect_batch(bundle, work, mode='process', through=5)
    assert json.loads((work / 'selection.json').read_text(encoding='utf-8')) == result['selection']
    assert receipt(work)['selection_published'] is True


def test_failed_selection_replace_preserves_old_export_and_resumes_without_promoting_pending(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    collection.collect_batch(bundle, work, mode='process', through=5, batch_size=2)
    previous = (work / 'selection.json').read_bytes()
    replace = Path.replace
    def full(source, destination):
        if Path(destination).name == 'selection.json':
            raise OSError('synthetic disk full at selection publication')
        return replace(source, destination)
    monkeypatch.setattr(Path, 'replace', full)
    with pytest.raises(OSError, match='disk full'):
        collection.collect_batch(bundle, work, mode='process', through=5)
    assert (work / 'selection.json').read_bytes() == previous
    assert receipt(work)['status'] == 'operational_error' and not receipt(work)['selection_published']
    pending = list(work.glob('selection-*.pending'))
    assert pending
    for path in pending:
        path.write_bytes(b'invalid uncommitted output')
    monkeypatch.setattr(Path, 'replace', replace)
    result = collection.collect_batch(bundle, work, mode='process', through=5)
    assert len(result['selection']['articles']) == 5
    assert receipt(work)['selection_published'] and all(p.exists() for p in pending)


def test_receipt_failure_does_not_hide_original_sqlite_error(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    def original(*args):
        raise sqlite3.OperationalError('original write interrupted')
    def unavailable(*args):
        raise sqlite3.OperationalError('receipt progress unavailable')
    monkeypatch.setattr(collection, '_save', original)
    monkeypatch.setattr(collection, '_progress', unavailable)
    with pytest.raises(sqlite3.OperationalError, match='original write') as caught:
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    result = receipt(work)
    assert result == caught.value.collection_receipt
    assert result['progress_available'] is False and result['eligible_supply'] is None
    assert result['stop_reason']['message'] == 'original write interrupted'
    assert result['receipt_errors'] == ['receipt progress unavailable']


def test_valid_unsupported_model_is_excluded_after_checked_raw_and_namespace(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    import hashlib
    def response(request):
        params = request.url.params
        page = int(params['pageids']) if 'pageids' in params else int(params['revids']) - 100
        revision = {'revid': 100 + page, 'timestamp': SNAP}
        if 'revids' in params:
            revision.update(sha1=hashlib.sha1(TEXT.encode()).hexdigest(),
                            slots={'main': {'content': TEXT, 'contentmodel': 'json'}})
        return httpx.Response(200, json={'curtimestamp': HORIZON,
            'query': {'pages': [{'pageid': page, 'title': f'Article {page}', 'ns': 0, 'revisions': [revision]}]}})
    client = lambda: MediaWikiClient('fixture@example.com', attempts=1, transport=httpx.MockTransport(response))
    result = collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=client)
    assert result['summary']['unsupported'] == 2 and result['summary']['missing'] == 0
    assert not result['summary']['blockers'] and result['summary']['remaining_to_process'] == 3


def test_sqlite_commit_failure_rolls_back_and_does_not_mask_original_error(tmp_path):
    with PipelineStore(tmp_path / 'db') as store:
        real = store.db
        class CommitFailure:
            def __getattr__(self, name):
                return getattr(real, name)
            def commit(self):
                raise sqlite3.OperationalError('synthetic disk full before commit')
        store.db = CommitFailure()
        with pytest.raises(sqlite3.OperationalError, match='disk full'):
            with store.transaction():
                store.db.execute("INSERT INTO api_cooldowns VALUES ('viwiki','2031-01-01T00:00:00Z')")
        assert not real.in_transaction and not real.execute('SELECT * FROM api_cooldowns').fetchall()
        store.db = real
    store = PipelineStore(tmp_path / 'other')
    class FailedCleanup:
        closed = False
        def execute(self, *args):
            pytest.fail('Do not checkpoint WAL while unwinding an error')
        def close(self):
            self.closed = True
    real = store.db; proxy = FailedCleanup(); store.db = proxy
    original = sqlite3.OperationalError('original I/O interruption')
    store.__exit__(type(original), original, None)
    assert proxy.closed
    real.close()


def test_sqlite_operational_stop_is_resumable_and_receipt_survives(tmp_path, config, monkeypatch):
    _, bundle, work, _ = prepared(tmp_path, config)
    monkeypatch.setattr(collection.time, 'sleep', lambda _: None)
    save = collection._save
    monkeypatch.setattr(collection, '_save', lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
    with pytest.raises(sqlite3.OperationalError):
        collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    assert receipt(work)['status'] == 'operational_error' and receipt(work)['exit_code'] == 75
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        assert not db.execute("SELECT 1 FROM collection_control WHERE key='fatal'").fetchone()
    monkeypatch.setattr(collection, '_save', save)
    collection.collect_batch(bundle, work, mode='acquire', through=5, allow_network=True, client_factory=factory([]))
    assert receipt(work)['remaining_to_acquire'] == 0


def test_cli_emits_expected_budget_receipt(tmp_path, config, monkeypatch, capsys):
    _, bundle, work, _ = prepared(tmp_path, config)
    spec = importlib.util.spec_from_file_location('hardening_cli', 'scripts/collection-readiness.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    def budget(*args, **kwargs):
        error = collection.CollectionBudget('fixture budget stop')
        error.collection_receipt = {'status': 'budget_exhausted', 'expected_stop': True, 'exit_code': 5}
        raise error
    monkeypatch.setattr(cli, 'collect_batch', budget)
    assert cli.main(['batch', '--bundle', str(bundle), '--workspace', str(work), '--mode', 'acquire', '--through', '5', '--allow-network']) == 5
    assert json.loads(capsys.readouterr().out)['expected_stop'] is True


def test_real_sigterm_dispatcher_preserves_lease_checkpoint_and_cli_receipt(tmp_path, config):
    _, bundle, work, _ = prepared(tmp_path, config)
    bootstrap = """
import os, sys, runpy, signal, socket
sys.path.insert(0, 'backend/src')
from vimfc.pipeline import incremental_collection as c
def denied(*a, **k): raise RuntimeError('Offline synthetic signal fixture')
socket.socket.connect=denied
socket.getaddrinfo=denied
def interrupted(provider, candidate):
    return provider.executor.execute('synthetic_sigterm', candidate['page_id'], {'fixture': True}, lambda: signal.raise_signal(signal.SIGTERM))
c._checked_acquisition=interrupted
sys.argv=['scripts/collection-readiness.py', 'batch', '--bundle', sys.argv[1], '--workspace', sys.argv[2], '--mode', 'acquire', '--through', '5', '--allow-network']
runpy.run_path('scripts/collection-readiness.py', run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-B', '-c', bootstrap, str(bundle), str(work)],
                            capture_output=True, text=True, timeout=30, env={**os.environ, 'VIMFC_OFFLINE': '1'})
    assert result.returncode == 130, result.stderr
    assert json.loads(result.stdout)['status'] == 'interrupted'
    assert receipt(work)['physical_requests'] == 0
    with sqlite3.connect(work / 'pipeline.sqlite') as db:
        row = db.execute("SELECT lease_until,last_error FROM stage_results WHERE stage='synthetic_sigterm'").fetchone()
        assert row == (None, 'interrupted')
        assert not db.execute("SELECT 1 FROM collection_control WHERE key='fatal'").fetchone()


def test_shell_requeue_guard_stops_before_paths_or_worker_are_read():
    bash = Path(os.environ.get('PROGRAMFILES', '')) / 'Git/bin/bash.exe' if os.name == 'nt' else Path(shutil.which('bash') or '')
    if not bash.is_file():
        pytest.skip('A local Bash interpreter is unavailable')
    template = Path('configs/slurm/annotation-pilot10000.sbatch').read_text(encoding='utf-8')
    prefix = template.split(': "${COLLECTION_REPO')[0]
    result = subprocess.run([str(bash), '-c', prefix], capture_output=True, text=True,
        env={**os.environ, 'COLLECTION_REVIEWED': '1', 'SLURM_RESTART_COUNT': '1'}, timeout=10)
    assert result.returncode == 3
    assert json.loads(result.stderr)['reason'] == 'slurm_requeue_requires_review'
