from vimfc.pipeline.collect import CandidateCollector
from vimfc.storage.pipeline_store import PipelineStore


def test_continuation_roundrobin_and_canonical_alias_dedup(tmp_path,config):
    config={**config,'streams':[{'id':'namespace','kind':'allpages','prefix':''}],'namespace_prefixes':[]}
    class Client:
        def __init__(self): self.calls=0
        def query(self,site,params):
            self.calls+=1
            return ({'query':{'allpages':[{'pageid':1,'title':'Alias'},{'pageid':2,'title':'Canonical'}]},'continue':{'apcontinue':'B','continue':'-||'}} if self.calls==1 else {'query':{'allpages':[{'pageid':3,'title':'Other'}]}})
    class Identities:
        def resolve(self,title):
            i=3 if title=='Other' else 2
            return {'status':'resolved','page_id':i,'title':f'Page {i}'}
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        client=Client()
        collector=CandidateCollector(client,Identities(),config)
        state={}
        pages,state,exhausted=collector.discover(state,1)
        assert len(pages)==1 and not exhausted
        pages2,state,exhausted=collector.discover(state,2)
        assert {p['page_id'] for p in pages+pages2}=={2,3}
        assert exhausted and client.calls==2


def test_seed_streams_and_namespace_prefixes_are_scheduled_round_robin(tmp_path,config):
    class Client:
        def __init__(self): self.calls=[]
        def query(self,site,params):
            self.calls.append(params)
            key=params['list']
            identity=len(self.calls)
            return {'query':{key:[{'pageid':identity,'title':str(identity)}]}}
    class Identities:
        def resolve(self,title): return {'status':'resolved','page_id':int(title),'title':title}
    client=Client()
    collector=CandidateCollector(client,Identities(),config)
    pages,state,exhausted=collector.discover({},len(config['streams'])+len(config['namespace_prefixes']))
    assert len({p['page_id'] for p in pages})==len(config['streams'])+len(config['namespace_prefixes'])
    assert any(c.get('apprefix')=='Y' for c in client.calls)
    assert any(c.get('cmtitle')=='Thể loại:Phim' for c in client.calls)
    assert exhausted
