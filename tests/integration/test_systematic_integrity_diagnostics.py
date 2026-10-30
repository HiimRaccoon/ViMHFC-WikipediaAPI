"""Offline observations never grant source trust or modify live checkpoints."""
from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from vimfc.domain import SourceRevisionRef,SourceUnavailable
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAP='2026-10-25T03:00:00Z'
CASES=[('Q10761835',12035838,23640705,'2013-04-09T03:15:10Z',442,'9f51e2f1a242039ae7cd2786e790f487a133f40f'),
 ('Q10841261',12113859,24242903,'2013-04-10T19:39:10Z',252,'571cb4511be866828e14c1b717e45bd2cae72264'),
 ('Q10766570',12040430,23658997,'2013-04-09T05:01:00Z',267,'2974f13a61b34bfe8cb0f4c1c1f0103b77c693ec')]
CAPTURED_BODY_HASHES={23640705:'dc77ab36004986a245c601bb480c493760e9ca511a3caaabb0e675e81d15939b',
 24242903:'391b23a6802ac295fea4421ea5b909deb8279acb7bc4fc51befee769b10a2e0c',
 23658997:'ceb2a3a36f27084e4513d1a9487da5a0d8c2125a81a685c84ac43e4c45eab97f'}
def captured(case):return Path(f'tests/fixtures/wikidata-{case[2]}-unverified-response.json').read_bytes()
def reference(case):return SourceRevisionRef('wikidata',case[0],case[1],case[0],case[2],case[3],SNAP)
def inspect(body,ref,**kw):
 from vimfc.source.integrity_diagnostics import inspect_revision_response
 return inspect_revision_response(body,ref,expected_response_sha256=kw.pop('sha',hashlib.sha256(body).hexdigest()),**kw)

@pytest.mark.parametrize('case',CASES)
def test_all_three_exact_responses_are_untrusted_mismatch_observations(case):
 body=captured(case);r=inspect(body,reference(case),sha=CAPTURED_BODY_HASHES[case[2]])
 assert r['format']=='vimfc-integrity-diagnostic-1'
 assert r['status']=='source_representation_mismatch' and not r['trusted_historical_evidence']
 assert r['content_sha1']==case[5] and r['utf8_length']==case[4]
 assert not r['revision_sha1_matches_content'] and not r['slot_sha1_matches_content']
 assert r['reported_revision_size'] in (None,236) # Old captured fixtures omit size.
 assert r['source_validation_semantics_changed'] is False

@pytest.mark.parametrize('case',CASES)
def test_all_captured_cases_still_fail_existing_source_validation_without_publication(tmp_path,case):
 payload=json.loads(captured(case))
 class Client:
  def query(self,*a,**k):return deepcopy(payload)
 with PipelineStore(tmp_path/'db') as store:
  cache=RawCache(tmp_path/'raw');source=RevisionSource(Client(),store,cache)
  with pytest.raises(SourceUnavailable) as caught:source.fetch_revisions('wikidata',[reference(case)])
  assert caught.value.code=='source_hash_mismatch' and caught.value.details['computed_sha1']==case[5]
  assert not list(cache.root.rglob('*.gz')) and store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0

@pytest.mark.parametrize('mutation',['response_hash','revision','qid','timestamp','future','fractional_future','content_identity','duplicate_json','api_error'])
def test_ambiguous_wrong_scope_tampered_or_unavailable_inputs_refused(mutation):
 case=CASES[2];body=captured(case);p=json.loads(body);ref=reference(case);kw={}
 if mutation=='response_hash':kw['sha']='0'*64
 elif mutation=='revision':p['query']['pages'][0]['revisions'][0]['revid']+=1
 elif mutation=='qid':p['query']['pages'][0]['title']='Q1'
 elif mutation=='timestamp':p['query']['pages'][0]['revisions'][0]['timestamp']='2013-04-09T05:01:01Z'
 elif mutation=='future':ref=SourceRevisionRef('wikidata',case[0],case[1],case[0],case[2],case[3],'2013-04-01T00:00:00Z')
 elif mutation=='fractional_future':
  stamp='2013-04-09T05:01:00.500000Z'
  p['query']['pages'][0]['revisions'][0]['timestamp']=stamp
  ref=SourceRevisionRef('wikidata',case[0],case[1],case[0],case[2],stamp,case[3])
 elif mutation=='content_identity':p['query']['pages'][0]['revisions'][0]['slots']['main']['content']='{"id":"Q1","type":"item"}'
 elif mutation=='duplicate_json':body=b'{"query":{},"query":{}}'
 else:p={'error':{'code':'maxlag','info':'test'}}
 if mutation not in ('response_hash','future','duplicate_json'):body=json.dumps(p).encode()
 with pytest.raises(ValueError):inspect(body,ref,**kw)

def test_matching_unicode_control_is_observation_not_an_acceptance_path(tmp_path):
 case=CASES[2];p=json.loads(captured(case));rev=p['query']['pages'][0]['revisions'][0];slot=rev['slots']['main']
 content=json.dumps(json.loads(slot['content']),ensure_ascii=False,separators=(',',':'))
 slot['content']=content;slot['sha1']=rev['sha1']=hashlib.sha1(content.encode()).hexdigest()
 body=json.dumps(p,ensure_ascii=False).encode();r=inspect(body,reference(case))
 assert r['status']=='metadata_hashes_match' and r['slot_sha1_matches_content'] and not r['trusted_historical_evidence']
 class Client:
  def query(self,*a,**k):return deepcopy(p)
 with PipelineStore(tmp_path/'db') as store:
  raw=RevisionSource(Client(),store,RawCache(tmp_path/'raw')).fetch_revisions('wikidata',[reference(case)])[0]
  assert raw.content==content # Synthetic control, NOT proof the real source recovered.

def test_cli_is_offline_exact_and_never_overwrites_input_or_existing_output(tmp_path):
 case=CASES[2];source=Path(f'tests/fixtures/wikidata-{case[2]}-unverified-response.json').resolve();before=source.read_bytes();output=tmp_path/'audit.json'
 argv=[sys.executable,'-B','scripts/inspect-wikidata-integrity.py','--response',str(source),'--response-sha256',hashlib.sha256(before).hexdigest(),
  '--qid',case[0],'--page-id',str(case[1]),'--revision-id',str(case[2]),'--timestamp',case[3],'--snapshot',SNAP,'--output',str(output)]
 result=subprocess.run(argv,capture_output=True,text=True);assert result.returncode==0,result.stderr
 saved=output.read_bytes();assert json.loads(saved)['status']=='source_representation_mismatch'
 result=subprocess.run(argv,capture_output=True,text=True);assert result.returncode!=0 and output.read_bytes()==saved
 assert source.read_bytes()==before
