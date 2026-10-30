from copy import deepcopy
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
import json
from pathlib import Path
import httpx
import pytest
from vimfc.domain import SourceUnavailable,Incompatible,canonical
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.seed_inventory import SeedInventory,inventory_policy
from tests.integration.test_seed_inventory import Client,payload


class Clock:
    def __init__(self):self.value=datetime(2026,10,28,tzinfo=timezone.utc);self.waits=[]
    def __call__(self):return self.value
    def sleep(self,seconds):self.waits.append(seconds);self.value+=timedelta(seconds=seconds)


def test_503_diagnostics_capture_safe_headers_body_fingerprint_and_context():
    def transport(request):
        assert request.url.params['maxlag']=='5'
        return httpx.Response(503,headers={'Retry-After':'17','Server':'fixture-proxy','Via':'fixture',
            'Content-Type':'text/html','X-Cache':'miss','Set-Cookie':'secret'},content=b'backend unavailable')
    with MediaWikiClient('fixture@example.test',transport=httpx.MockTransport(transport),attempts=1) as client:
        with pytest.raises(SourceUnavailable) as caught:
            client.query('viwiki',{'action':'query','list':'categorymembers','cmtitle':'Category:Test','token':'secret'})
        http=caught.value.details.get('http',{})
        assert http.get('status')==503,'HTTP diagnostic missing status'
        assert http['endpoint']=='https://vi.wikipedia.org/w/api.php'
        assert http['request_context']['cmtitle']=='Category:Test' and http['request_context']['maxlag']==5
        assert 'token' not in http['request_context']
        assert http['response_headers']['retry-after']=='17' and http['response_headers']['via']=='fixture'
        assert 'set-cookie' not in http['response_headers'] and len(http['body_sha256'])==64
        assert http['body_length']==19 and http['retry_after_seconds']==17
        assert caught.value.retry_at


def test_retry_after_http_date_is_not_shortened():
    target=datetime.now(timezone.utc)+timedelta(minutes=3)
    with MediaWikiClient('fixture@example.test',attempts=1,transport=httpx.MockTransport(lambda r:httpx.Response(503,headers={'Retry-After':format_datetime(target,usegmt=True)}))) as client:
        with pytest.raises(SourceUnavailable) as caught:client.query('viwiki',{'action':'query'})
        assert caught.value.details.get('http',{}).get('retry_after_seconds',0)>170
        assert datetime.fromisoformat(caught.value.retry_at.replace('Z','+00:00'))>=target.replace(microsecond=0)


def test_inventory_pacing_is_persisted_and_applies_across_streams(tmp_path,config):
    clock=Clock();policy=inventory_policy(config)
    client=Client([payload([]),payload([])])
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep,jitter=lambda:0) as store:
        store.step(client,'people')
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep,jitter=lambda:0) as store:
        store.step(client,'organizations')
    assert sum(clock.waits)>=1 and len(client.calls)==2


def test_503_opens_durable_host_circuit_and_preserves_cursor_until_authorized_resume(tmp_path,config):
    policy=inventory_policy(config);clock=Clock();cursor={'cmcontinue':'next','continue':'-||'}
    class Failed:
        def query(self,*args,**kwargs):
            assert kwargs['attempts']==1
            raise SourceUnavailable('HTTP 503',code='http_503',retryable=True,
                retry_at=(clock()+timedelta(seconds=60)).isoformat().replace('+00:00','Z'),details={'http':{'status':503}})
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep,jitter=lambda:0.5) as store:
        store.step(Client([payload([],cursor)]),'people');before=store.state('people')
        with pytest.raises(SourceUnavailable):store.step(Failed(),'people')
        assert store.state('people')==before
        first=json.loads(store.db.execute('SELECT record FROM attempt_receipts WHERE attempt_id=2').fetchone()[0])
        assert first['error']['details']['http']['status']==503
        assert first['host_state']['failure_streak']==1
        assert first['host_state']['retry_at']>=(clock()+timedelta(seconds=59)).isoformat().replace('+00:00','Z')
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep,jitter=lambda:0.5) as store:
        other=Client([])
        with pytest.raises(SourceUnavailable):store.step(other,'organizations')
        assert not other.calls and store.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==2
        clock.value+=timedelta(seconds=120)
        with pytest.raises(SourceUnavailable):store.step(Failed(),'people')
        second=json.loads(store.db.execute('SELECT record FROM attempt_receipts WHERE attempt_id=3').fetchone()[0])
        assert second['host_state']['failure_streak']==2 and second['host_state']['backoff_seconds']>first['host_state']['backoff_seconds']
        assert store.state('people')==before


def test_inventory_lease_blocks_second_worker_without_charging_request(tmp_path,config):
    policy=inventory_policy(config);clock=Clock()
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as first,SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as second:
        first.claim_host()
        with pytest.raises(SourceUnavailable,match='lease'):second.step(Client([]),'people')
        assert second.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
        first.release_host()
        second.step(Client([payload([])]),'people')


def test_freeze_cannot_race_another_active_worker(tmp_path,config):
    policy=inventory_policy(config);clock=Clock()
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as first,SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as second:
        first.claim_host()
        with pytest.raises(SourceUnavailable,match='lease'):second.freeze(config)
        assert not second.db.execute("SELECT 1 FROM metadata WHERE key='frozen'").fetchone()


def test_expired_lease_recovery_does_not_reset_used_attempts(tmp_path,config):
    policy=inventory_policy(config);policy['max_requests']=1;clock=Clock()
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as first:
        first.step(Client([payload([])]),'people');first.claim_host()
    clock.value+=timedelta(minutes=3)
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as second:
        with pytest.raises(Incompatible,match='budget'):second.step(Client([]),'organizations')
        assert second.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==1


def context_files(tmp_path,config):
    cfg=deepcopy(config);cfg['contact']='fixture@example.test';cfg['user_agent']='ViMFC-fixture'
    for key in ('classification','extraction','ranking'):
        (tmp_path/(key+'.json')).write_text(canonical(cfg.pop(key)),encoding='utf-8')
        cfg[key+'_file']=key+'.json'
    path=tmp_path/'pipeline.json';path.write_text(canonical(cfg),encoding='utf-8')
    return {'config':str(path),'database':str(tmp_path/'historical/db.sqlite'),'raw_cache':str(tmp_path/'historical/raw')}


def test_failure_telemetry_and_error_receipt_are_written_when_collection_aborts(tmp_path,config):
    from vimfc.pipeline.inventory_acquisition import collect_inventory
    context=context_files(tmp_path,config)
    calls=[]
    def factory(cfg):
        def transport(request):calls.append(request);return httpx.Response(503,headers={'Retry-After':'5','Server':'fixture'})
        return MediaWikiClient(cfg['contact'],attempts=1,transport=httpx.MockTransport(transport))
    result=collect_inventory(context,tmp_path/'output',client_factory=factory)
    assert len(calls)==1 and result['stopped'] and result['http_attempts_this_invocation']==1
    receipt=json.loads(Path(result['receipt_path']).read_text(encoding='utf-8'))
    assert receipt['failure']['code']=='http_503' and receipt['failure']['details']['http']['status']==503
    assert receipt['resources']['available_measurements'] and receipt['resources']['peak_rss_bytes']>0
    assert receipt['resources']['cpu_seconds']>=0 and receipt['end_to_end_seconds']>=0
    assert not (tmp_path/'output/inventory.json').exists()
    assert (Path(result['receipt_path']).parent/'resources.json').exists()


def test_legacy_75_attempts_are_retained_and_next_request_is_76(tmp_path,config):
    clock=Clock();policy=inventory_policy(config)
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as store:
        store.db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?)',[(i,'people','2026-10-27T00:00:00Z','2026-10-27T00:00:01Z','HTTP 503' if i in (28,75) else None) for i in range(1,76)])
        store.db.commit()
        before=[tuple(r) for r in store.db.execute('SELECT * FROM attempts ORDER BY id')]
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as store:
        store.step(Client([payload([{'pageid':1,'ns':0,'title':'A','type':'page'}])]),'people')
        assert [tuple(r) for r in store.db.execute('SELECT * FROM attempts WHERE id<=75 ORDER BY id')]==before
        assert store.db.execute('SELECT max(id),count(*) FROM attempts').fetchone()[:]==(76,76)
        assert store.db.execute('SELECT attempt_id FROM attempt_receipts').fetchone()[0]==76


def test_partial_response_failure_rolls_back_origins_and_preserves_cursor_receipt(tmp_path,config):
    clock=Clock();policy=inventory_policy(config)
    good={'pageid':1,'title':'A','ns':0,'type':'page'}
    with SeedInventory(tmp_path/'db',policy,clock=clock,sleep=clock.sleep) as store:
        before=store.state('people')
        with pytest.raises(Incompatible):store.step(Client([payload([good,dict(good,pageid=2,ns=6)])]),'people')
        assert store.state('people')==before and store.count()==0
        assert store.db.execute('SELECT count(*) FROM responses').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM origins').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM attempt_receipts').fetchone()[0]==1


def test_proxy_transport_exception_is_not_misreported_as_http_503():
    def failed(request):raise httpx.ProxyError('fixture proxy connection failed')
    with MediaWikiClient('fixture@example.test',attempts=1,transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(SourceUnavailable) as caught:client.query('viwiki',{'action':'query'})
        assert caught.value.code=='proxy_error' and caught.value.details['transport_exception']=='ProxyError'
        assert 'http' not in caught.value.details
        from vimfc.pipeline.scheduling import transient_source_code
        assert transient_source_code(caught.value.code)


def test_failure_telemetry_also_survives_client_initialization_error(tmp_path,config):
    from vimfc.pipeline.inventory_acquisition import collect_inventory
    def failed(cfg):raise ValueError('fixture invalid client setup')
    result=collect_inventory(context_files(tmp_path,config),tmp_path/'output',client_factory=failed)
    assert result['failure']['type']=='ValueError' and result['stopped']
    assert result['resources']['available_measurements'] and result['http_attempts_this_invocation']==0
    assert result['status_after']['charged_attempts']==0


def test_publication_resumes_without_network_after_alias_write_failure(tmp_path,config,monkeypatch):
    import vimfc.pipeline.inventory_acquisition as module
    from vimfc.pipeline.inventory_publication import publish_inventory,recover_publication
    policy=inventory_policy(config);output=tmp_path/'inventory';output.mkdir()
    clock=Clock()
    with SeedInventory(output/'listing-checkpoint.sqlite',policy,clock=clock,sleep=clock.sleep) as store:
        store.step(Client([payload([{'pageid':1,'ns':0,'title':'A','type':'page'}])]),'people')
        import vimfc.pipeline.inventory_publication as publication
        original=publication.publish_alias
        def fail(source,destination):raise OSError('fixture interrupted alias publication')
        monkeypatch.setattr(publication,'publish_alias',fail)
        with pytest.raises(OSError):publish_inventory(store,config,output,output/'acquisition-attempts/one')
        assert store.db.execute("SELECT 1 FROM metadata WHERE key='frozen'").fetchone()
        assert not (output/'inventory.json').exists()
        monkeypatch.setattr(publication,'publish_alias',original)
        recovered=recover_publication(store,config,output)
        assert recovered['source']['unique_candidates']==1
        assert (output/'inventory.json').exists() and (output/'inventory-summary.json').exists()
        assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==1


def test_metadata_corruption_after_failure_does_not_erase_telemetry(tmp_path,config):
    from vimfc.pipeline.inventory_acquisition import collect_inventory
    import sqlite3
    output=tmp_path/'output'
    def failed(cfg):
        with sqlite3.connect(output/'listing-checkpoint.sqlite') as db:
            db.execute("INSERT INTO metadata VALUES ('host_state','not-json')")
        raise ValueError('fixture setup failure followed by metadata corruption')
    result=collect_inventory(context_files(tmp_path,config),output,client_factory=failed)
    assert result['failure']['type']=='ValueError'
    assert result['status_after']['status_read_error'] and result['resources']['available_measurements']
    assert Path(result['receipt_path']).exists() and result['http_attempts_this_invocation']==0
