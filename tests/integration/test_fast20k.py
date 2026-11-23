from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest
from vimfc.domain import Incompatible
from vimfc.pipeline.fast20k import run,load,restore
from vimfc.pipeline.fast20k import CampaignTransport,validate_profile
from vimfc.pipeline.fast20k_corpus import publish
from tests.integration.test_incremental_collection import prepared,factory

def profile(target=2,limit=20,max_candidates=5):
    cfg=json.loads(Path('configs/fast20k.json').read_text())
    cfg.update(target=target,request_limit=limit,max_candidates=max_candidates,checkpoint_every_articles=1)
    return cfg

def call(tmp_path,config,monkeypatch,*,cfg=None,stop=None):
    monkeypatch.setenv('VIMFC_OFFLINE','1')
    _,bundle,_,_=prepared(tmp_path,config)
    calls=[]
    kwargs=dict(bundle=bundle,workspace=tmp_path/'fast',durable=tmp_path/'durable',profile=cfg or profile(),
        client_factory=factory(calls),fixture=True,stop_requested=stop or (lambda:False))
    return kwargs,calls

def test_multibatch_exact_target_and_publication(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch)
    result=run(**kwargs)
    assert result['stop_reason']=='TARGET_REACHED' and result['eligible_articles']==2
    assert result['states']['processed']==2
    assert not calls and not result.get('coverage_error')
    with pytest.raises(Incompatible):publish(kwargs['workspace'],tmp_path/'research-corpus')
    package=publish(kwargs['workspace'],tmp_path/'fixture-corpus',fixture=True)
    assert package['articles']==2 and package['fixture'] and package['claims_generated']==0
    rows=[json.loads(l) for l in (tmp_path/'fixture-corpus/corpus.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==2 and all(len(a['extraction']['paragraphs'])==1 for a in rows)
    before=len(calls)
    assert run(**kwargs)['eligible_articles']==2 and len(calls)==before

def test_candidate_exhaustion_has_explicit_shortfall(tmp_path,config,monkeypatch):
    kwargs,_=call(tmp_path,config,monkeypatch,cfg=profile(target=6))
    result=run(**kwargs)
    assert result['stop_reason']=='CANDIDATE_EXHAUSTED' and result['eligible_articles']==5
    with pytest.raises(Incompatible,match='insufficient'):publish(kwargs['workspace'],tmp_path/'partial',fixture=True)
    assert not (tmp_path/'partial').exists()

def test_budget_is_not_candidate_cap_and_does_not_reset_on_resume(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5,limit=1))
    result=run(**kwargs)
    assert result['stop_reason']=='REQUEST_BUDGET_EXHAUSTED' and len(calls)==1
    assert load(kwargs['durable']/'http-budget.json')['attempts']==1
    second=run(**kwargs)
    assert second['stop_reason']=='REQUEST_BUDGET_EXHAUSTED' and len(calls)==1

def test_checkpoint_restore_new_directory_and_counter_floor(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    flag={'stop':False}
    kwargs.update(stop_requested=lambda:flag['stop'],on_checkpoint=lambda receipt:flag.update(stop=True))
    first=run(**kwargs)
    assert first['stop_reason']=='CHECKPOINT_STOP' and first['eligible_articles']==1
    new=tmp_path/'restored'
    restore(kwargs['bundle'],new,kwargs['durable'],kwargs['profile'])
    kwargs.update(workspace=new,stop_requested=lambda:False,on_checkpoint=None)
    result=run(**kwargs)
    assert result['stop_reason']=='TARGET_REACHED' and result['eligible_articles']==5
    assert len(calls)==4

def test_changed_config_is_refused(tmp_path,config,monkeypatch):
    kwargs,_=call(tmp_path,config,monkeypatch)
    run(**kwargs)
    changed=deepcopy(kwargs['profile']);changed['request_limit']+=1
    kwargs['profile']=changed
    with pytest.raises(Incompatible,match='binding'):run(**kwargs)


def test_restored_lower_sql_counter_cannot_refund_persistent_spending(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    first=run(**kwargs)
    assert len(calls)==4 and first['physical_requests']==4
    with closing(sqlite3.connect(kwargs['workspace']/'pipeline.sqlite')) as db:
        db.execute("UPDATE fast_control SET value='0' WHERE key='attempts'");db.commit()
    result=run(**kwargs)
    assert result['physical_requests']==4 and len(calls)==4
    assert result['eligible_target_reached'] and not result['corpus_complete']


def test_qwen_failure_keeps_articles_and_archived_metadata(tmp_path,config,monkeypatch):
    class Broken:
        def classify(self,*args):raise RuntimeError('test model unavailable')
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    result=run(**kwargs,qwen=Broken())
    assert result['stop_reason']=='TARGET_REACHED' and result['eligible_articles']==5
    with closing(sqlite3.connect(kwargs['workspace']/'pipeline.sqlite')) as db:
        person=json.loads(db.execute('SELECT record_json FROM fast_work WHERE page_id=1').fetchone()[0])
        assert person['classification']['primary_entity_type']=='Person'
        assert person['classification']['classification_source']=='archived_project_metadata'
        assert not db.execute('SELECT 1 FROM fast_qwen WHERE page_id=1').fetchone()
        failures=list(db.execute("SELECT record_json FROM fast_qwen WHERE status='failed'"))
        assert failures and 'test model unavailable' in failures[0][0]


def test_published_qwen_references_are_self_contained(tmp_path,config,monkeypatch):
    import httpx
    from vimfc.pipeline.qwen_fallback import QwenFallback
    from vimfc.domain import digest
    kwargs,_=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    qwen=QwenFallback(kwargs['profile']['qwen'],transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
        'choices':[{'message':{'content':'{"group":"Location","uncertainty":false}'}}]})))
    try:run(**kwargs,qwen=qwen)
    finally:qwen.close()
    publish(kwargs['workspace'],tmp_path/'qwen-corpus',fixture=True)
    with closing(sqlite3.connect(tmp_path/'qwen-corpus/corpus.sqlite')) as db:
        assert json.loads(db.execute("SELECT value FROM metadata WHERE key='configuration'").fetchone()[0])['extraction']['version']=='lead-first-paragraph-1'
        suggested=list(db.execute("SELECT page_id,record_json,raw_json FROM fast_qwen WHERE status='suggested'"))
        assert suggested
        for page,body,raw in suggested:
            assert json.loads(body)['raw_response_reference']==dict(table='fast_qwen',page_id=page,sha256=digest(json.loads(raw)))
        person=json.loads(db.execute('SELECT record_json FROM articles WHERE page_id=1').fetchone()[0])
        assert person['classification']['primary_entity_type']=='Person' and 'classification_suggestion' not in person


def test_corrupt_sources_stop_without_relaxing_integrity(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    kwargs['client_factory']=factory(calls,corrupt=True)
    result=run(**kwargs)
    assert result['stop_reason']=='INTEGRITY_FAILURE' and result['eligible_articles']<5
    before=len(calls)
    with pytest.raises(Incompatible,match='previous integrity'):run(**kwargs)
    assert len(calls)==before


def test_publication_replays_source_and_rejects_fabricated_paragraph(tmp_path,config,monkeypatch):
    from vimfc.domain import canonical,digest
    kwargs,_=call(tmp_path,config,monkeypatch)
    run(**kwargs)
    with closing(sqlite3.connect(kwargs['workspace']/'pipeline.sqlite')) as db:
        page,body=db.execute('SELECT page_id,record_json FROM fast_work WHERE eligible=1 LIMIT 1').fetchone()
        article=json.loads(body);article['extraction']['full_lead']='Fabricated paragraph.'
        article['extraction']['paragraphs']=['Fabricated paragraph.']
        db.execute('UPDATE fast_work SET record_json=?,record_hash=? WHERE page_id=?',(canonical(article),digest(article),page));db.commit()
    with pytest.raises(Incompatible,match='replay'):publish(kwargs['workspace'],tmp_path/'tampered',fixture=True)
    assert not (tmp_path/'tampered').exists()


@pytest.mark.parametrize('field,value',[('request_limit',True),('target',2.5),('batch_size',0),
    ('checkpoint_every_articles',0),('min_interval_seconds',float('nan')),('min_interval_seconds',float('inf'))])
def test_invalid_bounds_fail_closed(field,value):
    cfg=profile();cfg[field]=value
    with pytest.raises(Incompatible):validate_profile(cfg,fixture=True)


def test_transport_offline_denial_and_pre_send_charge(tmp_path,config,monkeypatch):
    import httpx
    from vimfc.storage.pipeline_store import PipelineStore
    from vimfc.domain import SourceUnavailable
    from vimfc.pipeline.fast20k import prepare
    kwargs,_=call(tmp_path,config,monkeypatch)
    prepare(kwargs['bundle'],kwargs['workspace'],kwargs['profile'],fixture=True)
    kwargs['durable'].mkdir()
    request=httpx.Request('GET','https://vi.wikipedia.org/w/api.php?maxlag=5&action=query')
    calls=[]
    with PipelineStore(kwargs['workspace']/'pipeline.sqlite') as store:
        denied=CampaignTransport(store,kwargs['durable'],kwargs['profile'],authorized=True)
        with pytest.raises(SourceUnavailable):denied.handle_request(request)
        assert store.db.execute("SELECT value FROM fast_control WHERE key='attempts'").fetchone()[0]=='0'
        denied.close()
        def uncertain(req):
            assert load(kwargs['durable']/'http-budget.json')['attempts']==1
            calls.append(req);raise httpx.ReadError('uncertain response')
        transport=CampaignTransport(store,kwargs['durable'],kwargs['profile'],delegate=httpx.MockTransport(uncertain))
        with pytest.raises(httpx.ReadError):transport.handle_request(request)
        assert len(calls)==1 and store.db.execute("SELECT value FROM fast_control WHERE key='attempts'").fetchone()[0]=='1'
        with pytest.raises(Incompatible):transport.handle_request(httpx.Request('GET','https://other.invalid/w/api.php?maxlag=5'))
        assert len(calls)==1


@pytest.mark.skipif(__import__('sys').platform!='linux',reason='POSIX SIGTERM recovery requires native Linux')
def test_sigterm_checkpoint_and_resume(tmp_path,config,monkeypatch):
    import subprocess,sys,time,os
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    params=dict(bundle=str(kwargs['bundle']),workspace=str(kwargs['workspace']),durable=str(kwargs['durable']),profile=kwargs['profile'])
    path=tmp_path/'params.json';path.write_text(json.dumps(params))
    child='''
import importlib.util,json,sys,time
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'backend/src'),str(root)]
spec=importlib.util.spec_from_file_location('fast_command',root/'scripts/fast20k.py')
command=importlib.util.module_from_spec(spec);spec.loader.exec_module(command)
from vimfc.pipeline.fast20k import run
from tests.integration.test_incremental_collection import factory
stop=command.install_signals()
def checkpoint(receipt):
    print('CHECKPOINT_READY',flush=True)
    while not stop():time.sleep(.05)
result=run(**json.loads(Path(sys.argv[2]).read_text()),client_factory=factory([]),fixture=True,stop_requested=stop,on_checkpoint=checkpoint)
print(result['stop_reason'],flush=True)
'''
    proc=subprocess.Popen([sys.executable,'-B','-c',child,str(Path.cwd()),str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,'VIMFC_OFFLINE':'1'})
    try:
        assert proc.stdout.readline().strip()=='CHECKPOINT_READY'
        proc.terminate();out,err=proc.communicate(timeout=30)
        assert proc.returncode==0 and 'CHECKPOINT_STOP' in out,err
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
    kwargs['workspace']=tmp_path/'after-signal'
    result=run(**kwargs)
    assert result['eligible_articles']==5 and len(calls)==4
