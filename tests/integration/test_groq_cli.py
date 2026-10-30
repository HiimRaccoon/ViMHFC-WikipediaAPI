import json
from pathlib import Path
import pytest

def test_windows_local_cli_exposes_prepare_preflight_run_evaluate_report():
 from vimfc.experiments.groq_classification.cli import parser
 p=parser()
 assert p.parse_args(['run','--dataset',r'C:\Experiment\dataset','--output',r'C:\Experiment\run','--limit','5','--smoke']).limit==5
 for action in ('prepare','preflight','evaluate','report'):assert action in p.format_help()

def test_missing_api_key_stops_before_provider_or_corpus_io(tmp_path,monkeypatch):
 from vimfc.experiments.groq_classification import cli
 monkeypatch.setattr(cli,'load_credentials',lambda p:None)
 monkeypatch.setattr(cli,'GroqClient',lambda *a,**k:pytest.fail('No network/client without key'))
 out=tmp_path/'result';code=cli.main(['run','--dataset',str(tmp_path/'notloaded'),'--output',str(out),'--limit','5','--smoke'])
 assert code==5
 receipts=list((out/'invocations').glob('*.json'));assert len(receipts)==1
 assert json.loads(receipts[0].read_text(encoding='utf-8'))['status']=='LIVE_NOT_RUN_MISSING_API_KEY'

def test_settings_cannot_add_paid_or_external_tools():
 from vimfc.experiments.groq_classification.cli import validate_settings
 cfg=json.loads(Path('configs/groq_classification.json').read_text(encoding='utf-8'))
 assert validate_settings(cfg)==cfg
 for key,value in [('max_requests',0),('temperature',0.5),('tools',[{'type':'web_search'}]),('endpoint','https://evil.test')]:
  modified={**cfg,key:value}
  with pytest.raises(ValueError):validate_settings(modified)

def test_full_run_needs_explicit_resume_and_passing_smoke(tmp_path,monkeypatch):
 from vimfc.experiments.groq_classification import cli
 out=tmp_path/'result';out.mkdir()
 with pytest.raises(ValueError,match='resume'):cli.main(['run','--dataset','unused','--output',str(out),'--limit','300'])

def test_preflight_never_invents_model_when_key_is_missing(tmp_path,monkeypatch):
 from vimfc.experiments.groq_classification import cli
 monkeypatch.setattr(cli,'load_credentials',lambda p:None)
 assert cli.main(['preflight','--dataset','unused','--output',str(tmp_path/'p')])==5
 record=json.loads(next((tmp_path/'p'/'invocations').glob('*.json')).read_text(encoding='utf-8'))
 assert record['model_id'] is None and record['physical_attempts']==0

@pytest.mark.parametrize('has_key',[False,True])
def test_rejected_historical_output_is_never_written_even_on_failure(tmp_path,monkeypatch,has_key):
 from vimfc.experiments.groq_classification import cli
 raw=tmp_path/'historical-raw';raw.mkdir();dataset=tmp_path/'dataset';dataset.mkdir()
 (dataset/'manifest.json').write_text(json.dumps({'source':{'context':{'raw_cache':str(raw),'database':str(tmp_path/'historical-db'/'pipeline.sqlite')}}}),encoding='utf-8')
 monkeypatch.setattr(cli,'load_credentials',lambda p:'synthetic-key' if has_key else None)
 monkeypatch.setattr(cli,'GroqClient',lambda *a,**k:pytest.fail('No HTTP from rejected output'))
 output=raw/'accidental-output'
 with pytest.raises(ValueError,match='preserved source'):cli.main(['run','--dataset',str(dataset),'--output',str(output),'--limit','5','--smoke'])
 assert not output.exists()

def test_unexpected_source_error_receipt_is_stopped_not_starting(tmp_path,monkeypatch):
 from vimfc.experiments.groq_classification import cli
 data=tmp_path/'dataset';data.mkdir();out=tmp_path/'inference'
 (data/'manifest.json').write_text(json.dumps({'source':{'context':{'raw_cache':str(tmp_path/'raw'),'database':str(tmp_path/'source'/'pipeline.sqlite')}}}),encoding='utf-8')
 monkeypatch.setattr(cli,'load_credentials',lambda p:'synthetic-key')
 monkeypatch.setattr(cli,'validate_free_confirmation',lambda p:{'fixture':True})
 def corrupted(*a):raise ValueError('Corrupt source fixture')
 monkeypatch.setattr(cli,'load_dataset',corrupted)
 with pytest.raises(ValueError):cli.main(['run','--dataset',str(data),'--output',str(out),'--limit','5','--smoke'])
 receipt=json.loads(next((out/'invocations').glob('*.json')).read_text(encoding='utf-8'))
 assert receipt['status']=='EXPERIMENT_STOPPED' and receipt['error_type']=='ValueError'
 assert 'Corrupt source fixture' not in json.dumps(receipt) and receipt['physical_attempts']==0

def test_mock_smoke_then_full_resume_does_not_duplicate_and_preserves_mode(tmp_path,monkeypatch,config):
 import hashlib,copy
 from datetime import datetime,timezone
 from vimfc.domain import digest
 from vimfc.experiments.groq_classification import cli
 from vimfc.experiments.groq_classification.store import ExperimentStore
 from vimfc.experiments.groq_classification.classifier import GroqAssistedClassifier
 now=[1000.];clock=lambda:now[0];sleeper=lambda seconds:now.__setitem__(0,now[0]+seconds)
 monkeypatch.setattr(cli.time,'time',clock)
 monkeypatch.setattr(cli.time,'sleep',sleeper)
 monkeypatch.setattr(cli,'ExperimentStore',lambda p,s,l:ExperimentStore(p,s,l,clock=clock))
 monkeypatch.setattr(cli,'GroqAssistedClassifier',lambda *a:GroqAssistedClassifier(*a,clock=clock,sleep=sleeper,estimator=lambda p:{'reserved_tokens':500,'method':'synthetic-test-counter','actual_tokens':None}))
 monkeypatch.setattr(cli,'load_credentials',lambda p:'gsk_'+'x'*30)
 monkeypatch.delenv('GROQ_MODEL',raising=False)
 class Fake:
  calls=[];models=0
  def __init__(self,*a,**k):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def list_models(self):
   Fake.models+=1;return {'models':[{'id':'openai/gpt-oss-20b','active':True}],'headers':{},'http_status':200,'elapsed_seconds':0.01,'response_fingerprint':'models'}
  def complete(self,p):
   Fake.calls.append(copy.deepcopy(p))
   content=json.dumps({'predicted_group':'Organization','abstain':False,'evidence_sentence_ids':[0],'reason':'Câu 0 nêu tổ chức','ambiguity':None})
   return {'body':{'model':p['model'],'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}},'headers':{},'http_status':200,'elapsed_seconds':0.1,'response_fingerprint':'body'}
 monkeypatch.setattr(cli,'GroqClient',Fake)
 data=tmp_path/'dataset';data.mkdir();rows=[]
 for n in range(6):
  inp={'title':'Tổ chức '+str(n),'lead_text':'Đây là một tổ chức.','sentences':[{'id':0,'text':'Đây là một tổ chức.'}]}
  rows.append({'input':inp,'input_fingerprint':digest(inp),'evaluation':{'page_id':n+1,'baseline_outcome':'unclassified','baseline_primary':None,'eligible':False,'sampling_stratum':'Organization'}})
 monkeypatch.setattr(cli,'load_dataset',lambda p:rows)
 (data/'manifest.json').write_text(json.dumps({'manifest_id':'synthetic','config':config,'source':{'context':{'raw_cache':str(tmp_path/'raw'),'database':str(tmp_path/'source'/'pipeline.sqlite')}}}),encoding='utf-8')
 monkeypatch.setattr(cli,'load_free_tier_confirmation',lambda p:{'confirmed':True})
 out=tmp_path/'inference';ledger=tmp_path/'ledger.sqlite'
 local_env=tmp_path/'.env';local_env.write_text('',encoding='utf-8')
 common=['--dataset',str(data),'--output',str(out),'--ledger',str(ledger),'--env-file',str(local_env)]
 assert cli.main(['run',*common,'--limit','1','--smoke','--mode','blind','--model','openai/gpt-oss-20b'])==0
 assert cli.main(['run',*common,'--limit','6','--resume'])==0
 assert len(Fake.calls)==6 and Fake.models==2
 assert all(json.loads(p['messages'][1]['content']) in [r['input'] for r in rows] for p in Fake.calls)
 assert all(r['evaluation']['eligible'] is False for r in rows)
