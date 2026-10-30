import json
from pathlib import Path
import pytest


def test_free_defaults_match_qwen_published_limits():
 cfg=json.loads(Path('configs/groq_classification.json').read_text(encoding='utf-8'))
 assert {k:cfg[k] for k in ('rpm','rpd','tpm','tpd')}=={'rpm':30,'rpd':1000,'tpm':8000,'tpd':200000}


def test_tokenizer_counts_chat_schema_output_separately_without_utf8_proxy():
 from vimfc.experiments.groq_classification.token_estimation import estimate_components
 payload={'model':'qwen/qwen3.8-27b','messages':[{'role':'user','content':'Tiếng Việt '*200}],
  'max_completion_tokens':256,'response_format':{'type':'json_schema','json_schema':{'name':'x','strict':True,'schema':{'type':'object'}}}}
 count=lambda s:len(s.split())
 result=estimate_components(payload,chat_text='<|im_start|>user\n'+payload['messages'][0]['content']+'<|im_end|>\n<|im_start|>assistant\n',count=count)
 assert result['estimated_prompt_tokens']==result['chat_template_tokens']+result['schema_tokens']
 assert result['reserved_tokens']==result['estimated_prompt_tokens']+256
 assert result['schema_tokens']>0 and result['actual_tokens'] is None
 assert result['reserved_tokens']<len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))


def test_missing_or_tampered_tokenizer_is_fail_closed(tmp_path):
 from vimfc.experiments.groq_classification.token_estimation import QwenTokenEstimator
 from vimfc.experiments.groq_classification.store import Pause
 with pytest.raises(Pause,match='tokenizer'):QwenTokenEstimator(tmp_path/'manifest.json')
 (tmp_path/'manifest.json').write_text(json.dumps({'model_id':'qwen/qwen3.8-27b','revision':'forged','files':{}}),encoding='utf-8')
 with pytest.raises(Pause,match='tokenizer'):QwenTokenEstimator(tmp_path/'manifest.json')


def test_current_provider_lower_limit_overrides_defaults_and_oversize_is_not_waitable(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 caps={'max_requests':12,'max_total_tokens':40000,'rpm':30,'rpd':1000,'tpm':8000,'tpd':200000,'max_attempts':2}
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'test'},caps,clock=lambda:1000.) as s:
  s.headers({'x-ratelimit-limit-tokens':'4000','x-ratelimit-remaining-tokens':'3900','x-ratelimit-reset-tokens':'1s'})
  with pytest.raises(Pause,match='single_request_token_limit') as error:s.reserve('large',4100,'qwen','chat','inv')
  assert error.value.retry_at is None and s.summary()['physical_attempts']==0


def test_temporary_minute_exhaustion_resumes_after_reset_without_new_budget(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 now=[1000.];caps={'max_requests':12,'max_total_tokens':40000,'rpm':30,'rpd':1000,'tpm':8000,'tpd':200000,'max_attempts':2}
 path=tmp_path/'x.sqlite'
 with ExperimentStore(path,{'id':'test'},caps,clock=lambda:now[0]) as s:
  s.headers({'x-ratelimit-remaining-tokens':'100','x-ratelimit-reset-tokens':'2s'})
  with pytest.raises(Pause) as e:s.reserve('next',1000,'qwen','chat','inv')
  assert e.value.retry_at==1002.
 now[0]=1002.1
 with ExperimentStore(path,{'id':'test'},caps,clock=lambda:now[0]) as s:
  assert s.reserve('next',1000,'qwen','chat','inv')==1
  assert s.summary()['physical_attempts']==1


def test_exact_minute_wait_has_no_artificial_extra_delay(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 caps={'max_requests':12,'max_total_tokens':40000,'rpm':1,'rpd':1000,'tpm':8000,'tpd':200000,'max_attempts':2}
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'test'},caps,clock=lambda:1000.) as s:
  a=s.reserve('first',0,'qwen','models','inv');s.finish(a,{},tokens=0)
  with pytest.raises(Pause) as e:s.reserve('next',10,'qwen','chat','inv')
  assert e.value.retry_at==1060.
