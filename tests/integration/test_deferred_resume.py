"""Offline reproductions of the Pilot 1000 cooldown and negative-hint failures."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest

from vimfc.domain import SourceUnavailable, canonical, digest
from vimfc.pipeline.runner import Builder, StageExecutor, PilotControls
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.source.mediawiki import MediaWikiClient
from tests.helpers import article
from tests.integration.test_manifest_provider import Identities
from tests.unit.test_classify import SNAP


FUTURE = '2099-01-01T00:00:00Z'
PAST = '2000-01-01T00:00:00Z'


def cohort(store, run, count=3):
    for i in range(1, count + 1):
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',
                         (run, i, i, canonical({'page_id': i, 'title': f'Article {i}'})))


class CoolingProvider:
    def __init__(self):
        self.ready = False
        self.calls = []

    def process(self, candidate):
        page = candidate['page_id']
        self.calls.append(page)
        # One cache-only article is safe even while the source is cooling down.
        if page != 2 and not self.ready:
            raise SourceUnavailable('source maxlag', retryable=True, code='host_cooldown',
                                    retry_at=FUTURE, details={'site': 'wikidata'})
        return article(page)


def test_cooldown_is_deferred_cache_only_work_continues_and_resume_is_unique(tmp_path, config):
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run)
        provider = CoolingProvider()
        builder = Builder(store, run, provider, tmp_path/'reports')
        first = builder._process(3, False)
        first_bytes = (tmp_path/'reports'/run/'3'/'metrics.json').read_bytes()
        assert first['source_failure_count'] == 0
        assert first['work_counts'] == {'attempted': 3, 'processed': 1, 'deferred': 2, 'source_failed': 0}
        assert not first['data_complete'] and not first['pilot_accepted']
        assert first['deferred_count'] == 2
        state = json.loads(store.db.execute("SELECT record_json FROM run_events WHERE stage='progress' ORDER BY event_id DESC LIMIT 1").fetchone()[0])
        assert state['processed'] == 1 and state['queued'] == 2 and state['eta_seconds'] is None
        provider.ready = True
        # A restart before Retry-After must not re-enter the deferred candidates.
        again = Builder(store, run, provider, tmp_path/'reports')._process(3, False)
        assert provider.calls == [1, 2, 3]
        assert again['deferred_count'] == 2
        store.db.execute('UPDATE candidate_work SET next_retry_at=? WHERE run_id=?', (PAST, run))
        store.db.execute('UPDATE stage_results SET next_retry_at=? WHERE run_id=? AND next_retry_at IS NOT NULL', (PAST, run))
        final = Builder(store, run, provider, tmp_path/'reports')._process(3, False)
        assert final['work_counts'] == {'attempted': 3, 'processed': 3, 'deferred': 0, 'source_failed': 0}
        assert final['data_complete'] and not final['pilot_accepted']
        assert provider.calls == [1, 2, 3, 1, 3]
        assert store.db.execute('SELECT count(*) FROM candidate_work WHERE run_id=?', (run,)).fetchone()[0] == 3
        preserved = list((tmp_path/'reports'/run/'3'/'previous-latest').glob('*/metrics.json'))
        assert any(p.read_bytes() == first_bytes for p in preserved)


def test_due_stage_retries_without_manual_requeue_and_retains_error_identity(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, {})
        executor = StageExecutor(store, run)
        def fail():
            raise SourceUnavailable('maxlag', retryable=True, code='maxlag',
                                    retry_at=FUTURE, details={'site': 'wikidata'})
        with pytest.raises(SourceUnavailable):
            executor.execute('acquire_manifest', 1, {}, fail)
        row = store.db.execute('SELECT * FROM stage_results').fetchone()
        assert row['status'] == 'deferred'
        with pytest.raises(SourceUnavailable) as error:
            StageExecutor(store, run).execute('acquire_manifest', 1, {}, lambda: pytest.fail('too early'))
        assert error.value.details['site'] == 'wikidata'
        assert error.value.code == 'maxlag'
        store.db.execute('UPDATE stage_results SET next_retry_at=?', (PAST,))
        assert StageExecutor(store, run).execute('acquire_manifest', 1, {}, lambda: {'ok': True}) == {'ok': True}
        assert store.db.execute('SELECT attempt_count FROM stage_results').fetchone()[0] == 2


def test_old_negative_hint_is_refreshed_once_then_has_ttl_without_rewriting_old_record(tmp_path, config):
    calls = []
    client = SimpleNamespace(stage='')
    def query(site, params):
        calls.append(params)
        return {'query': {'pages': [{'pageid': 1}]}}
    client.query = query
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        provider = HistoricalProvider(store, run, client, RawCache(tmp_path/'raw'), identities=Identities())
        raw = Identities().raw_page(1)
        from vimfc.pipeline.dependencies import article_identity
        dep = digest({'article': article_identity(raw)})
        store.complete(run, 1, 'item_hint', dep, {'qid': None})
        original = dict(store.db.execute("SELECT * FROM stage_results WHERE stage='item_hint'").fetchone())
        for _ in range(2):
            with pytest.raises(SourceUnavailable) as error:
                provider._pinned_qid(raw)
            assert error.value.code == 'missing_item_hint' and error.value.retryable
        assert len(calls) == 1
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE stage='item_hint'").fetchone()) == original
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0] == 0
        store.db.execute("UPDATE stage_results SET next_retry_at=? WHERE stage='item_hint_refresh'", (PAST,))
        client.query = lambda *_: {'query': {'pages': [{'pageid': 1, 'pageprops': {'wikibase_item': 'Q1'}}]}}
        assert provider._pinned_qid(raw) == 'Q1'
        assert store.item_hint(run, 1, article_identity(raw)) == 'Q1'


def test_interruption_releases_stage_lease_and_preserves_completed_work(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, {})
        executor = StageExecutor(store, run)
        executor.execute('acquire', 1, {}, lambda: {'raw': 'pinned'})
        def interrupt():
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            executor.execute('acquire_manifest', 1, {}, interrupt)
        assert executor.execute('acquire', 1, {}, lambda: pytest.fail('must reuse')) == {'raw': 'pinned'}
        assert StageExecutor(store, run).execute('acquire_manifest', 1, {}, lambda: {'manifest': 'same'}) == {'manifest': 'same'}


def test_incomplete_report_cannot_unlock_next_pilot(tmp_path, config):
    from vimfc.domain import InvalidConfig
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        controls = PilotControls(store, run)
        controls.save_report({'run_id': run, 'size': 100, 'completed': True, 'data_complete': False})
        with pytest.raises(InvalidConfig):
            controls.pilot(1000)


def test_client_enforces_maxlag_and_host_cooldown_without_repeat_http(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={'Retry-After': '120'}, json={'error': {'code': 'maxlag', 'info': 'busy'}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test', attempts=1, transport=httpx.MockTransport(handler)) as client:
        client.cooldown_store = store
        with pytest.raises(SourceUnavailable):
            client.query('wikidata', {'action': 'query', 'maxlag': 99})
        assert requests[0].url.params['maxlag'] == '5'
        with pytest.raises(SourceUnavailable) as error:
            client.query('wikidata', {'action': 'query'})
        assert error.value.code == 'host_cooldown' and len(requests) == 1
        deadline = datetime.fromisoformat(error.value.retry_at.replace('Z', '+00:00'))
        assert deadline > datetime.now(timezone.utc) + timedelta(seconds=115)


def test_long_retry_after_defers_instead_of_blocking_native_request_loop(tmp_path):
    sleeps = []
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={'Retry-After': '600'}, json={})
    with MediaWikiClient('fixture@example.test', sleep=sleeps.append, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable) as error:
            client.query('wikidata', {'action': 'query'})
        assert error.value.retry_at and error.value.retryable
        assert len(requests) == 1 and sleeps == []


def test_scheduler_wait_is_bounded_and_only_retries_due_candidates(tmp_path, config):
    from vimfc.pipeline.scheduling import CandidateScheduler
    stamp = [datetime(2026, 10, 25, tzinfo=timezone.utc)]
    calls, sleeps = [], []
    def sleep(seconds):
        sleeps.append(seconds)
        stamp[0] += timedelta(seconds=seconds)
    def work(candidate):
        page = candidate['page_id']
        calls.append(page)
        if stamp[0].second < 10:
            deadline = '2026-10-25T00:00:10Z' if page == 1 else '2026-10-26T00:00:00Z'
            raise SourceUnavailable('busy', retryable=True, code='host_cooldown', retry_at=deadline)
        return article(page)
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        scheduler = CandidateScheduler(store, run, max_wait_seconds=15, retry_rounds=2,
                                       clock=lambda: stamp[0], sleep=sleep)
        records = scheduler.run([{'page_id': 1}, {'page_id': 2}], work)
        assert calls == [1, 2, 1]
        assert sleeps == [5, 5]
        assert records[0].get('status') != 'deferred' and records[1]['status'] == 'deferred'


def test_retry_round_limit_stops_even_when_deadlines_are_already_due(tmp_path, config):
    from vimfc.pipeline.scheduling import CandidateScheduler
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        def work(candidate):
            raise SourceUnavailable('still busy', retryable=True, code='maxlag', retry_at=PAST)
        scheduler = CandidateScheduler(store, run, max_wait_seconds=0, retry_rounds=2)
        records = scheduler.run([{'page_id': 1}], work)
        assert scheduler.visits == 3 and len(records) == 1
        assert records[0]['status'] == 'deferred'


def test_real_provider_handles_cooldown_cache_only_restart_and_frozen_replay(tmp_path, config):
    from vimfc.pipeline.dependencies import article_identity
    from tests.integration.test_cached_hierarchy_source import seed
    from tests.unit.test_classify import item
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run)
        cache = RawCache(tmp_path/'raw')
        for q, value in {'Q1': item('Q1', {'P31': ['Q5'], 'P106': ['Q901']}, 'A'),
                         'Q5': item('Q5'), 'Q901': item('Q901')}.items():
            seed(store, cache, q, value)
        identities = Identities()
        for page, q in [(1, 'Q1'), (2, 'Q2'), (3, 'Q1')]:
            store.pin_item_hint(run, page, article_identity(identities.raw_page(page)), q)
        store.db.execute('INSERT INTO api_cooldowns VALUES (?,?)', ('wikidata', FUTURE))
        def denied(request):
            pytest.fail('No physical source call is permitted in this cache/cooldown fixture')
        with MediaWikiClient('fixture@example.test', transport=httpx.MockTransport(denied)) as client:
            provider = HistoricalProvider(store, run, client, cache, identities=identities)
            first = Builder(store, run, provider, tmp_path/'reports')._process(3, False)
            assert first['work_counts']['processed'] == 2 and first['deferred_count'] == 1
            assert first['source_failure_count'] == 0
            assert first['performance']['request_rates']['physical_request_count'] == 0
            before = list(store.db.execute('SELECT page_id,manifest_id FROM article_manifests ORDER BY page_id'))
        # Offline source recovery: the exact historical artifact becomes available.
        seed(store, cache, 'Q2', item('Q2', {'P31': ['Q5']}, 'A'))
        for table, column in [('api_cooldowns', 'retry_at'), ('source_failures', 'retry_at'),
                              ('stage_results', 'next_retry_at'), ('candidate_work', 'next_retry_at')]:
            store.db.execute(f'UPDATE {table} SET {column}=? WHERE {column} IS NOT NULL', (PAST,))
        store.db.execute('UPDATE candidates SET discovery_order=-discovery_order WHERE run_id=?', (run,))
        with MediaWikiClient('fixture@example.test', transport=httpx.MockTransport(denied)) as client:
            fresh = HistoricalProvider(store, run, client, cache, identities=identities)
            final = Builder(store, run, fresh, tmp_path/'reports')._process(3, False)
            assert final['data_complete'] and final['work_counts']['processed'] == 3
            assert final['fingerprint_audit']['manifest_bound_processes'] == 3
            for page, manifest in before:
                assert store.db.execute('SELECT manifest_id FROM article_manifests WHERE run_id=? AND page_id=?', (run, page)).fetchone()[0] == manifest
            assert final['performance']['request_rates']['physical_request_count'] == 0


def test_optional_retryable_http_frontier_is_reported_as_incomplete_source_coverage(tmp_path, config):
    from tests.integration.test_primary_provider import setup
    with PipelineStore(tmp_path/'db') as store:
        cache = RawCache(tmp_path/'raw')
        cfg = setup(store, cache, config)
        run = store.create(SNAP, cfg)
        cohort(store, run, 1)
        def handler(request):
            if request.url.host == 'vi.wikipedia.org':
                return httpx.Response(200, json={'query': {'pages': [{'pageprops': {'wikibase_item': 'Q1'}}]}})
            return httpx.Response(507, json={})
        with MediaWikiClient('fixture@example.test', attempts=1, transport=httpx.MockTransport(handler)) as client:
            p = HistoricalProvider(store, run, client, cache, identities=Identities())
            report = Builder(store, run, p, tmp_path/'reports')._process(1, False)
            assert report['subtype_audit']['primary_classified'] == 1
            assert report['pinned_transient_manifest_count'] == 1
            assert not report['data_complete']


def test_additive_migration_preserves_legacy_completed_and_failed_rows(tmp_path, config):
    import sqlite3
    path = tmp_path/'db'
    with PipelineStore(path) as store:
        run = store.create(SNAP, config)
        store.complete(run, 1, 'classify', 'manifest-bound', {'outcome': 'completed', 'manifest_id': 'old'})
        stamp = store.run(run)
    db = sqlite3.connect(path)
    db.execute('DROP TABLE candidate_work')
    db.execute('ALTER TABLE stage_results DROP COLUMN error_json')
    db.execute("INSERT INTO stage_results(run_id,identity,stage,dependency_hash,status,attempt_count,next_retry_at,last_error) VALUES (?, '2', 'acquire_manifest', ?, 'failed', 2, ?, 'maxlag')", (run, digest({}), PAST))
    db.commit()
    before = db.execute('SELECT * FROM stage_results ORDER BY identity').fetchall()
    db.close()
    with PipelineStore(path) as store:
        columns = [r['name'] for r in store.db.execute('PRAGMA table_info(stage_results)') if r['name'] != 'error_json']
        after = store.db.execute('SELECT '+','.join(columns)+' FROM stage_results ORDER BY identity').fetchall()
        assert [tuple(r) for r in after] == before
        assert store.run(run) == stamp
        assert store.result(run, 1, 'classify', 'manifest-bound') == {'outcome': 'completed', 'manifest_id': 'old'}
        assert StageExecutor(store, run).execute('acquire_manifest', 2, {}, lambda: {'recovered': True}) == {'recovered': True}


def test_cli_returns_exit5_for_reported_incomplete_pilot_without_claiming_acceptance(tmp_path, config, monkeypatch, capsys):
    from vimfc.cli import main
    config['contact'] = 'fixture@example.test'
    path = tmp_path/'db'
    with PipelineStore(path) as store:
        run = store.create(SNAP, config)
    class ReportedIncomplete:
        def __init__(self, *args, **kwargs):
            pass
        def pilot(self, size):
            return {'completed': True, 'data_complete': False, 'pilot_accepted': False,
                    'work_counts': {'attempted': 100, 'processed': 0, 'deferred': 100, 'source_failed': 0}}
    # Only replace the work executor: parser, gate, immutable store/config,
    # provider setup and public CLI exit/report contract remain real and offline.
    monkeypatch.setattr('vimfc.pipeline.runner.Builder', ReportedIncomplete)
    code = main(['pipeline', 'pilot', '--database', str(path), '--run', run,
                 '--candidates', '100', '--max-wait-seconds', '0', '--retry-rounds', '0'])
    output = json.loads(capsys.readouterr().out)
    assert code == 5 and output['completed'] and not output['data_complete']
    assert output['pilot_accepted'] is False and output['work_counts']['deferred'] == 100
