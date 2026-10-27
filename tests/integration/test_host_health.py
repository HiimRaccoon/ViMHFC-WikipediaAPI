"""Host-level backpressure tests: no real sockets or pilot data."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import httpx
import pytest

from vimfc.domain import SourceUnavailable, canonical
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.scheduling import CandidateScheduler
from vimfc.pipeline.runner import Builder, PilotControls
from tests.integration.test_deferred_resume import cohort, FUTURE, PAST
from tests.unit.test_classify import SNAP


def seed_queue(store, run, count):
    cohort(store, run, count)
    for page in range(1,count+1):
        value={'page_id':page,'status':'deferred','error_code':'maxlag','retry_at':PAST,
               'error_details':{'site':'wikidata'},'retryable':True}
        store.db.execute('INSERT INTO candidate_work VALUES (?,?,?,?,?,?,?)',
            (run,page,'deferred',canonical(value),PAST,3,PAST))


def test_preflight_never_probes_during_persisted_cooldown(tmp_path):
    def denied(request):pytest.fail('Cooldown must prevent the health HTTP probe')
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(denied)) as client:
        store.db.execute('INSERT INTO api_cooldowns VALUES (?,?)',('wikidata',FUTURE))
        health=client.enable_wikidata_health(store)
        result=health.preflight()
        assert not result['allowed'] and not result['probe_performed']
        assert result['retry_at']==FUTURE
        assert not any(k.endswith(':requests') for k in client.metrics)


def test_failed_preflight_is_one_attempt_opens_persistent_circuit_and_honors_retry_after(tmp_path):
    requests=[];sleeps=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,headers={'Retry-After':'600'},json={'error':{'code':'maxlag','info':'busy'}})
    with PipelineStore(tmp_path/'db') as store:
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond),sleep=sleeps.append) as client:
            result=client.enable_wikidata_health(store).preflight()
            assert not result['allowed'] and result['state']=='open' and result['probe_performed']
            assert len(requests)==1 and sleeps==[]
            assert requests[0].url.params['maxlag']=='5'
            assert datetime.fromisoformat(result['retry_at'].replace('Z','+00:00'))>=datetime.now(timezone.utc)+timedelta(seconds=590)
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as second:
            after=second.enable_wikidata_health(store).preflight()
            assert not after['allowed'] and not after['probe_performed'] and len(requests)==1


def test_probe_success_is_advisory_and_next_data_maxlag_trips_before_http_retry(tmp_path):
    requests=[]
    def respond(request):
        requests.append(request)
        if len(requests)==1:return httpx.Response(200,json={'query':{'general':{'sitename':'fixture'}}})
        return httpx.Response(200,json={'error':{'code':'maxlag','info':'busy again'}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as client:
        health=client.enable_wikidata_health(store)
        result=health.preflight()
        assert result['allowed'] and not result['stability_confirmed']
        with pytest.raises(SourceUnavailable) as error:
            client.query('wikidata',{'action':'query','titles':'Q1','prop':'revisions'})
        assert error.value.code=='maxlag' and error.value.retryable
        assert len(requests)==2 and health.snapshot()['state']=='open'
        with pytest.raises(SourceUnavailable):client.query('wikidata',{'action':'query'})
        assert len(requests)==2


def test_second_preflight_cannot_overlap_the_owned_half_open_probe(tmp_path):
    requests=[];nested=[]
    with PipelineStore(tmp_path/'db') as store:
        def respond(request):
            requests.append(request)
            nested.append(second_health.preflight())
            return httpx.Response(200,json={'query':{'general':{}}})
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as first, MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as second:
            first_health=first.enable_wikidata_health(store);second_health=second.enable_wikidata_health(store)
            assert first_health.preflight()['allowed']
        assert len(requests)==1 and not nested[0]['allowed'] and not nested[0]['probe_performed']


def test_open_host_skips_all_known_host_workers_and_retry_passes_without_queue_edits(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);seed_queue(store,run,10)
        before=[tuple(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
        guard=lambda:{'site':'wikidata','blocked':True,'state':'open','retry_at':FUTURE,'reason':'maxlag'}
        scheduler=CandidateScheduler(store,run,retry_rounds=2,max_wait_seconds=60,host_guard=guard)
        observations=[]
        result=scheduler.run(store.candidates(run),lambda c:pytest.fail('Circuit-open workers must not run'),
            lambda *args:observations.append(args))
        assert scheduler.visits==0 and len(observations)==10 and len(result)==10
        assert [tuple(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]==before


def test_mid_pass_trip_stops_peer_workers_instead_of_repeating_the_whole_host_queue(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);seed_queue(store,run,10)
        state={'site':'wikidata','blocked':False,'state':'closed'};calls=[]
        def work(candidate):
            calls.append(candidate['page_id']);state.update(blocked=True,state='open',retry_at=FUTURE)
            raise SourceUnavailable('maxlag',retryable=True,code='maxlag',retry_at=FUTURE,details={'site':'wikidata'})
        scheduler=CandidateScheduler(store,run,retry_rounds=2,max_wait_seconds=60,host_guard=lambda:state)
        result=scheduler.run(store.candidates(run),work)
        assert calls==[1] and len(result)==10 and all(r['status']=='deferred' for r in result)
        assert store.db.execute('SELECT attempt_count FROM candidate_work WHERE page_id=2').fetchone()[0]==3


@pytest.mark.parametrize('deferred_only',[False,True])
def test_failed_builder_preflight_publishes_pause_receipt_without_touching_pilot_checkpoints(tmp_path,config,deferred_only):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);seed_queue(store,run,100)
        old={'run_id':run,'size':100,'completed':True,'data_complete':False,'attempt_id':'previous-data-attempt'}
        PilotControls(store,run).save_report(old)
        before=[tuple(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
        class Provider:
            client=SimpleNamespace(metrics=Counter())
            def preflight_deferred(self):return {'allowed':False,'blocked':True,'state':'open','retry_at':FUTURE,'probe_performed':False,'site':'wikidata'}
            def process(self,candidate):pytest.fail('No worker after failed preflight')
        result=Builder(store,run,Provider(),tmp_path/'reports',deferred_only=deferred_only).pilot(100)
        assert result['paused'] and not result['completed'] and result['work_counts']['attempted']==0
        assert result['previous_pilot_attempt_id']=='previous-data-attempt'
        assert PilotControls(store,run).report(100)==old
        assert [tuple(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]==before
        assert store.db.execute('SELECT count(*) FROM stage_results').fetchone()[0]==0


def test_expired_circuit_requires_probe_and_recovers_on_later_invocation(tmp_path):
    requests=[]
    def lag(request):
        requests.append(request)
        return httpx.Response(200,json={'error':{'code':'maxlag','info':'lag'}})
    with PipelineStore(tmp_path/'db') as store:
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(lag)) as client:
            first=client.enable_wikidata_health(store).preflight()
            assert not first['allowed'] and first['consecutive_failures']==1
        store.db.execute('UPDATE api_cooldowns SET retry_at=?',(PAST,))
        store.db.execute('UPDATE host_circuits SET next_retry_at=?',(PAST,))
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(lag)) as client:
            health=client.enable_wikidata_health(store)
            assert health.snapshot()['blocked'] and not health.snapshot()['cooldown_active']
            second=health.preflight()
            assert not second['allowed'] and second['consecutive_failures']==2
            deadline=datetime.fromisoformat(second['retry_at'].replace('Z','+00:00'))
            assert deadline>=datetime.now(timezone.utc)+timedelta(seconds=230)
        store.db.execute('UPDATE api_cooldowns SET retry_at=?',(PAST,))
        store.db.execute('UPDATE host_circuits SET next_retry_at=?',(PAST,))
        def success(request):
            requests.append(request)
            return httpx.Response(200,json={'query':{'general':{}}})
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(success)) as client:
            health=client.enable_wikidata_health(store)
            result=health.preflight()
            assert result['allowed'] and not result['stability_confirmed']
            client.query('wikidata',{'action':'query'})
        assert len(requests)==4


def test_only_viwiki_hint_ttls_do_not_trigger_a_wikidata_probe(tmp_path,config):
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.storage.raw_cache import RawCache
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);seed_queue(store,run,1)
        record={'page_id':1,'status':'deferred','error_code':'missing_item_hint','error_details':{'site':'viwiki'},'retry_at':FUTURE}
        store.db.execute('UPDATE candidate_work SET record_json=?,next_retry_at=?',(canonical(record),FUTURE))
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(lambda r:pytest.fail('No due WD work'))) as client:
            provider=HistoricalProvider(store,run,client,RawCache(tmp_path/'raw'))
            result=provider.preflight_deferred()
            assert result['allowed'] and not result['probe_performed'] and result['reason']=='no_due_wikidata_work'


def test_open_wikidata_does_not_suspend_other_host_work(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config);seed_queue(store,run,2)
        record={'page_id':2,'status':'deferred','error_code':'missing_item_hint','error_details':{'site':'viwiki'},'retry_at':PAST}
        store.db.execute('UPDATE candidate_work SET record_json=? WHERE page_id=2',(canonical(record),))
        calls=[]
        def work(candidate):calls.append(candidate['page_id']);return {'page_id':candidate['page_id'],'status':'completed'}
        scheduler=CandidateScheduler(store,run,retry_rounds=2,host_guard=lambda:{'site':'wikidata','blocked':True,'retry_at':FUTURE})
        records=scheduler.run(store.candidates(run),work)
        assert calls==[2] and records[0]['status']=='deferred' and records[1]['status']=='completed'


def test_new_run_can_probe_expired_circuit_inherited_from_another_run(tmp_path,config):
    from vimfc.pipeline.provider import HistoricalProvider
    from vimfc.storage.raw_cache import RawCache
    calls=[]
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config)
        store.db.execute("INSERT INTO host_circuits(site,state,next_retry_at) VALUES ('wikidata','open',?)",(PAST,))
        def respond(request):calls.append(request);return httpx.Response(200,json={'query':{'general':{}}})
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as client:
            result=HistoricalProvider(store,run,client,RawCache(tmp_path/'raw')).preflight_deferred()
            assert result['allowed'] and result['probe_performed'] and len(calls)==1


def test_superseded_probe_cannot_overwrite_newer_state_or_claim_http_was_sent(tmp_path):
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(lambda r:pytest.fail('No physical probe'))) as client:
        health=client.enable_wikidata_health(store);original=client.query
        def replaced(*args,**kwargs):
            store.db.execute("UPDATE host_circuits SET probe_token='other-worker',probe_until=?",(FUTURE,))
            return original(*args,**kwargs)
        client.query=replaced
        result=health.preflight()
        row=store.db.execute('SELECT * FROM host_circuits').fetchone()
        assert not result['allowed'] and not result['probe_performed']
        assert row['probe_token']=='other-worker' and row['state']=='half_open' and row['consecutive_failures']==0


def test_stale_physical_probe_failure_cannot_overwrite_replacement_owner(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        def respond(request):
            store.db.execute("UPDATE host_circuits SET probe_token='new-owner',probe_until=?",(FUTURE,))
            return httpx.Response(200,json={'error':{'code':'maxlag','info':'old probe failed'}})
        with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(respond)) as client:
            result=client.enable_wikidata_health(store).preflight()
            row=store.db.execute('SELECT * FROM host_circuits').fetchone()
            assert result['probe_performed'] and not result['allowed']
            assert row['probe_token']=='new-owner' and row['consecutive_failures']==0
            assert not any(v for k,v in client.metrics.items() if k.endswith(':circuit_trips'))


def test_shared_preflight_also_guards_authorized_nonpilot_entry_without_data_work(tmp_path,config,monkeypatch):
    # Authored local fixture only: bypass authorization verification to isolate
    # the already-authorized resume path; neither _process nor any API runs.
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config)
        class Provider:
            client=SimpleNamespace(metrics=Counter())
            def preflight_deferred(self):return {'allowed':False,'blocked':True,'site':'wikidata','state':'open','retry_at':FUTURE}
        builder=Builder(store,run,Provider(),tmp_path/'reports')
        monkeypatch.setattr(builder.controls,'production',lambda report:None)
        monkeypatch.setattr(builder,'_process',lambda *args:pytest.fail('Blocked host must never enter data processing'))
        result=builder.production({})
        assert result['paused'] and result['execution_mode']=='production'


def test_paused_cli_emits_one_json_document_with_health_receipt(tmp_path,config,monkeypatch,capsys):
    from vimfc.cli import main
    config['contact']='fixture@example.test'
    with PipelineStore(tmp_path/'db') as store:run=store.create(SNAP,config)
    class Paused:
        def __init__(self,*args,**kwargs):pass
        def pilot(self,size):return {'paused':True,'completed':False,'data_complete':False,'pilot_accepted':False,
            'work_counts':{'attempted':0,'processed':0,'deferred':100,'source_failed':0},
            'host_health':{'state':'open'},'health_report_path':'fixture-health.json'}
    monkeypatch.setattr('vimfc.pipeline.runner.Builder',Paused)
    code=main(['pipeline','pilot','--database',str(tmp_path/'db'),'--run',run,'--candidates','100'])
    result=json.loads(capsys.readouterr().out)
    assert code==5 and result['paused'] and result['health_report_path']=='fixture-health.json'
