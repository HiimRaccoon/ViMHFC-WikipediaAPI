"""Captured revision response is untrusted test input, never an accepted raw artifact."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from vimfc.domain import SourceRevisionRef, SourceUnavailable
from vimfc.pipeline.reviewed_pilot import IntegrityStop, PilotGuard
from vimfc.pipeline.runner import StageExecutor
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAPSHOT='2026-10-25T03:00:00Z'
REPORTED='b12e3d961393f3c96a98b718a779a7038ca7d581'
COMPUTED='9f51e2f1a242039ae7cd2786e790f487a133f40f'


def captured():
    return json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_text(encoding='utf-8'))


def revision(payload):
    return payload['query']['pages'][0]['revisions'][0]


def reference():
    return SourceRevisionRef('wikidata','Q10761835',12035838,'Q10761835',23640705,'2013-04-09T03:15:10Z',SNAPSHOT)


def test_captured_mismatch_preserves_both_hashes_format_and_bytes_without_publishing(tmp_path):
    payload=captured();requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json=payload)
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as client:
        cache=RawCache(tmp_path/'raw')
        source=RevisionSource(client,store,cache)
        with pytest.raises(SourceUnavailable) as failure:
            source.fetch_revisions('wikidata',[reference()])
        error=failure.value
        assert error.code=='source_hash_mismatch' and not error.retryable
        assert error.details['reported_sha1']==error.details['slot_sha1']==REPORTED
        assert error.details['computed_sha1']==COMPUTED
        assert error.details['content_sha256']=='66b3d9cad6e4afaa054840f3448a5b3dedbea30aa53fece6f88aa2f8bb9844f8'
        assert error.details['utf8_length']==442
        assert error.details['content_format']=='application/json'
        assert error.details['revision_sha1_matches_content'] is False
        assert error.details['slot_sha1_matches_content'] is False
        assert error.details['hash_validation_basis']=='revision_sha1'
        assert 'slotsha1' in requests[0].url.params['rvprop'].split('|')
        assert requests[0].url.params['maxlag']=='5'
        assert not list(cache.root.rglob('*.gz'))
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0


def test_matching_slot_does_not_override_mismatching_revision_hash(tmp_path):
    payload=captured();revision(payload)['slots']['main']['sha1']=COMPUTED
    class Client:
        def query(self,*args):return payload
    with PipelineStore(tmp_path/'db') as store:
        source=RevisionSource(Client(),store,RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable) as failure:source.fetch_revisions('wikidata',[reference()])
        assert failure.value.details['slot_sha1_matches_content'] is True
        assert failure.value.details['revision_sha1_matches_content'] is False
        assert not list((tmp_path/'raw').rglob('*.gz'))


@pytest.mark.parametrize('conversion',['pretty_json','unicode_unescaped','trailing_lf'])
def test_json_equivalence_or_encoding_conversion_is_never_a_hash_bypass(tmp_path,conversion):
    payload=captured();rev=revision(payload);content=rev['slots']['main']['content']
    if conversion=='pretty_json':alternative=json.dumps(json.loads(content),ensure_ascii=True,indent=2)
    elif conversion=='unicode_unescaped':alternative=json.dumps(json.loads(content),ensure_ascii=False,separators=(',',':'))
    else:alternative=content+'\n'
    rev['sha1']=hashlib.sha1(alternative.encode('utf-8')).hexdigest()
    class Client:
        def query(self,*args):return payload
    with PipelineStore(tmp_path/'db') as store:
        source=RevisionSource(Client(),store,RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable) as failure:source.fetch_revisions('wikidata',[reference()])
        assert failure.value.details['computed_sha1']==COMPUTED
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0


def test_valid_non_ascii_utf8_is_accepted_exactly_without_normalization(tmp_path):
    payload=captured();rev=revision(payload)
    content=json.dumps(json.loads(rev['slots']['main']['content']),ensure_ascii=False,separators=(',',':'))
    expected=hashlib.sha1(content.encode('utf-8')).hexdigest()
    rev['sha1']=expected;rev['slots']['main'].update(content=content,sha1=expected)
    class Client:
        def query(self,*args):return payload
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');source=RevisionSource(Client(),store,cache)
        result=source.fetch_revisions('wikidata',[reference()])[0]
        assert result.content==content and result.source_sha1==expected
        assert cache.get(result.cache_ref)['content'].encode('utf-8')==content.encode('utf-8')


def test_interrupted_stage_resumes_same_key_only_after_fixture_input_becomes_verifiable(tmp_path):
    """Synthetic recovery tests mechanics; it does NOT claim the real revision recovered."""
    payload=captured();calls=[]
    class Client:
        def query(self,*args):calls.append(args);return deepcopy(payload)
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAPSHOT,{'version':'fixture'})
        executor=StageExecutor(store,run)
        preserved=executor.execute('process','99',{'fixture':'finished'},lambda:{'status':'completed'})
        before=dict(store.db.execute("SELECT * FROM stage_results WHERE identity='99'").fetchone())
        guard=PilotGuard(10,progress=False)
        source=RevisionSource(Client(),store,RawCache(tmp_path/'raw'),integrity_guard=guard.source_failure)
        def work():
            raw=source.fetch_revisions('wikidata',[reference()])[0]
            return {'content_sha256':raw.local_sha256}
        dependencies={'fixture_ref':23640705}
        with pytest.raises(IntegrityStop):executor.execute('acquire_manifest','421635',dependencies,work)
        interrupted=dict(store.db.execute("SELECT * FROM stage_results WHERE identity='421635'").fetchone())
        assert interrupted['status']=='pending' and interrupted['lease_until'] is None
        assert interrupted['last_error']=='interrupted' and interrupted['result_json'] is None
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0
        # Change only the isolated mock source. Never change the real metadata.
        revision(payload)['sha1']=COMPUTED
        revision(payload)['slots']['main']['sha1']=COMPUTED
        actual=StageExecutor(store,run).execute('acquire_manifest','421635',dependencies,work)
        assert actual['content_sha256']=='66b3d9cad6e4afaa054840f3448a5b3dedbea30aa53fece6f88aa2f8bb9844f8'
        def forbidden():raise AssertionError('Completed checkpoint ran twice')
        assert StageExecutor(store,run).execute('acquire_manifest','421635',dependencies,forbidden)==actual
        assert len(calls)==2
        final=dict(store.db.execute("SELECT * FROM stage_results WHERE identity='421635'").fetchone())
        assert final['dependency_hash']==interrupted['dependency_hash'] and final['attempt_count']==2
        assert final['status']=='completed'
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE identity='99'").fetchone())==before
