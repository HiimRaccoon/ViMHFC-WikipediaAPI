from types import SimpleNamespace
from dataclasses import asdict
import pytest
from vimfc.domain import SourceUnavailable,canonical
from vimfc.pipeline.runner import StageExecutor
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.helpers import article


def test_structural_pending_does_not_schedule_or_repeat_a_network_retry(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{})
        executor=StageExecutor(store,run)
        pending={'outcome':'pending','reason':'truncated_hierarchy','retryable':False}
        assert executor.execute('classify',1,{},lambda:pending)==pending
        row=store.db.execute('SELECT next_retry_at FROM stage_results').fetchone()
        assert row[0] is None
        assert executor.execute('classify',1,{},lambda:pytest.fail('must retain deterministic pending'))==pending


def test_retry_after_deadline_survives_nested_stage_failure(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{})
        executor=StageExecutor(store,run)
        deadline='2099-01-01T00:00:00Z'
        def failure():
            raise SourceUnavailable('maxlag',retryable=True,code='maxlag',retry_at=deadline)
        with pytest.raises(SourceUnavailable): executor.execute('acquire',1,{},failure)
        assert store.db.execute('SELECT next_retry_at FROM stage_results').fetchone()[0]==deadline
        with pytest.raises(SourceUnavailable) as error:
            executor.execute('acquire',1,{},lambda:pytest.fail('cooldown'))
        assert error.value.retry_at==deadline


def test_pending_classification_recovers_upserts_and_defers_ineligible_links(tmp_path,config):
    from vimfc.domain import RawRevision,SourceRevisionRef
    from vimfc.pipeline.classify import classify
    a=article(1)
    qid=next(iter(a['classification']['items']))
    title=a['title']
    ref=SourceRevisionRef('viwiki','1',1,title,1001,'2026-10-13T00:00:00Z','2026-10-14T00:00:00Z',None)
    raw=RawRevision(ref,a['extraction']['full_lead'],'wikitext','sha','a'*64,'raw')
    class Identities:
        def raw_page(self,identity): return raw
        def resolve(self,title): pytest.fail('ineligible article must not resolve link targets')
    class Wikidata:
        ready=False
        def cached_qid(self,title): return qid
        def closure(self,qid):
            if not self.ready:
                raise SourceUnavailable('maxlag',retryable=True,code='maxlag',retry_at='2099-01-01T00:00:00Z',details={'titles':'Q5'},partial_items={qid:a['classification']['items'][qid]})
            return a['classification']['items']
    class Client:
        stage=''
        def query(self,*args): pytest.fail('historical association should avoid current pageprops')
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(ref.snapshot,config)
        wd=Wikidata()
        provider=HistoricalProvider(store,run,Client(),RawCache(tmp_path/'raw'),identities=Identities(),wikidata=wd)
        first=provider.process({'page_id':1})
        assert first['retryable'] and first['retry_at']=='2099-01-01T00:00:00Z'
        assert first['classification']['items'] and first['graph_status']=='deferred_ineligible'
        assert first['classification']['error_details']['titles']=='Q5'
        store.db.execute("UPDATE stage_results SET next_retry_at='2000-01-01T00:00:00Z'")
        wd.ready=True
        second=provider.process({'page_id':1})
        assert second['classification']['outcome']=='completed'
        rows=store.db.execute('SELECT record_json FROM classification_results').fetchall()
        assert len(rows)==1 and 'completed' in rows[0][0]
