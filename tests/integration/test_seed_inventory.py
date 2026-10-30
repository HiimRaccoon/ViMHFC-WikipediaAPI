from copy import deepcopy
from importlib import import_module
import json
import pytest
from vimfc.domain import Incompatible, SourceUnavailable, digest


def api():
    return import_module('vimfc.source.seed_inventory')


class Client:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def query(self, site, params, **kwargs):
        self.calls.append((site, params, kwargs))
        assert site == 'viwiki' and params['action'] == 'query'
        return next(self.payloads)


def payload(rows, cursor=None, key='categorymembers'):
    result = {'curtimestamp': '2026-10-28T00:00:00Z', 'query': {key: rows}}
    if cursor:
        result['continue'] = cursor
    return result


def settings(config):
    return api().inventory_policy(config, target=60000, max_requests=800)


def test_atomic_checkpoint_dedup_and_reopen_keep_all_origins(tmp_path, config):
    cfg = settings(config)
    db = tmp_path/'inventory.sqlite'
    row = {'pageid': 1, 'title': 'A', 'ns': 0, 'type': 'page'}
    client = Client([payload([row]), payload([row, {'pageid': 2, 'title': 'B', 'ns': 0, 'type': 'page'}])])
    with api().SeedInventory(db, cfg) as store:
        store.step(client, 'people')
    with api().SeedInventory(db, cfg) as store:
        store.step(client, 'organizations')
        frozen = store.freeze(config)
        assert len(frozen['seeds']) == 2
        assert len(frozen['seeds'][0]['origins']) == 2
        assert frozen['seeds'][0]['namespace'] == 0
        assert frozen['seeds'][0]['candidate']['discovery_metadata_only'] is True
        assert frozen['seeds'][0]['candidate']['discovery_namespace_status'] == 'unverified'
        assert frozen['source']['unique_candidates'] == 2
        assert frozen['source']['duplicate_observations'] == 1
        assert frozen['source']['target_reached'] is False
        assert store.freeze(config) == frozen
        with pytest.raises(Incompatible): store.step(client, 'people')


def test_cursor_and_category_cycles_are_bounded_and_resume_identically(tmp_path, config):
    cfg = settings(config)
    cursor = {'cmcontinue': 'next', 'continue': '-||'}
    category = {'pageid': 20, 'title': cfg['roots']['people'], 'ns': 14, 'type': 'subcat'}
    client = Client([payload([category], cursor), payload([], cursor)])
    with api().SeedInventory(tmp_path/'db', cfg) as store:
        store.step(client, 'people')
        before = store.state('people')
        with pytest.raises(Incompatible, match='cursor'): store.step(client, 'people')
        assert store.state('people') == before
        assert len(store.state('people')['queue']) == 1
        assert client.calls[-1][1]['cmcontinue'] == 'next'


@pytest.mark.parametrize('bad', ['namespace', 'identity', 'conflict', 'missing_timestamp'])
def test_invalid_listings_do_not_publish_partial_pages(tmp_path, config, bad):
    cfg = settings(config)
    good = {'pageid': 1, 'title': 'A', 'ns': 0, 'type': 'page'}
    other = dict(good, pageid=2, title='B')
    rows = [good, other]
    if bad == 'namespace': other['ns'] = 6
    if bad == 'identity': other['pageid'] = True
    if bad == 'conflict': other.update(pageid=1, title='Changed')
    p = payload(rows)
    if bad == 'missing_timestamp': p.pop('curtimestamp')
    with api().SeedInventory(tmp_path/'db', cfg) as store:
        with pytest.raises(Incompatible): store.step(Client([p]), 'people')
        assert store.count() == 0


def test_request_cap_and_source_error_preserve_checkpoint_without_loop(tmp_path, config):
    cfg = settings(config); cfg['max_requests'] = 1
    cursor = {'cmcontinue': 'next', 'continue': '-||'}
    client = Client([payload([], cursor)])
    with api().SeedInventory(tmp_path/'db', cfg) as store:
        store.step(client, 'people')
        with pytest.raises(Incompatible, match='budget'): store.step(client, 'people')
        assert len(client.calls) == 1
        assert store.state('people')['continuation'] == cursor


def test_resume_refuses_policy_change(tmp_path, config):
    cfg = settings(config)
    with api().SeedInventory(tmp_path/'db', cfg): pass
    cfg['seed'] = 'different'
    with pytest.raises(Incompatible): api().SeedInventory(tmp_path/'db', cfg)


@pytest.mark.parametrize('mutation',['response','title','origin'])
def test_freeze_rejects_corrupt_listing_provenance(tmp_path,config,mutation):
    cfg=settings(config)
    with api().SeedInventory(tmp_path/'db',cfg) as store:
        store.step(Client([payload([{'pageid':1,'title':'A','ns':0,'type':'page'}])]),'people')
        if mutation=='response':store.db.execute("UPDATE responses SET record='{}'")
        elif mutation=='title':store.db.execute("UPDATE pages SET title='Changed'")
        else:store.db.execute('DELETE FROM origins')
        store.db.commit()
        with pytest.raises(Incompatible):store.freeze(config)


def test_cooldown_survives_restart_and_blocks_other_stream_without_probe(tmp_path,config):
    cfg=settings(config)
    class Failed:
        def query(self,*args,**kwargs):
            raise SourceUnavailable('lag',code='maxlag',retryable=True,retry_at='2100-01-01T00:00:00Z')
    with api().SeedInventory(tmp_path/'db',cfg) as store:
        with pytest.raises(SourceUnavailable):store.step(Failed(),'people')
        assert store.count()==0
    client=Client([])
    with api().SeedInventory(tmp_path/'db',cfg) as store:
        with pytest.raises(SourceUnavailable,match='cooldown'):store.step(client,'organizations')
    assert client.calls==[]


def test_exhausted_category_streams_use_namespace_fallback_without_inventing_strata(tmp_path,config):
    cfg=settings(config)
    with api().SeedInventory(tmp_path/'db',cfg) as store:
        for stream in cfg['roots']:store.step(Client([payload([])]),stream)
        client=Client([payload([{'pageid':10,'title':'Explored','ns':0}],key='allpages')])
        store.collect(client)
        result=store.freeze(config)
        assert len(client.calls)==1
        assert result['seeds'][0]['strata']==[]
        assert result['source']['unassigned']==1


def test_soft_quotas_exploration_and_plan_determinism(config):
    mod = import_module('vimfc.pipeline.seed_only')
    rows = []
    for n in range(1, 121):
        strata = list(config['quotas'])[n % 8:n % 8+1] if n <= 80 else []
        rows.append({'page_id': n, 'title': str(n), 'namespace': 0, 'strata': strata,
                     'candidate': {'page_id': n, 'title': str(n), 'discovery_metadata_only': True},
                     'origins': [{'kind': 'seed', 'stream': 'fixture'}]})
    inv = {'format': 'vimfc-discovery-inventory-1', 'snapshot': '2026-10-25T03:00:00Z',
           'semantic_rules': {k:digest(config[k]) for k in ('classification','extraction','ranking')},
           'seeds': rows, 'neighbors': []}
    inv['inventory_id'] = digest(inv)
    policy = mod.seed_policy(config['quotas'])
    first = mod.plan_seed_cohort(inv, policy, 100)
    assert first['slot_counts'] == {'seed': 100, 'neighbor': 0}
    assert len({r['page_id'] for r in first['candidates']}) == 100
    assert sum(r['sampling_stratum'] == 'exploration' for r in first['candidates']) >= 20
    assert set(policy['quotas']) == set(config['quotas'])
    inv['seeds'].reverse(); inv.pop('inventory_id'); inv['inventory_id'] = digest(inv)
    second = mod.plan_seed_cohort(inv, policy, 100)
    assert first['candidates'] == second['candidates']
    assert all('primary_entity_type' not in c for c in first['candidates'])
    from vimfc.pipeline.discovery_policy import verify_cohort
    verify_cohort(second, inv)
    broken = deepcopy(second); broken['candidates'][0]['title'] = 'tampered'
    with pytest.raises(Incompatible): verify_cohort(broken, inv)


def test_metadata_title_does_not_override_historical_page_identity(tmp_path, config):
    from tests.integration.test_manifest_provider import setup, Identities, Client as ProviderClient, SNAP
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.storage.pipeline_store import PipelineStore
    from vimfc.storage.raw_cache import RawCache
    with PipelineStore(tmp_path/'db') as store:
        cache = RawCache(tmp_path/'raw'); cfg = setup(store, cache, config)
        cfg['discovery'] = {'version': 'seed-only-2'}
        run = store.create(SNAP, cfg)
        provider = HistoricalProvider(store, run, ProviderClient(), cache, identities=Identities())
        candidate = {'page_id':1, 'title':'Current renamed title', 'discovery_metadata_only':True}
        raw = provider._raw(candidate)
        assert raw.ref.title == 'A'
        cfg['discovery']['version'] = 'stratified-lead-1'
        old = store.create(SNAP, cfg)
        legacy = HistoricalProvider(store, old, ProviderClient(), cache, identities=Identities())
        with pytest.raises(Incompatible): legacy._raw(candidate)
