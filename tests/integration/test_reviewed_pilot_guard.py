import httpx
import pytest
from vimfc.domain import SourceUnavailable,Incompatible
from vimfc.pipeline.scheduling import CandidateScheduler
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore


def test_integrity_stop_preserves_failed_checkpoint_and_never_enters_next_candidate(tmp_path):
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    guard=PilotGuard(10,progress=False)
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-25T03:00:00Z',{})
        calls=[]
        def work(c):
            calls.append(c['page_id'])
            raise SourceUnavailable('bad hash',code='source_hash_mismatch')
        def observe(c,record,current,invoked):
            guard.progress({'event':'candidate_processed',**record})
        with pytest.raises(Incompatible,match='source_hash_mismatch'):
            CandidateScheduler(store,run).run([{'page_id':1},{'page_id':2}],work,observe)
        assert calls==[1]
        assert [(r['page_id'],r['status']) for r in store.db.execute('SELECT * FROM candidate_work')]==[(1,'source_failed')]


def test_http_budget_is_enforced_before_transport_and_keeps_maxlag5():
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    requests=[];guard=PilotGuard(2,progress=False)
    def transport(request):requests.append(request);return httpx.Response(200,json={'query':{}})
    with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(transport)) as client:
        client.client.event_hooks['request'].append(guard.http_request)
        for _ in range(2):client.query('viwiki',{'action':'query'})
        with pytest.raises(Incompatible,match='request_budget'):
            client.query('viwiki',{'action':'query'})
    assert len(requests)==2 and guard.http_attempts==2 and guard.blocked_attempts==1
    assert all(r.url.params['maxlag']=='5' for r in requests)


def test_missing_hint_or_policy_outcome_is_not_relabelled_integrity():
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    guard=PilotGuard(10,progress=False)
    guard.progress({'event':'candidate_deferred','error_code':'missing_item_hint','page_id':1})
    guard.progress({'event':'candidate_processed','classification':'unresolved','error_code':None,'page_id':2})
    assert guard.last_candidate==2


def test_untyped_revision_identity_failure_also_stops():
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    guard=PilotGuard(10,progress=False)
    with pytest.raises(Incompatible,match='integrity_stop'):
        guard.progress({'status':'source_failed','page_id':1,'error_code':'source_unavailable','error':'Returned revision identity mismatch'})


def test_revision_boundary_stops_before_optional_batch_sibling_refetch(tmp_path):
    from vimfc.domain import SourceRevisionRef
    from vimfc.source.revisions import RevisionSource
    from vimfc.source.dependencies import DependencySource
    from vimfc.source.primary_dependencies import RoleLoader
    from vimfc.storage.raw_cache import RawCache
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    from types import SimpleNamespace
    calls=[]
    class Client:
        def query(self,*args):
            calls.append(args)
            return {'query':{'pages':[{'pageid':1,'revisions':[{'revid':1,'timestamp':'2026-10-20T00:00:00Z','sha1':'bad','slots':{'main':{'content':'{}','contentmodel':'wikibase-item'}}}]}]}}
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');guard=PilotGuard(10,progress=False)
        source=RevisionSource(Client(),store,cache)
        source.integrity_guard=guard.source_failure
        # A hash failure must escape RoleLoader before it can fetch the sibling
        # after isolating a failed member of the original batch.
        refs=[SourceRevisionRef('wikidata',q,i,q,i,'2026-10-20T00:00:00Z','2026-10-25T03:00:00Z') for i,q in [(1,'Q1'),(2,'Q2')]]
        with pytest.raises(Incompatible,match='integrity_stop'):
            RoleLoader(DependencySource(source,'2026-10-25T03:00:00Z',{}))._fetch(refs)
        assert len(calls)==1 and not list((tmp_path/'raw').rglob('*.gz'))


def test_optional_item_validation_cannot_become_completed_incomplete_subtype(config):
    from types import SimpleNamespace
    from vimfc.domain import SourceRevisionRef,RawRevision
    from vimfc.source.dependencies import DependencySource
    from vimfc.pipeline.reviewed_pilot import PilotGuard
    guard=PilotGuard(10,progress=False)
    source=SimpleNamespace(integrity_guard=guard.source_failure)
    raw=RawRevision(SourceRevisionRef('wikidata','Q901',1,'Q901',1,'2026-10-20T00:00:00Z','2026-10-25T03:00:00Z'),'{}','wikibase-item','bad','bad')
    with pytest.raises(Incompatible,match='integrity_stop'):
        DependencySource(source,'2026-10-25T03:00:00Z',config['classification'])._item('Q901',raw)


def test_reviewed_invocation_writes_partial_receipt_on_integrity_stop(tmp_path,config,monkeypatch):
    from copy import deepcopy
    from vimfc.domain import canonical
    import vimfc.pipeline.reviewed_pilot as module
    class FailingProvider:
        def __init__(self,*args):pass
        def discover(self,*args):pytest.fail('Archived cohort must not discover')
        def process(self,candidate):raise SourceUnavailable('bad hash',code='source_hash_mismatch')
    monkeypatch.setattr(module,'HistoricalProvider',FailingProvider)
    cfg=deepcopy(config);cfg['contact']='fixture@example.test'
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-25T03:00:00Z',cfg)
        store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',[(run,i,i,canonical({'page_id':i,'title':str(i)})) for i in range(1,1001)])
        result=module.run_reviewed(store,run,tmp_path/'raw',100,reports=tmp_path/'reports',max_requests=10)
        assert result['stopped'] and not result['completed'] and not result['pilot_accepted']
        assert result['checkpoint_counts']=={'source_failed':1} and result['source_http_attempts']==0
        assert result['last_candidate']==1 and result['report_scope']=='partial_checkpoint_only_integrity_or_budget_stop'
        assert not store.db.execute('SELECT 1 FROM pilot_reports WHERE run_id=?',(run,)).fetchone()


def test_isolated_replay_copies_exact_cohort_and_future_hint_ttl_without_touching_source(tmp_path,config):
    from copy import deepcopy
    from pathlib import Path
    from vimfc.domain import canonical,digest
    from vimfc.pipeline.reviewed_pilot import prepare_reviewed_replay
    cfg=deepcopy(config);cfg['contact']='fixture@example.test'
    source=tmp_path/'source.sqlite';raw=tmp_path/'raw';raw.mkdir()
    with PipelineStore(source) as store:
        old=store.create('2026-10-25T03:00:00Z',cfg)
        store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',[(old,i,i,canonical({'page_id':i,'title':str(i)})) for i in range(1,1001)])
        store.db.execute("INSERT INTO stage_results(run_id,identity,stage,dependency_hash,status,next_retry_at,error_json) VALUES (?,?,'item_hint_refresh',?,'deferred',?,?)",(old,'2','hint','2100-01-01T00:00:00Z',canonical({'code':'missing_item_hint'})))
        store.db.execute('INSERT INTO candidate_work VALUES (?,?,?,?,?,?,?)',(old,2,'deferred',canonical({'page_id':2,'status':'deferred','error_code':'missing_item_hint'}),'2100-01-01T00:00:00Z',1,'2000-01-01T00:00:00Z'))
    before=source.read_bytes()
    context=prepare_reviewed_replay(source,old,config,raw,tmp_path/'replay',tmp_path/'reports')
    assert source.read_bytes()==before and context['run_id']!=old and context['snapshot']=='2026-10-25T03:00:00Z'
    with PipelineStore(context['database']) as store:
        assert [x['page_id'] for x in store.candidates(context['run_id'])]==list(range(1,1001))
        assert digest(store.candidates(context['run_id']))==context['cohort_hash']
        assert store.run(context['run_id'])['config']['contact']=='fixture@example.test'
        assert store.db.execute('SELECT next_retry_at FROM candidate_work WHERE run_id=?',(context['run_id'],)).fetchone()[0]=='2100-01-01T00:00:00Z'
