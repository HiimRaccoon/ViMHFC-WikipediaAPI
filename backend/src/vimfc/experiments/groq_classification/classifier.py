"""Wikidata-first suggestions, finite free-only inference and immutable receipts."""
import json,random,time,uuid
from datetime import datetime,timezone
from vimfc.domain import GROUPS,digest
from .prompt import build_prompt,response_format,validate_prediction
from .provider import ProviderError
from .store import Pause

def validate_free_confirmation(confirmed):
 """An explicit user-confirmed flag authorizes Free-only experiment calls."""
 if confirmed is not True:raise Pause('LIVE_NOT_RUN_FREE_TIER_UNCONFIRMED')
 return {'format':'vimfc-groq-free-confirmation-1','confirmed':True,
  'basis':'explicit_user_confirmation','setting':'GROQ_FREE_TIER_CONFIRMED','provider_billing_attestation':False}

def request_key(row,profile,prompt,settings):
 return digest({'format':'groq-suggestion-key-1','page_id':row['evaluation']['page_id'],'input_fingerprint':row['input_fingerprint'],
  'model':profile['model_id'],'prompt_hash':prompt['prompt_hash'],'schema_hash':prompt['schema_hash'],
  'parameters':{**profile['request_kwargs'],'max_completion_tokens':settings['max_output_tokens'],'response_format':response_format(profile)}})

class GroqAssistedClassifier:
 def __init__(self,store,client,profile,rules,settings,*,clock=time.time,sleep=time.sleep,estimator=None):
  self.store,self.client,self.profile,self.rules,self.settings=store,client,profile,rules,settings
  self.clock,self.sleep=clock,sleep;self.invocation_id=str(uuid.uuid4());self.started=clock()
  self.start_summary=store.summary();self.free_proof=None
  self.estimator=estimator
 def _check_runtime(self):
  if self.clock()-self.started>=self.settings['max_runtime_seconds']:raise Pause('execution_runtime_budget')
 def _reserve(self,key,tokens,endpoint):
  while True:
   self._check_runtime()
   try:return self.store.reserve(key,tokens,self.profile['model_id'],endpoint,self.invocation_id)
   except Pause as e:
    wait=(e.retry_at-self.clock()) if e.retry_at is not None else None
    if (wait is None or wait<=0 or wait>self.settings['max_wait_seconds']
        or self.clock()+wait-self.started>=self.settings['max_runtime_seconds']):raise
    self.sleep(wait+0.001)
 def classify(self,row,*,mode='assisted'):
  if mode not in ('assisted','blind'):raise ValueError('Unsupported experiment mode')
  value=row['evaluation'];verified=value['baseline_outcome']=='completed' and value['baseline_primary'] in GROUPS
  if mode=='assisted' and verified:
   return {'page_id':value['page_id'],'input_fingerprint':row['input_fingerprint'],'model_id':self.profile['model_id'],
    'mode':mode,'status':'skipped_verified','prediction':None,'classification_state':'wikidata_verified_primary',
    'wikidata_verified_primary':value['baseline_primary'],'llm_suggested_primary':None,'human_reviewed_primary':None,'eligible_changed':False}
  if digest(row['input'])!=row['input_fingerprint']:raise ValueError('Input fingerprint changed')
  prompt=build_prompt(row,self.rules);key=request_key(row,self.profile,prompt,self.settings)
  cached=self.store.cached(key)
  if cached:
   if cached['input_fingerprint']!=row['input_fingerprint'] or cached['model_id']!=self.profile['model_id']:raise ValueError('Cached suggestion identity mismatch')
   checked=validate_prediction(cached['response_content'],row)
   if cached.get('finish_reason')!='stop':checked={'status':'invalid_schema','prediction':None}
   if checked['status']!=cached['status'] or checked['prediction']!=cached['prediction']:raise ValueError('Cached suggestion validation changed')
   return cached
  previous=self.store.successful_response(key)
  if previous:
   response=previous['response'];self.free_proof=response.get('free_tier_confirmation')
   return self._result(row,prompt,key,response,response.get('experiment_mode',mode),previous['attempt_id'],previous['invocation_id'])
  self.free_proof=validate_free_confirmation(self.settings.get('free_tier_confirmed'))
  if self.store.pending():raise Pause('uncertain_request_requires_review')
  payload={'model':self.profile['model_id'],'messages':prompt['messages'],**self.profile['request_kwargs'],
   'max_completion_tokens':self.settings['max_output_tokens'],'response_format':response_format(self.profile)}
  from .token_estimation import estimate_request
  estimate=(self.estimator or estimate_request)(payload)
  reservation=estimate['reserved_tokens']
  while True:
   attempt=self._reserve(key,reservation,'chat')
   try:response=self.client.complete(payload)
   except ProviderError as error:
    unknown=(error.status is None and error.code not in ('models_not_verified','payload_invalid','tools_forbidden','response_mode_unsupported','streaming_forbidden')) or error.code in ('invalid_response','model_response_mismatch')
    receipt={'code':error.code,'http_status':error.status,'retry_after_seconds':error.retry_after_seconds,'headers':error.headers,'response_hash':error.response_hash}
    self.store.finish(attempt,receipt,error=True,uncertain=unknown)
    self.store.headers(error.headers)
    if unknown:raise Pause('uncertain_request_requires_review') from None
    if error.status==429 or error.status is not None and error.status>=500:
     retry=max(error.retry_after_seconds or 0,min(60,2**self.store.attempts_for(key))*(1+random.random()/4))
     self.store.cooldown(retry,error.code)
     if self.store.attempts_for(key)<self.settings['max_attempts']:continue
    raise Pause(error.code) from None
   body=response['body'];usage=body.get('usage')
   usage_valid=(isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens','total_tokens'))
       and usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens'])
   response={**response,'experiment_mode':mode,'free_tier_confirmation':self.free_proof,'token_estimate':estimate}
   overrun=None
   try:self.store.finish(attempt,response,tokens=usage['total_tokens'] if usage_valid else None)
   except Pause as stop:overrun=stop
   self.store.headers(response['headers'])
   result=self._result(row,prompt,key,response,mode,attempt,self.invocation_id)
   if overrun:raise overrun
   if not usage_valid:raise Pause('usage_unavailable_stopped_after_checkpoint')
   return result
 def _result(self,row,prompt,key,response,mode,attempt,invocation):
   body=response['body'];value=row['evaluation'];verified=value['baseline_outcome']=='completed' and value['baseline_primary'] in GROUPS
   usage=body.get('usage');usage_valid=(isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens','total_tokens')) and usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens'])
   try:
    choice,=body['choices'];content=choice['message']['content'];finish=choice['finish_reason']
   except (KeyError,TypeError,ValueError):content='';finish='invalid'
   validated=validate_prediction(content,row)
   if finish!='stop':validated={'status':'invalid_schema','prediction':None,'validation_errors':['non_stop_or_missing_finish_reason']}
   prediction=validated['prediction'];suggested=prediction['predicted_group'] if prediction and not prediction['abstain'] else None
   result={'request_key':key,'page_id':value['page_id'],'input_fingerprint':row['input_fingerprint'],'mode':mode,
    'model_id':self.profile['model_id'],'model_backend_revision':body.get('system_fingerprint'),
    'backend_revision_pinned':False,'reproducibility':'Frozen inputs/prompt/config/responses; hosted inference is not bitwise reproducible',
    'prompt_version':prompt['prompt_version'],'prompt_hash':prompt['prompt_hash'],'schema_hash':prompt['schema_hash'],
    'parameters':{'model':self.profile['model_id'],**self.profile['request_kwargs'],'max_completion_tokens':self.settings['max_output_tokens'],'response_format':response_format(self.profile)},'request_message_hash':digest(prompt['messages']),
    'response':body,'response_content':content,'response_fingerprint':response['response_fingerprint'],
    'request_metadata':{'http_status':response['http_status'],'headers':response['headers'],'attempt_id':attempt,'invocation_id':invocation},'finish_reason':finish,
    'latency_seconds':response['elapsed_seconds'],'usage':usage if usage_valid else None,'token_estimate':response.get('token_estimate'),**validated,
    'classification_state':'llm_suggested_primary' if suggested else 'unresolved',
    'wikidata_verified_primary':value['baseline_primary'] if verified else None,'llm_suggested_primary':suggested,
    'human_reviewed_primary':None,'eligible_changed':False,'free_tier_confirmation':response.get('free_tier_confirmation')}
   self.store.save_result(key,result)
   return result

def human_reviewed_suggestion(result,label):
 """Future policy input only; leaves source verification and eligibility intact."""
 if (label.get('review_status')!='reviewed' or not label.get('reviewer') or not label.get('review_date')
     or label.get('input_fingerprint')!=result['input_fingerprint'] or label.get('page_id')!=result['page_id']
     or label.get('human_primary') not in GROUPS):raise ValueError('Explicit bound human review required')
 try:datetime.fromisoformat(label['review_date'].replace('Z','+00:00'))
 except (ValueError,TypeError,AttributeError):raise ValueError('Explicit valid review date required') from None
 return {**result,'human_reviewed_primary':label['human_primary'],'human_review':dict(label),'classification_state':'human_reviewed_primary','eligible_changed':False,
  'promotion_policy_version':None,'accepted_corpus_promotion':False}
