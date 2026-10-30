from copy import deepcopy
import hashlib,json,sqlite3
from pathlib import Path
import pytest
from vimfc.domain import Incompatible,SourceRevisionRef,SourceUnavailable,canonical
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.source.revisions import RevisionSource
from vimfc.pipeline.reviewed_pilot import IntegrityStop,PilotGuard
from tests.integration.test_reviewed_quarantine import review_file,ref,SNAP

def registry(path,store,run,create=True):
 from vimfc.pipeline.integrity_registry import IntegrityRegistry
 return IntegrityRegistry(path,store,run,create=create)
def observation(reg,ref_value=None):
 ref_value=ref_value or ref()
 body=Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes()
 return reg.observe_mismatch(body,ref_value,{'original_failure':{'code':'source_hash_mismatch'},'request_cost':{'physical_attempts':1},'root_article':{'page_id':1},'dependency_path':None})
def auth(tmp):
 p=tmp/'authorization.txt';p.write_text('Explicit fixture reviewer authorization for exact refusal',encoding='utf-8');return p

def test_append_idempotence_restart_and_observation_determinism(tmp_path,config):
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   obs=observation(reg);assert observation(reg)==obs
   first=reg.audit();assert first['event_count']==1 and first['states']=={'UNKNOWN_INTEGRITY':1}
   assert store.db.execute("SELECT count(*) FROM run_events WHERE stage='integrity_registry_anchor'").fetchone()[0]==1
  with registry(tmp_path/'registry.sqlite',store,run,False) as restored:
   assert restored.audit()==first
   with pytest.raises(SourceUnavailable) as caught:restored.check(ref())
   assert caught.value.code=='source_hash_mismatch' and caught.value.details['registry_preflight'] is True
   with pytest.raises(IntegrityStop):PilotGuard(1,progress=False).source_failure(caught.value)

def test_review_refusal_requires_exact_external_approval_and_authorization(tmp_path,config):
 from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);approval,_=review_file(tmp_path,store.run(run))
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   obs=observation(reg)
   with pytest.raises(Incompatible):reg.review_refusal(obs,approval,tmp_path/'absent')
   reg.review_refusal(obs,approval,auth(tmp_path));gate=ReviewedIntegrityQuarantine.from_file(approval,store.run(run))
   reg.preflight(gate);assert reg.audit()['states']=={'REVIEWED_REFUSAL':1}
   with pytest.raises(Incompatible):reg.preflight(None)
   assert reg.audit()['event_count']==2
   reg.review_refusal(obs,approval,tmp_path/'authorization.txt');assert reg.audit()['event_count']==2
   assert not list((tmp_path/'raw').rglob('*.gz'))

@pytest.mark.parametrize('tamper',['evidence','event','delete_tail','unanchored_tail','missing_registry','authorization'])
def test_registry_artifact_anchor_and_history_tampering_fails_closed(tmp_path,config,tamper):
 from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);path=tmp_path/'registry.sqlite';approval,_=review_file(tmp_path,store.run(run))
  with registry(path,store,run) as reg:
   obs=observation(reg)
   if tamper=='authorization':
    authorization=auth(tmp_path);reg.review_refusal(obs,approval,authorization);authorization.write_text('changed',encoding='utf-8')
   elif tamper=='evidence':next((tmp_path/'registry-artifacts'/'responses').glob('*')).write_bytes(b'corrupt')
   elif tamper in ('event','delete_tail','unanchored_tail'):
    if tamper=='unanchored_tail':
     store.db.execute("DELETE FROM run_events WHERE stage='integrity_registry_anchor'")
    else:
     with pytest.raises(sqlite3.IntegrityError):reg.db.execute('DELETE FROM events')
     reg.db.execute('DROP TRIGGER immutable_event_update');reg.db.execute('DROP TRIGGER immutable_event_delete')
     reg.db.execute("UPDATE events SET record_json='{}'" if tamper=='event' else 'DELETE FROM events')
   elif tamper=='missing_registry':
    reg.close();path.unlink()
    with pytest.raises(Incompatible):registry(path,store,run,False)
    return
   with pytest.raises(Incompatible):reg.verify()

def test_forged_identity_conflict_and_unproven_recovery_rejected(tmp_path,config):
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   obs=observation(reg)
   wrong=SourceRevisionRef('wikidata','Q1',1,'Q1',23640705,ref().revision_timestamp,SNAP)
   with pytest.raises(Incompatible):observation(reg,wrong)
   with pytest.raises(Incompatible):reg.check(wrong)
   with pytest.raises(Incompatible):reg.review_recovery(obs,Path('tests/fixtures/wikidata-23640705-unverified-response.json'),auth(tmp_path))
   assert reg.audit()['event_count']==1

def test_unknown_known_ref_prevents_network_cache_and_worker_claim(tmp_path,config):
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   observation(reg)
   class Forbidden:
    def query(self,*a,**k):raise AssertionError('No HTTP')
    def find(self,*a,**k):raise AssertionError('No cache read')
   source=RevisionSource(Forbidden(),store,Forbidden());source.integrity_registry=reg
   with pytest.raises(SourceUnavailable):source.fetch_revisions('wikidata',[ref()])
   with pytest.raises(SourceUnavailable):reg.preflight(None)
   assert store.db.execute('SELECT count(*) FROM stage_results').fetchone()[0]==0

def test_non_integrity_states_are_distinct_and_not_dispositions(tmp_path,config):
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   for state in ('SOURCE_UNAVAILABLE','SEMANTIC_UNRESOLVED','MISSING_HISTORICAL_DEPENDENCY'):
    reg.observe_status(state,{'code':state,'root_article':{'page_id':9},'source_ref':None})
   reg.preflight(None)
   with pytest.raises(Incompatible):reg.observe_status('REVIEWED_REFUSAL',{})
   with pytest.raises(Incompatible):reg.observe_status('VERIFIED_RECOVERY',{})
   assert reg.audit()['states']=={s:1 for s in ('SOURCE_UNAVAILABLE','SEMANTIC_UNRESOLVED','MISSING_HISTORICAL_DEPENDENCY')}

def fake_runtime(monkeypatch,payload):
 import httpx
 import vimfc.pipeline.reviewed_pilot as pilot
 from vimfc.source.mediawiki import MediaWikiClient
 calls=[]
 def respond(request):calls.append(request);return httpx.Response(200,json=payload)
 def client(*a,**k):return MediaWikiClient('fixture@example.test',*a[1:],**k,transport=httpx.MockTransport(respond))
 class Provider:
  def __init__(self,store,run,client,cache,**extras):
   self.source=RevisionSource(client,store,cache);self.source.integrity_registry=extras.get('integrity_registry')
   self.source.integrity_registry.set_root({'page_id':17,'title':'fixture root'})
 class Builder:
  def __init__(self,store,run,provider,**kw):self.provider=provider
  def pilot(self,size):return self.provider.source.fetch_revisions('wikidata',[ref()])
 monkeypatch.setattr(pilot,'MediaWikiClient',client);monkeypatch.setattr(pilot,'HistoricalProvider',Provider);monkeypatch.setattr(pilot,'Builder',Builder)
 return pilot,calls,Builder

def cohort(store,run):
 for i in range(1000):store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,i+1,i,canonical({'page_id':i+1,'title':str(i+1)})))

@pytest.mark.parametrize('capture_fails',[False,True])
def test_new_unknown_capture_keeps_original_stop_and_restart_does_not_fetch(tmp_path,config,monkeypatch,capture_fails):
 body=json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes())
 pilot,calls,_=fake_runtime(monkeypatch,body)
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);cohort(store,run)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   if capture_fails:monkeypatch.setattr(reg,'capture_error',lambda *a,**k:(_ for _ in ()).throw(OSError('diagnostic disk full')))
   result=pilot.run_reviewed(store,run,tmp_path/'raw',100,max_requests=3,reports=tmp_path/'reports',integrity_registry=reg)
   assert result['exit_code']==3 and result['failure']['source_code']=='source_hash_mismatch' and len(calls)==1
   assert not list((tmp_path/'raw').rglob('*.gz'))
   if capture_fails:assert 'integrity_registry_error' in result['failure']['details']
   else:
    assert reg.audit()['states']=={'UNKNOWN_INTEGRITY':1}
    with pytest.raises(IntegrityStop):pilot.run_reviewed(store,run,tmp_path/'raw',100,max_requests=3,reports=tmp_path/'reports',integrity_registry=reg)
    assert len(calls)==1

def test_diagnostic_write_failure_cannot_hide_original_non_hash_source_error(tmp_path,config,monkeypatch):
 pilot,calls,Builder=fake_runtime(monkeypatch,{'query':{'pages':[{'missing':True}]}})
 monkeypatch.setattr(Builder,'pilot',lambda self,size:self.provider.source.select_revision('wikidata','Q1',SNAP))
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);cohort(store,run)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   monkeypatch.setattr(reg,'observe_status',lambda *a,**k:(_ for _ in ()).throw(OSError('full')))
   result=pilot.run_reviewed(store,run,tmp_path/'raw',100,max_requests=3,reports=tmp_path/'reports',integrity_registry=reg)
   assert result['failure']['type']=='Incompatible'
   assert 'No historical revision' in result['failure']['message'] and len(calls)==1

def test_provider_rejects_foreign_registry_even_when_it_is_empty(tmp_path,config):
 from vimfc.pipeline.provider import HistoricalProvider
 from types import SimpleNamespace
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);other=store.create(SNAP,config)
  with registry(tmp_path/'first.sqlite',store,run) as first,registry(tmp_path/'other.sqlite',store,other) as foreign:
   observation(first)
   with pytest.raises(Incompatible):HistoricalProvider(store,run,SimpleNamespace(),RawCache(tmp_path/'raw'),integrity_registry=foreign)

@pytest.mark.parametrize('mutation',['missing_slot_sha1','numeric_alias'])
def test_unparseable_or_aliased_new_mismatch_still_records_unknown(tmp_path,config,monkeypatch,mutation):
 payload=json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes())
 if mutation=='missing_slot_sha1':payload['query']['pages'][0]['revisions'][0]['slots']['main'].pop('sha1')
 pilot,calls,Builder=fake_runtime(monkeypatch,payload)
 if mutation=='numeric_alias':
  r=ref();alias=SourceRevisionRef(r.site,str(r.page_id),r.page_id,r.title,r.revision_id,r.revision_timestamp,r.snapshot)
  monkeypatch.setattr(Builder,'pilot',lambda self,size:self.provider.source.fetch_revisions('wikidata',[alias]))
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);cohort(store,run)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   result=pilot.run_reviewed(store,run,tmp_path/'raw',100,max_requests=3,reports=tmp_path/'reports',integrity_registry=reg)
   assert result['failure']['source_code']=='source_hash_mismatch' and reg.audit()['states']=={'UNKNOWN_INTEGRITY':1}
   with pytest.raises(SourceUnavailable):reg.preflight(None)
   assert len(calls)==1 and not list((tmp_path/'raw').rglob('*.gz'))

def test_cache_only_manifest_blocks_known_dependency_before_reading_cache(tmp_path,config):
 from vimfc.pipeline.dependencies import make_manifest
 from vimfc.source.dependencies import DependencySource
 cfg=deepcopy(config['classification']);r=ref()
 body=json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes())['query']['pages'][0]['revisions'][0]['slots']['main']['content']
 item={'entity':json.loads(body),'revision_id':r.revision_id,'revision_timestamp':r.revision_timestamp,'page_id':r.page_id,'content_model':'wikibase-item','raw_hash':hashlib.sha256(body.encode()).hexdigest()}
 article={'site':'viwiki','page_id':17,'title':'fixture root','revision_id':1,'revision_timestamp':'2010-01-01T00:00:00Z','content_hash':'a'*64}
 manifest=make_manifest(article,r.identity,lambda q:item,cfg,SNAP)
 class Forbidden:
  def get(self,*a):raise AssertionError('Unknown dependency must not read cache')
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   observation(reg);source=RevisionSource(None,store,Forbidden());source.integrity_registry=reg
   with pytest.raises(SourceUnavailable) as caught:DependencySource(source,SNAP,cfg).materialize(manifest,allow_fetch=False)
   assert caught.value.details['registry_preflight'] is True

def test_reviewed_shared_dependency_never_publishes_and_completed_stage_is_reused(tmp_path,config):
 from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine,QuarantinedIntegrity
 from vimfc.pipeline.runner import StageExecutor
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run));gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run))
  StageExecutor(store,run).execute('process',55,{'input':'unchanged'},lambda:{'fixture':'completed'})
  before=dict(store.db.execute("SELECT * FROM stage_results WHERE identity='55'").fetchone())
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   obs=observation(reg);reg.review_refusal(obs,path,auth(tmp_path));gate.bind(store)
   reg.set_root({'page_id':99,'title':'different root requiring this dependency'})
   source=RevisionSource(None,store,RawCache(tmp_path/'raw'));source.integrity_registry=reg;source.reviewed_quarantine=gate
   with pytest.raises(QuarantinedIntegrity):source.fetch_revisions('wikidata',[ref()])
   StageExecutor(store,run).execute('process',55,{'input':'unchanged'},lambda:pytest.fail('Completed worker must not repeat'))
   assert dict(store.db.execute("SELECT * FROM stage_results WHERE identity='55'").fetchone())==before
   assert not list((tmp_path/'raw').rglob('*.gz')) and store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0

def test_verified_recovery_requires_original_hash_and_still_does_not_publish_raw(tmp_path,config):
 payload=json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes());rev=payload['query']['pages'][0]['revisions'][0];slot=rev['slots']['main']
 original=json.dumps(json.loads(slot['content']),ensure_ascii=False,separators=(',',':'))
 expected=hashlib.sha1(original.encode()).hexdigest();slot['sha1']=rev['sha1']=expected
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   obs=reg.observe_mismatch(json.dumps(payload).encode(),ref(),{'fixture':'synthetic recovery mechanism, not real source recovery'})
   slot['content']=original;proof=tmp_path/'synthetic-original.json';proof.write_text(json.dumps(payload),encoding='utf-8')
   reg.review_recovery(obs,proof,auth(tmp_path));reg.preflight(None)
   assert reg.audit()['states']=={'VERIFIED_RECOVERY':1} and not reg.audit()['trusted_evidence_published']
   assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==0

@pytest.mark.parametrize('stale',['matching_body','different_mismatch_body'])
def test_stale_response_is_opaque_unknown_not_false_exact_failure_proof(tmp_path,config,stale):
 original=json.loads(Path('tests/fixtures/wikidata-23640705-unverified-response.json').read_bytes());rev=original['query']['pages'][0]['revisions'][0];slot=rev['slots']['main'];r=ref()
 details={'site':r.site,'identity':r.identity,'page_id':r.page_id,'revision_id':r.revision_id,'revision_timestamp':r.revision_timestamp,'snapshot':r.snapshot,
  'reported_sha1':rev['sha1'],'computed_sha1':hashlib.sha1(slot['content'].encode()).hexdigest(),'slot_sha1':slot['sha1']}
 if stale=='matching_body':rev['sha1']=slot['sha1']=details['computed_sha1']
 else:slot['content']+=' '
 with PipelineStore(tmp_path/'db') as store:
  run=store.create(SNAP,config)
  with registry(tmp_path/'registry.sqlite',store,run) as reg:
   reg.last_response={'body':json.dumps(original).encode(),'http':{'fixture':'stale'}}
   reg.capture_error(SourceUnavailable('Source SHA-1 mismatch',code='source_hash_mismatch',details=details),invocation='fixture',charged_attempts=7,cap=10)
   audit=reg.audit();assert audit['states']=={'UNKNOWN_INTEGRITY':1}
   assert next(iter(audit['observations'].values()))['kind']=='status'
