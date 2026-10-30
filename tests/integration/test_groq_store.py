import json
import pytest

def limits(**changes):
 return dict(max_requests=10,max_total_tokens=10000,rpm=60,rpd=10,tpm=10000,tpd=10000,max_attempts=2,**changes)

def test_budget_reservations_survive_restart_and_never_reset(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 path=tmp_path/'ledger.sqlite';now=[1000.];clock=lambda:now[0]
 with ExperimentStore(path,{'id':'scope'},limits(),clock=clock) as s:
  for i in range(10):s.reserve(str(i),0,'model','models','inv');now[0]+=2
 with ExperimentStore(path,{'id':'scope'},limits(),clock=clock) as s:
  with pytest.raises(Pause,match='request_budget'):s.reserve('next',0,'model','models','inv')
  assert s.summary()['physical_attempts']==10

def test_pending_attempt_requires_manual_reconciliation_not_duplicate(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as s:
  s.reserve('article',300,'model','chat','inv')
  with pytest.raises(Pause,match='uncertain_request'):s.reserve('article',300,'model','chat','another')

def test_valid_response_global_cache_and_tamper_detection(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as s:
  s.save_result('k',{'request_key':'k','status':'valid'})
  assert s.cached('k')['status']=='valid'
  s.db.execute("UPDATE results SET record_json='{}' WHERE request_key='k'")
  with pytest.raises(ValueError,match='checksum'):s.cached('k')

def test_host_cooldown_and_retry_after_survive_other_scope_restart(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 path=tmp_path/'x.sqlite'
 with ExperimentStore(path,{'id':'a'},limits(),clock=lambda:1000.) as s:s.cooldown(120,'rate_limited')
 with ExperimentStore(path,{'id':'b'},limits(),clock=lambda:1001.) as s:
  with pytest.raises(Pause) as e:s.reserve('next',200,'model','chat','inv')
  assert e.value.retry_at>=1120

def test_token_upper_reservation_then_actual_settlement(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as s:
  attempt=s.reserve('k',300,'model','chat','inv');s.finish(attempt,{'http_status':200},tokens=100)
  assert s.summary()['charged_tokens']==100

def test_model_limits_and_org_shared_tokens_not_ignored(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits(),clock=lambda:1000.) as s:
  s.headers({'x-ratelimit-limit-tokens':'100','x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'2m'})
  with pytest.raises(Pause) as e:s.reserve('k',10,'model','chat','inv')
  assert e.value.retry_at>=1120

def test_global_single_process_lease_rejects_parallel_worker(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as one,ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as two:
  with one.lease():
   with pytest.raises(Pause):
    with two.lease():pass

def test_historical_or_foreign_database_cannot_become_experiment_ledger(tmp_path):
 import sqlite3,hashlib
 from vimfc.experiments.groq_classification.store import ExperimentStore
 path=tmp_path/'pipeline.sqlite'
 with sqlite3.connect(path) as db:db.execute('CREATE TABLE pipeline_runs(id TEXT)')
 before=hashlib.sha256(path.read_bytes()).hexdigest()
 with pytest.raises(ValueError,match='foreign'):ExperimentStore(path,{'id':'scope'},limits())
 assert hashlib.sha256(path.read_bytes()).hexdigest()==before

def test_actual_token_overrun_is_durable_even_though_run_stops(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as s:
  attempt=s.reserve('k',100,'model','chat','inv')
  with pytest.raises(Pause,match='actual_usage'):s.finish(attempt,{'http_status':200},tokens=200)
  assert s.summary()['charged_tokens']==200

def test_unknown_token_reset_cannot_ignore_exhausted_provider_header(tmp_path):
 from vimfc.experiments.groq_classification.store import ExperimentStore,Pause
 with ExperimentStore(tmp_path/'x.sqlite',{'id':'scope'},limits()) as s:
  s.headers({'x-ratelimit-remaining-tokens':'0'})
  with pytest.raises(Pause,match='token_reset_unknown'):s.reserve('k',100,'model','chat','inv')
