from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from vimfc.domain import Incompatible,canonical,digest,SourceUnavailable
from vimfc.pipeline.btt import Forest,POLICY,prepare,validate_forest
from vimfc.pipeline.fast20k import run,load,restore
from vimfc.pipeline.fast20k_corpus import publish
from vimfc.storage.pipeline_store import PipelineStore
from tests.integration.test_fast20k import call,profile


def forest(tmp_path,config,graph,target=7):
    store=PipelineStore(tmp_path/'forest.sqlite')
    run_id=store.create('2026-10-25T03:00:00Z',config)
    store.db.executescript('''
      CREATE TABLE fast_work(page_id INTEGER PRIMARY KEY,ordinal INTEGER UNIQUE,status TEXT DEFAULT 'pending',tries INTEGER DEFAULT 0,retry_at TEXT,eligible INTEGER DEFAULT 0,record_json TEXT,record_hash TEXT,error_json TEXT);
      CREATE TABLE fast_qwen(page_id INTEGER PRIMARY KEY,status TEXT,record_json TEXT,raw_json TEXT);
    ''')
    Forest.create(store)
    h=dict(context=dict(run_id=run_id),profile=dict(target=target,max_candidates=100000,selection=POLICY))
    f=Forest(store,h)
    for page,targets in graph.items():
        a=dict(page_id=page,title=f'Article {page}',classification=dict(primary_entity_type='Person'),
            extraction=dict(links=[dict(target_title=f'Article {t}',scope='retained_lead_prose') for t in targets]))
        store.db.execute('INSERT INTO fast_work VALUES (?,?,?,0,NULL,1,?,?,NULL)',(page,page,'processed',canonical(a),digest(a)))
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run_id,page,page,canonical(dict(page_id=page,title=f'Article {page}'))))
        f.refresh(page)
    return store,f


def test_three_levels_two_children_highest_degree_duplicates_and_cycles(tmp_path,config):
    graph={1:[2,3,4,4],2:[1,5,6],3:[1,7,8],4:[],5:[2],6:[],7:[3],8:[]}
    store,f=forest(tmp_path,config,graph)
    with store:
        provider=SimpleNamespace(identities=SimpleNamespace(resolve=lambda t:pytest.fail('cached graph must not request HTTP')))
        assert f.next(provider,lambda:False) is None and f.reason=='TARGET_REACHED'
        members=validate_forest(store.db,7)
        assert {m['page_id'] for m in members}=={1,2,3,5,6,7,8}
        assert max(m['depth'] for m in members)==3
        assert [m['page_id'] for m in members if m['parent_id']==1]==[2,3]
        assert f.next(provider,lambda:False) is None and f.count()==7


def test_switch_root_after_bounded_cluster_and_persist_selection(tmp_path,config):
    graph={1:[2],2:[3],3:[4],4:[],10:[11],11:[12],12:[]}
    store,f=forest(tmp_path,config,graph,target=6)
    with store:
        f.next(SimpleNamespace(),lambda:False)
        rows=validate_forest(store.db,6)
        assert len({r['root_id'] for r in rows})==2 and 4 not in {r['page_id'] for r in rows}
        assert Forest(store,f.header).count()==6


def test_missing_target_admission_provenance_and_probe_bound(tmp_path,config):
    graph={1:list(range(100,120))}
    store,f=forest(tmp_path,config,graph,target=2)
    calls=[]
    def resolve(title):calls.append(title);return dict(status='resolved',page_id=int(title.split()[-1]),title=title)
    with store:
        work=f.next(SimpleNamespace(identities=SimpleNamespace(resolve=resolve)),lambda:False)
        assert work[0]==100
        c=f.candidate(100)
        assert c['discovery_metadata_only'] and c['discovery_origins'][0]['parent_page_id']==1
        assert store.db.execute('SELECT count(*) FROM btt_links').fetchone()[0]==8
        assert calls==['Article 100']


def test_stop_and_candidate_cap_prevent_discovery(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[9,10]},target=2)
    with store:
        assert f.next(SimpleNamespace(),lambda:True) is None and f.reason=='CHECKPOINT_STOP'
        f.profile['max_candidates']=1
        assert f.next(SimpleNamespace(),lambda:False) is None and f.reason=='CANDIDATE_EXHAUSTED'


def test_integrity_error_in_target_lookup_still_fails_closed(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[9]},target=2)
    def bad(t):raise SourceUnavailable('raw hash mismatch',code='hash_mismatch')
    with store,pytest.raises(SourceUnavailable,match='hash mismatch'):
        f.next(SimpleNamespace(identities=SimpleNamespace(resolve=bad)),lambda:False)


def test_root_soft_quota_prefers_underrepresented_group(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[2],2:[1],10:[11],11:[10]},target=4)
    with store:
        store.db.execute("UPDATE btt_pool SET entity_group='Location' WHERE page_id IN (10,11)")
        first=f.choose_root()
        store.db.execute("UPDATE btt_roots SET status='selected' WHERE page_id=?",(first,))
        assert f.choose_root() in ({1,2} if first in {10,11} else {10,11})


def test_archived_unresolved_title_is_not_promoted_by_canonical_match(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[2],2:[3],3:[]},target=2)
    with store:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)',('2026-10-25T03:00:00Z','Article 2',canonical(dict(status='unresolved',reason='ambiguous_owner'))))
        f.next(SimpleNamespace(),lambda:False)
        assert {r['page_id'] for r in validate_forest(store.db,2)}=={2,3}


def test_fork_real_pipeline_fixture_resume_budget_and_publication(tmp_path,config,monkeypatch):
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    stopped={'yes':False}
    kwargs.update(stop_requested=lambda:stopped['yes'],on_checkpoint=lambda r:stopped.update(yes=r['progress']['eligible_articles']>=3))
    result=run(**kwargs)
    assert result['stop_reason']=='CHECKPOINT_STOP'
    parent_header=load(kwargs['workspace']/'workspace.json')
    (kwargs['durable']/'http-budget.json').write_text(canonical(dict(binding=digest(parent_header),attempts=7,last_request=0)))
    cfg=profile(target=2);cfg['selection']=POLICY
    new=tmp_path/'btt-work';durable=tmp_path/'btt-durable'
    h=prepare(kwargs['bundle'],new,durable,kwargs['durable'],cfg,fixture=True)
    assert h['context']['run_id']!=load(kwargs['workspace']/'workspace.json')['context']['run_id']
    before=len(calls)
    btt=run(kwargs['bundle'],new,durable,cfg,client_factory=kwargs['client_factory'],fixture=True)
    assert btt['stop_reason']=='TARGET_REACHED' and btt['selected_articles']==2
    assert btt['eligible_articles']==3
    assert btt['physical_requests']>=7 and load(kwargs['durable']/'http-budget.json')['attempts']==7
    package=publish(new,tmp_path/'btt-corpus',fixture=True)
    assert package['selection_policy']==POLICY and package['articles']==2
    with closing(sqlite3.connect(new/'pipeline.sqlite')) as db:validate_forest(db,2)
    assert load(kwargs['workspace']/'workspace.json')['profile']==kwargs['profile']
    restored=tmp_path/'btt-restored';restore(kwargs['bundle'],restored,durable,cfg)
    count=len(calls)
    assert run(kwargs['bundle'],restored,durable,cfg,client_factory=kwargs['client_factory'],fixture=True)['selected_articles']==2
    assert len(calls)==count


def test_transient_discovery_retries_are_bounded_and_durable(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[9]},target=2)
    calls=[]
    def timeout(t):calls.append(t);raise SourceUnavailable('network timeout',retryable=True,code='network_timeout')
    with store:
        provider=SimpleNamespace(identities=SimpleNamespace(resolve=timeout))
        for n in (1,2):
            assert f.next(provider,lambda:False) is None and f.reason=='SOURCE_COOLDOWN'
            assert store.db.execute('SELECT tries FROM btt_retry').fetchone()[0]==n
        assert f.next(provider,lambda:False) is None and f.reason=='CANDIDATE_EXHAUSTED'
        assert len(calls)==3


def test_forest_rejects_missing_parent_source_link(tmp_path,config):
    store,f=forest(tmp_path,config,{1:[2],2:[3],3:[]},target=3)
    with store:
        f.next(SimpleNamespace(),lambda:False);validate_forest(store.db,3)
        store.db.execute("UPDATE btt_links SET title_key='Invented'")
        with pytest.raises(Incompatible,match='provenance'):validate_forest(store.db,3)


@pytest.mark.parametrize('request_limit,expected',[(30,'TARGET_REACHED'),(8,'REQUEST_BUDGET_EXHAUSTED')])
def test_new_hyperlink_identity_outside_root_inventory_is_accounted(tmp_path,config,monkeypatch,request_limit,expected):
    import hashlib,httpx
    kwargs,calls=call(tmp_path,config,monkeypatch,cfg=profile(target=5))
    stop={'yes':False}
    kwargs.update(stop_requested=lambda:stop['yes'],on_checkpoint=lambda r:stop.update(yes=r['progress']['eligible_articles']>=3))
    run(**kwargs)
    parent=load(kwargs['workspace']/'workspace.json')
    (kwargs['durable']/'http-budget.json').write_text(canonical(dict(binding=digest(parent),attempts=7,last_request=0)))
    cfg=profile(target=4,limit=request_limit);cfg['selection']=POLICY
    work=tmp_path/'btt';durable=tmp_path/'durable-btt';prepare(kwargs['bundle'],work,durable,kwargs['durable'],cfg,fixture=True)
    def make():
        client=kwargs['client_factory']();original=client.client._transport.handler
        def handler(req):
            params=req.url.params
            if params.get('titles') or params.get('pageids')=='6' or params.get('revids')=='106':
                calls.append(dict(params))
                text="Article4 l\u00e0 m\u1ed9t th\u00e0nh ph\u1ed1 th\u00e0nh l\u1eadp n\u0103m 1900 t\u1ea1i [[A]]."
                revision=dict(revid=106,timestamp='2026-10-25T03:00:00Z',sha1=hashlib.sha1(text.encode()).hexdigest(),slots=dict(main=dict(content=text,contentmodel='wikitext')))
                return httpx.Response(200,json=dict(curtimestamp='2026-10-26T03:00:00Z',query=dict(pages=[dict(pageid=6,title='Article 4',ns=0,revisions=[revision])])))
            return original(req)
        client.client._transport=httpx.MockTransport(handler);return client
    result=run(kwargs['bundle'],work,durable,cfg,client_factory=make,fixture=True)
    assert result['stop_reason']==expected
    assert result['physical_requests']==7+len(calls)
    if expected=='TARGET_REACHED':
        with closing(sqlite3.connect(work/'pipeline.sqlite')) as db:
            assert db.execute('SELECT 1 FROM btt_members WHERE page_id=6').fetchone()
            assert not db.execute('SELECT 1 FROM candidates WHERE canonical_page_id=6').fetchone()
        publish(work,tmp_path/'outside-corpus',fixture=True)
    else:
        assert len(calls)==1
        again=run(kwargs['bundle'],work,durable,cfg,client_factory=make,fixture=True)
        assert again['stop_reason']==expected and len(calls)==1
