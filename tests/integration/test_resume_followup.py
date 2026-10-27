"""No sockets: deferred-only scope, exact timing ownership, export RAM and hash rejection."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

import httpx
import pytest

from vimfc.domain import SourceRevisionRef, SourceUnavailable, canonical, digest
from vimfc.pipeline.runner import Builder, StageExecutor
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.helpers import article
from tests.integration.test_deferred_resume import cohort, FUTURE, PAST
from tests.unit.test_classify import SNAP


def test_backoff_belongs_to_manifest_acquisition_not_pure_classifier(tmp_path, config):
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run, 1)
        client = SimpleNamespace(metrics=Counter())
        class Provider:
            def __init__(self):
                self.client = client
                self.executor = StageExecutor(store, run, client=client)
            def process_dependencies(self, candidate):
                def acquire():
                    client.metrics['wikidata:historical_classification:backoff_seconds'] += .005
                    time.sleep(.01)
                    return {'prepared': True}
                self.executor.execute('acquire_manifest', 1, {}, acquire)
                return {}
            def process(self, candidate):
                self.executor.execute('classify', 1, {}, lambda: {'outcome': 'completed'})
                return article(1)
        report = Builder(store, run, Provider(), tmp_path/'reports')._process(1, False)
        stages = report['performance']['stages']
        assert stages['acquire_manifest']['backoff_seconds'] == .005
        assert stages['classify']['backoff_seconds'] == 0
        assert stages['process']['backoff_seconds'] == 0  # acquisition precedes process
        assert stages['classify']['active_seconds'] == stages['classify']['wall_seconds']


def test_resource_sampler_sees_memory_during_report_serialization(tmp_path, config, monkeypatch):
    from vimfc.pipeline.report import ResourceSampler
    rss = [100]
    original_sample = ResourceSampler.sample
    def sample(self):
        original_sample(self)
        if self.samples:
            self.samples[-1]['rss_bytes'] = rss[0]
    monkeypatch.setattr(ResourceSampler, 'sample', sample)
    atomic = Builder._atomic
    def writing(path, value):
        if path.name == 'metrics.json':
            rss[0] = 1000000000
            # Trigger the same sample the background thread takes during exports.
            for sampler in live:
                if not sampler.stop.is_set():
                    sampler.sample()
        atomic(path, value)
    live = []
    enter = ResourceSampler.__enter__
    def entered(self):
        live.append(self)
        return enter(self)
    monkeypatch.setattr(ResourceSampler, '__enter__', entered)
    monkeypatch.setattr(Builder, '_atomic', staticmethod(writing))
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run, 1)
        provider = SimpleNamespace(process=lambda candidate: article(1))
        result = Builder(store, run, provider, tmp_path/'reports')._process(1, False)
        assert result['performance']['resources']['peak_rss_bytes'] == 1000000000
        assert result['performance']['resources']['includes_report_exports'] is True
        disk = json.loads((tmp_path/'reports'/run/'1'/'metrics.json').read_text(encoding='utf-8'))
        assert disk['performance']['resources'] == result['performance']['resources']
        assert live[0].stop.is_set()


def test_deferred_only_never_reenters_completed_or_terminal_failed_candidates(tmp_path, config):
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run, 3)
        snapshot = store.run(run)
        completed = article(1)
        deps = {'snapshot': snapshot['snapshot'], 'config': snapshot['config_hash'],
                'candidate': {'page_id': 1, 'title': 'Article 1'},
                'input': {'manifest_id': completed['classification']['manifest_id'],
                          'article_hash': digest(completed['classification']['dependency_manifest']['article'])}}
        store.publish_manifest(run, 1, 'request', completed['classification']['dependency_manifest'])
        store.complete(run, 1, 'process', digest(deps), completed)
        before = dict(store.db.execute("SELECT * FROM stage_results WHERE stage='process'").fetchone())
        for page, status, deadline in [(1, 'processed', None), (2, 'deferred', PAST), (3, 'source_failed', None)]:
            record = {'page_id': page, 'status': status, 'retry_at': deadline, 'error_code': 'maxlag' if page == 2 else 'source_hash_mismatch'}
            store.db.execute('INSERT INTO candidate_work VALUES (?,?,?,?,?,?,?)', (run, page, status, canonical(record), deadline, 1, PAST))
        calls = []
        class Provider:
            def process(self, candidate):
                calls.append(candidate['page_id'])
                return article(candidate['page_id'])
        result = Builder(store, run, Provider(), tmp_path/'reports', deferred_only=True)._process(3, False)
        assert calls == [2]
        assert result['work_counts'] == {'attempted': 1, 'processed': 2, 'deferred': 0, 'source_failed': 1}
        assert result['scheduler']['checkpoint_restored'] == 2
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE stage='process' AND identity='1'").fetchone()) == before


@pytest.mark.parametrize('reported', ['0'*40, None])
def test_rejected_source_hash_keeps_exact_diagnostics_and_never_publishes_raw(tmp_path, reported):
    content = '{"id":"Q10761835","claims":{}}'
    timestamp = '2013-04-09T03:15:10Z'
    revision = {'revid': 23640705, 'timestamp': timestamp,
                'slots': {'main': {'content': content, 'contentmodel': 'wikibase-item'}}}
    if reported is not None:
        revision['sha1'] = reported
    def respond(request):
        return httpx.Response(200, json={'query': {'pages': [{'pageid': 12035838, 'revisions': [revision]}]}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test', transport=httpx.MockTransport(respond)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        ref = SourceRevisionRef('wikidata', 'Q10761835', 12035838, 'Q10761835', 23640705, timestamp, SNAP)
        with pytest.raises(SourceUnavailable) as error:
            source.fetch_revisions('wikidata', [ref], recover_missing=True)
        assert error.value.code == 'source_hash_mismatch' and not error.value.retryable
        assert error.value.details['reported_sha1'] == reported
        assert error.value.details['computed_sha1'] == hashlib.sha1(content.encode()).hexdigest()
        assert error.value.details['revision_id'] == 23640705
        assert error.value.details['content_sha256'] == hashlib.sha256(content.encode()).hexdigest()
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0] == 0
        assert not list((tmp_path/'raw').rglob('*.gz'))


def test_bad_hash_in_batch_does_not_refetch_offender_or_blame_valid_siblings(tmp_path, config):
    from vimfc.source.dependencies import DependencySource
    from vimfc.source.primary_dependencies import RoleLoader
    calls = []
    refs = [SourceRevisionRef('wikidata', f'Q{i}', i, f'Q{i}', 100+i, '2026-10-13T00:00:00Z', SNAP) for i in (1,2,3)]
    def respond(request):
        ids = [int(v) for v in request.url.params['revids'].split('|')]
        calls.append(ids)
        pages = []
        for revid in ids:
            i = revid-100
            content = canonical({'id': f'Q{i}', 'claims': {}})
            pages.append({'pageid': i, 'revisions': [{'revid': revid, 'timestamp': '2026-10-13T00:00:00Z',
                'sha1': '0'*40 if i==2 else hashlib.sha1(content.encode()).hexdigest(),
                'slots': {'main': {'contentmodel': 'wikibase-item', 'content': content}}}]})
        return httpx.Response(200, json={'query': {'pages': pages}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test', transport=httpx.MockTransport(respond)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        loader = RoleLoader(DependencySource(source, SNAP, config['classification']))
        loader._fetch(refs)
        assert set(loader.items) == {'Q1','Q3'} and set(loader.errors) == {'Q2'}
        assert calls == [[101,102,103],[103]]
        assert loader.errors['Q2'].details['revision_id'] == 102


def test_deferred_only_validates_pinned_artifacts_without_network_recovery(tmp_path, config):
    from dataclasses import replace
    from vimfc.domain import Incompatible
    from vimfc.pipeline.provider import HistoricalProvider
    from tests.integration.test_manifest_provider import Identities, Client
    from tests.integration.test_cached_hierarchy_source import seed
    from tests.unit.test_classify import item
    with PipelineStore(tmp_path/'db') as store:
        run = store.create(SNAP, config)
        cohort(store, run, 1)
        cache = RawCache(tmp_path/'raw')
        raw = Identities().raw_page(1)
        rawref = cache.put('viwiki', raw.ref.revision_id, {'page_id':1,'timestamp':raw.ref.revision_timestamp,
            'content':raw.content,'content_model':raw.content_model,'source_sha1':raw.source_sha1,'local_sha256':raw.local_sha256})
        class CachedIdentities(Identities):
            def raw_page(self, page):
                return replace(raw, cache_ref=rawref)
        for q,v in {'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')}.items():
            seed(store,cache,q,v)
        provider = HistoricalProvider(store, run, Client(), cache, identities=CachedIdentities())
        first = Builder(store,run,provider,tmp_path/'reports')._process(1,False)
        assert first['work_counts']['processed'] == 1
        def denied(*args):
            pytest.fail('Restored checkpoints may never recover inputs through HTTP')
        provider.client.query = denied
        after = Builder(store,run,provider,tmp_path/'reports',deferred_only=True)._process(1,False)
        assert after['work_counts']['attempted'] == 0 and after['work_counts']['processed'] == 1
        # A corrupt physical artifact blocks before any deferred worker executes.
        (cache.root/rawref['path']).write_bytes(b'corrupt fixture')
        with pytest.raises(Incompatible,match='cached input validation'):
            Builder(store,run,provider,tmp_path/'reports',deferred_only=True)._process(1,False)


def test_materialization_has_its_own_timer_outside_acquisition_checkpoint(tmp_path, config, monkeypatch):
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.source.dependencies import DependencySource
    from tests.integration.test_primary_provider import setup
    from tests.integration.test_manifest_provider import Identities, Client
    with PipelineStore(tmp_path/'db') as store:
        cache = RawCache(tmp_path/'raw');cfg = setup(store,cache,config);run=store.create(SNAP,cfg)
        client = Client();client.metrics=Counter()
        original=DependencySource.materialize
        def materialize(self,*args,**kwargs):
            client.metrics['wikidata:historical_classification:backoff_seconds']+=.005
            time.sleep(.01)
            return original(self,*args,**kwargs)
        monkeypatch.setattr(DependencySource,'materialize',materialize)
        provider=HistoricalProvider(store,run,client,cache,identities=Identities())
        provider.process_dependencies({'page_id':1})
        assert provider.executor.metrics['materialize_manifest'].get('backoff_seconds',0)==.005
        assert provider.executor.metrics['acquire_manifest'].get('backoff_seconds',0)==0
