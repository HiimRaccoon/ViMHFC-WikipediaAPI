from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from pathlib import Path
import json
import pytest
from vimfc.domain import canonical,digest,Incompatible
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.pipeline.runner import Builder
from tests.integration.test_manifest_provider import setup,Client,Identities,SNAP

def fixture_archive(tmp_path,config):
    database=tmp_path/'db';raw=tmp_path/'raw'
    with PipelineStore(database) as store:
        cache=RawCache(raw);cfg=setup(store,cache,config);run=store.create(SNAP,cfg)
        candidate={'page_id':1,'title':'A','discovery_origin':'people'}
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,canonical(candidate)))
        class CachedIdentities(Identities):
            def raw_page(self,page_id):
                raw=super().raw_page(page_id)
                ref=cache.put('viwiki',raw.ref.revision_id,{'page_id':raw.ref.page_id,'timestamp':raw.ref.revision_timestamp,
                    'content':raw.content,'content_model':raw.content_model,'source_sha1':raw.source_sha1,'local_sha256':raw.local_sha256})
                return replace(raw,cache_ref=ref)
        Builder(store,run,HistoricalProvider(store,run,Client(),cache,identities=CachedIdentities()),reports=tmp_path/'reports')._process(1,False)
    return {'database':str(database.resolve()),'raw_cache':str(raw.resolve()),'run_id':run,'snapshot':SNAP,'config_hash':digest(cfg),'cohort_hash':digest([candidate])}

def test_readonly_inventory_verifies_saved_records_and_refuses_corruption(tmp_path,config):
    api=import_module('vimfc.pipeline.discovery_archive');context=fixture_archive(tmp_path,config)
    with api.Archive(context) as archive:
        records=archive.records();inventory=archive.inventory(records)
        assert list(records)==[1] and inventory['seeds'][0]['strata']==['Person']
        assert inventory['source']['cohort_hash']==context['cohort_hash']
        with pytest.raises(Exception):archive.db.execute("DELETE FROM candidates")
    cache=RawCache(context['raw_cache']);path=next((cache.root/'viwiki').rglob('*.json.gz'));path.write_bytes(b'bad')
    with api.Archive(context) as archive:
        with pytest.raises(Incompatible):archive.records()

def test_new_discovery_identity_does_not_implicitly_clone_old_candidates(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,canonical({'page_id':1,'title':'A'})))
        changed=deepcopy(config);changed['discovery']={'version':'stratified-lead-1','manifest_id':'a'*64}
        new=store.create(SNAP,changed,reuse_run=run)
        assert store.candidates(new)==[]
        assert store.candidates(run)==[{'page_id':1,'title':'A'}]

def test_unknown_or_redirected_namespace_is_not_certified_from_sampling_origin(config):
    api=import_module('vimfc.pipeline.discovery_archive')
    archive=api.Archive.__new__(api.Archive)
    archive.config=config;archive.context={'snapshot':'2026-10-25T03:00:00Z','run_id':'fixture','config_hash':digest(config),'cohort_hash':'a'*64}
    archive.cohort=[{'page_id':1,'title':'Category:Not an article','discovery_origin':'people'}]
    inv=archive.inventory({})
    assert inv['seeds'][0]['namespace'] is None
    assert inv['seeds'][0]['candidate']['discovery_namespace_status']=='unverified'
