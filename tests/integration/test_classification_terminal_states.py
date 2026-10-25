from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.runner import StageExecutor
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.domain import SourceUnavailable,SourceRevisionRef,RawRevision
from vimfc.storage.raw_cache import RawCache
from tests.helpers import article


def test_new_run_reevaluates_unresolved_derived_results_but_reuses_complete_proofs(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        old=store.create('2026-10-14T00:00:00Z',{})
        pending={'outcome':'unresolved','reason':'missing_or_invalid_historical_dependency'}
        StageExecutor(store,old).execute('classify',1,{},lambda:pending)
        StageExecutor(store,old).execute('classify',2,{},lambda:{'outcome':'completed'})
        StageExecutor(store,old).execute('process',1,{},lambda:{'classification':pending,'status':'unresolved_classification'})
        new=store.create('2026-10-14T00:00:00Z',{},reuse_run=old)
        calls=[]
        executor=StageExecutor(store,new)
        result=executor.execute('classify',1,{},lambda:calls.append('classify') or {'outcome':'completed'})
        assert result['outcome']=='completed'
        executor.execute('classify',2,{},lambda:calls.append('should_reuse') or {})
        executor.execute('process',1,{},lambda:calls.append('process') or {'status':'completed'})
        assert calls==['classify','process']
        assert StageExecutor(store,old).execute('classify',1,{},lambda:{})==pending


def test_permanent_source_dependency_failure_is_terminal_unresolved_in_new_rules(tmp_path,config):
    a=article(1)
    config['classification']['terminal_unresolved']=True
    ref=SourceRevisionRef('viwiki','1',1,a['title'],1001,'2026-10-13T00:00:00Z','2026-10-14T00:00:00Z')
    class Identities:
        def raw_page(self,identity): return RawRevision(ref,a['extraction']['full_lead'],'wikitext','sha','a'*64,{})
        def resolve(self,title): raise AssertionError('ineligible lead target requests')
    class Wd:
        def cached_qid(self,title): return 'Q1'
        def closure(self,qid): raise SourceUnavailable('No historical revision',code='history_unavailable',details={'titles':'Q2'})
    class Client: stage=''
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(ref.snapshot,config)
        result=HistoricalProvider(store,run,Client(),RawCache(tmp_path/'raw'),identities=Identities(),wikidata=Wd()).process({'page_id':1})
        assert result['classification']['outcome']=='unresolved'
        assert result['classification']['error_code']=='history_unavailable'
        assert not result['quality']['eligible'] and not result['retryable']
        assert result['status']=='unresolved_classification'
        row=store.db.execute("SELECT status,next_retry_at FROM stage_results WHERE stage='classify'").fetchone()
        assert tuple(row)==('completed',None)
