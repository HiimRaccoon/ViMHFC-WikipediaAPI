import copy,json,hashlib
from datetime import datetime,timezone
import pytest
from vimfc.domain import digest

def sample(verified=False):
 value={'title':'Bài A','lead_text':'Đây là một tổ chức khoa học.','sentences':[{'id':0,'text':'Đây là một tổ chức khoa học.'}]}
 return {'input':value,'input_fingerprint':digest(value),'evaluation':{'page_id':123,'baseline_outcome':'completed' if verified else 'unclassified','baseline_primary':'Person' if verified else None,'eligible':False}}
def settings(tmp):
 return {'free_tier_confirmed':True,'max_requests':10,'max_total_tokens':100000,'rpm':60,'rpd':100,'tpm':50000,'tpd':100000,'max_attempts':2,'max_wait_seconds':60,'max_runtime_seconds':600,'max_output_tokens':256}
class Client:
 def __init__(self,failures=None,content=None):self.calls=[];self.failures=list(failures or []);self.content=content
 def complete(self,payload):
  self.calls.append(copy.deepcopy(payload))
  if self.failures:raise self.failures.pop(0)
  value=self.content or json.dumps({'predicted_group':'Organization','abstain':False,'evidence_sentence_ids':[0],'reason':'Câu 0 mô tả tổ chức.','ambiguity':None})
  return {'body':{'id':'synthetic','model':payload['model'],'choices':[{'finish_reason':'stop','message':{'content':value}}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}},'headers':{},'http_status':200,'elapsed_seconds':0.1,'response_fingerprint':'fixture'}

def make(tmp_path,config,client,options=None):
 from vimfc.experiments.groq_classification.store import ExperimentStore
 from vimfc.experiments.groq_classification.classifier import GroqAssistedClassifier
 now=[1000.];cfg=options or settings(tmp_path);store=ExperimentStore(tmp_path/'ledger.sqlite',{'id':'scope'},cfg,clock=lambda:now[0])
 profile={'model_id':'openai/gpt-oss-20b','response_mode':'json_schema','request_kwargs':{'temperature':0}}
 classifier=GroqAssistedClassifier(store,client,profile,config['classification'],cfg,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),estimator=lambda p:{'reserved_tokens':500,'method':'synthetic-test-counter','actual_tokens':None})
 return classifier,store

def test_assisted_preserves_verified_primary_and_eligible_without_api(tmp_path,config):
 client=Client();classifier,store=make(tmp_path,config,client)
 try:
  original=sample(True);before=copy.deepcopy(original);r=classifier.classify(original)
  assert r['classification_state']=='wikidata_verified_primary' and r['wikidata_verified_primary']=='Person'
  assert not client.calls and original==before and r['llm_suggested_primary'] is None
 finally:store.__exit__()

def test_blind_prediction_cached_across_resume_no_duplicate_or_leak(tmp_path,config):
 client=Client();classifier,store=make(tmp_path,config,client)
 try:
  first=classifier.classify(sample(),mode='blind');second=classifier.classify(sample(),mode='blind')
  assert first==second and len(client.calls)==1
  assert first['llm_suggested_primary']=='Organization' and first['wikidata_verified_primary'] is None
  assert json.loads(client.calls[0]['messages'][1]['content'])==sample()['input']
  assert store.summary()['physical_attempts']==1
 finally:store.__exit__()

def test_invalid_evidence_is_terminal_not_retried(tmp_path,config):
 value=json.dumps({'predicted_group':'Organization','abstain':False,'evidence_sentence_ids':[999],'reason':'Câu sai','ambiguity':None})
 client=Client(content=value);classifier,store=make(tmp_path,config,client)
 try:
  assert classifier.classify(sample())['status']=='invalid_evidence'
  assert classifier.classify(sample())['status']=='invalid_evidence' and len(client.calls)==1
 finally:store.__exit__()

def test_429_retry_is_bounded_and_keeps_charged_requests(tmp_path,config):
 from vimfc.experiments.groq_classification.provider import ProviderError
 client=Client([ProviderError('rate_limited',status=429,retry_after_seconds=2)])
 classifier,store=make(tmp_path,config,client)
 try:
  assert classifier.classify(sample())['status']=='valid'
  assert len(client.calls)==2 and store.summary()['physical_attempts']==2
 finally:store.__exit__()

@pytest.mark.parametrize('delay,expected_call',[(2,True),(120,False)])
def test_provider_token_reset_waits_only_within_configured_budget(tmp_path,config,delay,expected_call):
 from vimfc.experiments.groq_classification.store import Pause
 client=Client();classifier,store=make(tmp_path,config,client)
 try:
  store.headers({'x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':str(delay)+'s'})
  if expected_call:assert classifier.classify(sample())['status']=='valid'
  else:
   with pytest.raises(Pause,match='provider_token_limit'):classifier.classify(sample())
  assert len(client.calls)==int(expected_call)
 finally:store.__exit__()

def test_unknown_network_outcome_never_automatically_duplicates(tmp_path,config):
 from vimfc.experiments.groq_classification.provider import ProviderError
 from vimfc.experiments.groq_classification.store import Pause
 client=Client([ProviderError('network_unavailable')]);classifier,store=make(tmp_path,config,client)
 try:
  with pytest.raises(Pause):classifier.classify(sample())
  with pytest.raises(Pause):classifier.classify(sample())
  assert len(client.calls)==1
 finally:store.__exit__()

def test_free_account_not_inferred_from_api_key_or_model(tmp_path,config):
 from vimfc.experiments.groq_classification.store import Pause
 cfg=settings(tmp_path);cfg['free_tier_confirmed']=False;client=Client();classifier,store=make(tmp_path,config,client,cfg)
 try:
  with pytest.raises(Pause,match='FREE_TIER_UNCONFIRMED'):classifier.classify(sample())
  assert not client.calls and store.summary()['physical_attempts']==0
 finally:store.__exit__()

def test_missing_free_proof_is_fail_closed():
 from vimfc.experiments.groq_classification.classifier import validate_free_confirmation
 from vimfc.experiments.groq_classification.store import Pause
 with pytest.raises(Pause):validate_free_confirmation(None)

def test_http_success_survives_crash_before_result_publication(tmp_path,config,monkeypatch):
 client=Client();classifier,store=make(tmp_path,config,client)
 original=store.save_result
 try:
  def failed(*a):raise OSError('simulated disk interruption')
  monkeypatch.setattr(store,'save_result',failed)
  with pytest.raises(OSError):classifier.classify(sample(),mode='blind')
  monkeypatch.setattr(store,'save_result',original)
  assert classifier.classify(sample(),mode='blind')['status']=='valid'
  assert len(client.calls)==1
 finally:store.__exit__()

def test_length_truncated_response_replays_as_invalid_without_second_request(tmp_path,config):
 class Truncated(Client):
  def complete(self,p):
   r=super().complete(p);r['body']['choices'][0]['finish_reason']='length';return r
 client=Truncated();classifier,store=make(tmp_path,config,client)
 try:
  assert classifier.classify(sample())['status']=='invalid_schema'
  assert classifier.classify(sample())['status']=='invalid_schema'
  assert len(client.calls)==1
 finally:store.__exit__()

def test_human_review_needs_valid_date_and_never_replaces_wikidata_verified():
 from vimfc.experiments.groq_classification.classifier import human_reviewed_suggestion
 source={'page_id':123,'input_fingerprint':'f','wikidata_verified_primary':'Person','llm_suggested_primary':'Organization','human_reviewed_primary':None}
 label={'page_id':123,'input_fingerprint':'f','review_status':'reviewed','reviewer':'reviewer','review_date':'not-a-date','human_primary':'Organization'}
 with pytest.raises(ValueError):human_reviewed_suggestion(source,label)
 label['review_date']='2026-10-29'
 result=human_reviewed_suggestion(source,label)
 assert result['wikidata_verified_primary']=='Person' and result['human_reviewed_primary']=='Organization'
 assert result['accepted_corpus_promotion'] is False and source['human_reviewed_primary'] is None
