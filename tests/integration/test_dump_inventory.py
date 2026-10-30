"""Official metadata is discovery input, never accepted article evidence."""
import gzip
import hashlib
import json
from pathlib import Path
import httpx
import pytest
from vimfc.domain import Incompatible


def modules():
    from vimfc.source.dump_download import select_artifacts, Downloader
    from vimfc.source.dump_sql import sql_rows
    from vimfc.source.dump_inventory import DumpInventory
    return select_artifacts, Downloader, sql_rows, DumpInventory


def spec(data, table='page'):
    name=f'viwiki-20261001-{table}.sql.gz'
    return dict(name=name,url='https://dumps.wikimedia.org/viwiki/20261001/'+name,
                size=len(data),sha1=hashlib.sha1(data).hexdigest(),table=table)


def test_manifest_requires_done_matching_checksums_and_exact_official_scope():
    select,*_=modules()
    jobs={};lines=[]
    for table in ('page','categorylinks','linktarget'):
        s=spec(b'abc',table);lines.append(s['sha1']+'  '+s['name'])
        jobs[table]={'status':'done','files':{s['name']:{'url':s['url'].replace('https://dumps.wikimedia.org',''),'size':3,'sha1':s['sha1']}}}
    assert len(select({'jobs':jobs},'\n'.join(lines),'20261001'))==3
    extra='\n'+'a'*40+'  unrelated.sql.gz\n'+'b'*40+'  unrelated.sql.gz'
    assert len(select({'jobs':jobs},'\n'.join(lines)+extra,'20261001'))==3
    with pytest.raises(Incompatible):select({'jobs':jobs},'\n'.join(lines)+'\n'+'c'*40+'  '+spec(b'abc')['name'],'20261001')
    jobs['page']['status']='failed'
    with pytest.raises(Incompatible):select({'jobs':jobs},'\n'.join(lines),'20261001')
    jobs['page']['status']='done';jobs['page']['files'][spec(b'abc')['name']]['url']='https://evil.test/a'
    with pytest.raises(Incompatible):select({'jobs':jobs},'\n'.join(lines),'20261001')


def test_download_resume_range_checksum_and_no_duplicate_wire(tmp_path):
    _,Downloader,*_=modules();data=b'abcdef';s=spec(data);requests=[]
    def serve(request):
        requests.append(request)
        assert request.headers['Range']=='bytes=3-'
        return httpx.Response(206,headers={'Content-Range':'bytes 3-5/6','Content-Length':'3'},content=data[3:])
    (tmp_path/(s['name']+'.part')).write_bytes(data[:3])
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        d=Downloader(tmp_path,client,sleep=lambda _:None)
        assert d.fetch(s).read_bytes()==data
        assert d.fetch(s).read_bytes()==data
    assert len(requests)==1


@pytest.mark.parametrize('kind',['corrupt','bad_range','too_large','redirect'])
def test_download_never_publishes_unverified_response(tmp_path,kind):
    _,Downloader,*_=modules();s=spec(b'abcdef')
    if kind=='bad_range':(tmp_path/(s['name']+'.part')).write_bytes(b'abc')
    def serve(_):
        if kind=='redirect':return httpx.Response(302,headers={'Location':'https://evil.test'})
        if kind=='bad_range':return httpx.Response(206,headers={'Content-Range':'bytes 0-2/6'},content=b'def')
        return httpx.Response(200,content=b'xxxxxx' if kind=='corrupt' else b'01234567')
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:
        with pytest.raises(Incompatible):Downloader(tmp_path,c,sleep=lambda _:None).fetch(s)
    assert not (tmp_path/s['name']).exists()


def test_retry_after_restart_and_budget_do_not_loop(tmp_path):
    _,Downloader,*_=modules();s=spec(b'abc');calls=[]
    def serve(_):calls.append(1);return httpx.Response(503,headers={'Retry-After':'600'},content=b'bad')
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:
        d=Downloader(tmp_path,c,sleep=lambda _:None,max_attempts=2)
        with pytest.raises(Incompatible):d.fetch(s)
        with pytest.raises(Incompatible):Downloader(tmp_path,c,sleep=lambda _:None,max_attempts=2).fetch(s)
    assert len(calls)==1
    state=json.loads((tmp_path/'download-state.json').read_text())
    assert state['attempts']==1 and state['retry_at']>0


def sql(path,table,columns,rows):
    body=b'CREATE TABLE `'+table.encode()+b'` (\n'+b',\n'.join(b'  `'+c.encode()+b'` varbinary(255)' for c in columns)+b'\n) ENGINE=InnoDB;\nINSERT INTO `'+table.encode()+b'` VALUES\n'+b',\n'.join(rows)+b';\n-- Dump completed\n'
    path.write_bytes(gzip.compress(body));return path


def test_binary_sql_escaping_and_arity(tmp_path):
    *_,rows,_=modules()
    p=sql(tmp_path/'a.gz','categorylinks',['cl_from','cl_sortkey','cl_target_id'],[b"(4,'\xdc\\n\\\'x',9)"])
    schema,it=rows(p,'categorylinks')
    assert schema==['cl_from','cl_sortkey','cl_target_id']
    assert list(it)==[(4,b"\xdc\n'x",9)]
    p=sql(tmp_path/'a.gz','page',['page_id','page_title'],[b"(4,'unfinished)"])
    with pytest.raises(Incompatible):list(rows(p,'page')[1])


def fixtures(tmp_path):
    pages=sql(tmp_path/'p.gz','page',['page_id','page_namespace','page_title','page_is_redirect'],
        [b"(1,0,'Alpha',0)",b"(2,0,'Beta',0)",b"(3,0,'Redirect',1)",b"(10,14,'Nh\xc3\xa2n_v\xe1\xba\xadt_c\xc3\xb2n_s\xe1\xbb\x91ng',0)",b"(11,14,'Child',0)",b"(12,1,'Talk',0)"])
    targets=sql(tmp_path/'t.gz','linktarget',['lt_id','lt_namespace','lt_title'],
        [b"(100,14,'Nh\xc3\xa2n_v\xe1\xba\xadt_c\xc3\xb2n_s\xe1\xbb\x91ng')",b"(101,14,'Child')"])
    links=sql(tmp_path/'c.gz','categorylinks',['cl_from','cl_type','cl_target_id'],
        [b"(11,'subcat',100)",b"(1,'page',101)",b"(2,'page',100)"])
    return {'page':pages,'linktarget':targets,'categorylinks':links}


def test_transactional_import_category_join_and_deterministic_sampling(tmp_path,config):
    *_,Store=modules();files=fixtures(tmp_path);db=tmp_path/'new.sqlite'
    with Store(db,config) as store:
        for table,path in files.items():store.import_file(table,path,spec(path.read_bytes(),table))
        before=store.summary()
        for table,path in files.items():store.import_file(table,path,spec(path.read_bytes(),table))
        assert store.summary()==before
        inv=store.inventory(target=2,minimum=1)
        assert {r['page_id'] for r in inv['seeds']}=={1,2}
        assert all(r['strata']==['Person'] for r in inv['seeds'])
        assert all(r['candidate']['discovery_metadata_only'] for r in inv['seeds'])
        from vimfc.domain import digest
        assert inv['source']['config_hash']==digest(config)
        assert inv['source']['policy_hash']==digest(inv['source']['policy'])
        assert inv==store.inventory(target=2,minimum=1)
    from vimfc.pipeline.seed_only import plan_seed_cohort,seed_policy
    assert plan_seed_cohort(inv,seed_policy(config['quotas']),2)['slot_counts']=={'seed':2,'neighbor':0}


def test_conflicting_identity_and_partial_import_roll_back(tmp_path,config):
    *_,Store=modules()
    p=sql(tmp_path/'bad.gz','page',['page_id','page_namespace','page_title','page_is_redirect'],[b"(1,0,'A',0)",b"(1,0,'B',0)"])
    with Store(tmp_path/'new.sqlite',config) as store:
        with pytest.raises(Incompatible):store.import_file('page',p,spec(p.read_bytes()))
        assert store.db.execute('select count(*) from pages').fetchone()[0]==0
        assert store.db.execute('select count(*) from imported').fetchone()[0]==0


def test_xml_input_refused_without_executing_schema(tmp_path):
    *_,rows,_=modules();p=tmp_path/'xml.gz';p.write_bytes(gzip.compress(b'<mediawiki><page><id>9</id></page></mediawiki>'))
    with pytest.raises(Incompatible):rows(p,'page')


def test_publish_crash_recovers_without_sealing_partial_manifest(tmp_path,monkeypatch):
    from vimfc.pipeline.dump_acquisition import publish_inventory
    import vimfc.pipeline.dump_acquisition as module
    from vimfc.pipeline.discovery_policy import seal_inventory
    inventory=seal_inventory({'format':'vimfc-discovery-inventory-1','snapshot':'2026-10-25T03:00:00Z',
        'seeds':[{'page_id':1,'origins':[]}],'neighbors':[]})
    original=module.publish_new
    def interrupted(path,data):
        if Path(path).name=='manifest.json':raise OSError('simulated power loss')
        return original(path,data)
    monkeypatch.setattr(module,'publish_new',interrupted)
    with pytest.raises(OSError):publish_inventory(tmp_path,inventory,{'verified':True})
    assert not (tmp_path/'manifest.json').exists()
    monkeypatch.setattr(module,'publish_new',original)
    publish_inventory(tmp_path,inventory,{'verified':True})
    assert json.loads((tmp_path/'manifest.json').read_text())['inventory_id']==inventory['inventory_id']


def test_crash_reserves_wire_budget_before_body_is_read(tmp_path):
    _,Downloader,*_=modules();s=spec(b'abcdef')
    class Crash(httpx.SyncByteStream):
        def __iter__(self):yield b'ab';raise KeyboardInterrupt('power loss')
    def serve(_):return httpx.Response(200,stream=Crash())
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:
        with pytest.raises(KeyboardInterrupt):Downloader(tmp_path,c,max_bytes=6,sleep=lambda _:None).fetch(s)
        with pytest.raises(Incompatible):Downloader(tmp_path,c,max_bytes=6,sleep=lambda _:None).fetch(s)
    state=json.loads((tmp_path/'download-state.json').read_text())
    assert state['reserved_bytes']==6 and state['attempts']==1


def test_ignored_range_restarts_from_zero_and_verifies(tmp_path):
    _,Downloader,*_=modules();data=b'abcdef';s=spec(data)
    (tmp_path/(s['name']+'.part')).write_bytes(b'abc')
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=data))) as c:
        assert Downloader(tmp_path,c,sleep=lambda _:None).fetch(s).read_bytes()==data


def test_truncated_and_wrong_sql_schemas_refused(tmp_path,config):
    *_,rows,Store=modules()
    p=sql(tmp_path/'wrong.gz','page',['revision_id','page_title'],[b"(9,'A')"])
    with Store(tmp_path/'x.sqlite',config) as store:
        with pytest.raises(Incompatible):store.import_file('page',p,spec(p.read_bytes()))
    p=sql(tmp_path/'truncated.gz','page',['page_id'],[b'(1)'])
    p.write_bytes(p.read_bytes()[:-8])
    with pytest.raises((EOFError,Incompatible)):list(rows(p,'page')[1])


def test_acquisition_failure_retains_resource_receipt(tmp_path,config,monkeypatch):
    from vimfc.pipeline.dump_acquisition import acquire
    monkeypatch.chdir(tmp_path)
    config=dict(config,user_agent='ViMFC-test/1',contact='test@example.invalid')
    result=acquire(config,'data/inventories/dump-test',client_factory=lambda:
        httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(503,headers={'Retry-After':'600'}))))
    assert result['exit_code']==3 and result['resources']['peak_rss_bytes']>0
    assert 'HTTP503' in result['error']['message']
    assert not Path('data/inventories/dump-test/manifest.json').exists()
    requests=[]
    retry=acquire(config,'data/inventories/dump-test',client_factory=lambda:
        httpx.Client(transport=httpx.MockTransport(lambda r:requests.append(r))))
    assert not requests and 'cooldown' in retry['error']['message']


def test_publication_refuses_modified_inventory_before_any_alias(tmp_path):
    from vimfc.pipeline.dump_acquisition import publish_inventory
    with pytest.raises(Incompatible):publish_inventory(tmp_path,{'inventory_id':'forged','seeds':[]},{})
    assert not (tmp_path/'inventory.json').exists()
