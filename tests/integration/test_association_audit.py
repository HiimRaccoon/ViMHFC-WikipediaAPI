"""Historical sitelinks are audit evidence, never guesses from labels/pageprops."""
from dataclasses import asdict
import hashlib,json
from importlib import import_module
from pathlib import Path

import pytest
from vimfc.domain import SourceRevisionRef,canonical
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAP='2026-10-25T03:00:00Z'

def seed(store,cache,qid,title,*,timestamp='2026-10-24T00:00:00Z',bad_hash=False):
    rev=int(qid[1:])+100
    body=canonical({'id':qid,'claims':{},'labels':{'vi':{'value':'Misleading label'}},'sitelinks':{'viwiki':{'title':title}}})
    ref=SourceRevisionRef('wikidata',qid,rev,qid,rev,timestamp,SNAP)
    raw={'page_id':rev,'timestamp':timestamp,'content_model':'wikibase-item','content':body,'source_sha1':'0'*40 if bad_hash else hashlib.sha1(body.encode()).hexdigest(),'local_sha256':hashlib.sha256(body.encode()).hexdigest()}
    cache_ref=cache.put('wikidata',rev,raw)
    store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',('wikidata',qid,SNAP,canonical(ref)))
    store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)',('wikidata',rev,canonical({'cache_ref':cache_ref})))

def test_unique_selected_historical_sitelink_is_not_a_global_uniqueness_proof(tmp_path):
    api=import_module('vimfc.source.association_audit')
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');seed(store,cache,'Q1','Historical title')
        result=api.audit_associations(store.db,cache,SNAP,[{'page_id':9,'title':'Historical_title'}])
        article=result['articles'][0]
        assert article['status']=='historical_match_needs_review'
        assert article['matches'][0]['qid']=='Q1'
        assert not article['safe_to_pin_without_review']
        assert result['coverage']=='selected_archived_inventory_only'
        assert result['physical_api_requests']==0

@pytest.mark.parametrize('mode',['ambiguous','missing','future','hash'])
def test_partial_or_invalid_inventory_never_manufactures_qid(tmp_path,mode):
    api=import_module('vimfc.source.association_audit')
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw')
        if mode=='ambiguous':
            seed(store,cache,'Q1','A');seed(store,cache,'Q2','A')
        elif mode=='future':seed(store,cache,'Q1','A',timestamp='2026-10-26T00:00:00Z')
        elif mode=='hash':seed(store,cache,'Q1','A',bad_hash=True)
        result=api.audit_associations(store.db,cache,SNAP,[{'page_id':9,'title':'A'}])
        assert result['articles'][0]['status']==('ambiguous_historical_matches' if mode=='ambiguous' else 'not_found_in_archived_inventory')
        assert not result['articles'][0]['safe_to_pin_without_review']
        assert len(result['invalid_entries'])==int(mode in ('future','hash'))

def test_negative_hints_stop_at_review_gate_without_repeated_pageprops(tmp_path,config):
    from tests.integration.test_manifest_provider import Client,Identities
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.domain import SourceUnavailable
    from datetime import datetime,timezone,timedelta
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);client=Client();calls=[]
        def query(*args):calls.append(args);raise AssertionError('Exhausted hints must not query source')
        client.query=query
        provider=HistoricalProvider(store,run,client,RawCache(tmp_path/'raw'),identities=Identities())
        for _ in range(2):store.event(run,'stage_deferred',{'identity':'9','stage':'item_hint_refresh','code':'missing_item_hint'})
        raw=type('Raw',(),{'ref':SourceRevisionRef('viwiki','9',9,'A',12,'2026-10-24T00:00:00Z',SNAP),'local_sha256':'a'*64})()
        with pytest.raises(SourceUnavailable) as error:provider._pinned_qid(raw)
        assert error.value.code=='association_review_required' and not error.value.retryable
        assert calls==[]
