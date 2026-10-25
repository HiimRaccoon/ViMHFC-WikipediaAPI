from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest
from vimfc.domain import RawRevision,SourceRevisionRef,SourceUnavailable,Incompatible,digest
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.pipeline.runner import Builder
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from tests.unit.test_dependency_manifest import rules
from tests.unit.test_classify import item,SNAP
from tests.integration.test_cached_hierarchy_source import seed


class Identities:
    def raw_page(self,page_id):
        text=Path('tests/fixtures/lead.wiki').read_text(encoding='utf-8')
        ref=SourceRevisionRef('viwiki',str(page_id),page_id,'A',77,'2026-10-13T00:00:00Z',SNAP)
        return RawRevision(ref,text,'wikitext',hashlib.sha1(text.encode()).hexdigest(),hashlib.sha256(text.encode()).hexdigest())
    def resolve(self,title):
        return {'status':'missing','title':title}


class Client:
    def query(self,site,params):
        if site=='viwiki' and params.get('prop')=='pageprops':
            return {'query':{'pages':[{'pageprops':{'wikibase_item':'Q1'}}]}}
        raise SourceUnavailable('No fixture artifact available',code='history_unavailable')


def setup(store,cache,config):
    cfg=deepcopy(config);cfg['classification']=rules(config)
    for q,x in {'Q1':item('Q1',{'P31':['Q5'],'P106':['Q901']},'A'),'Q5':item('Q5'),'Q901':item('Q901')}.items():seed(store,cache,q,x)
    return cfg


def test_provider_pins_manifest_and_fingerprints_before_checkpoint_reuse(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config)
        run=store.create(SNAP,cfg)
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        candidate={'page_id':1,'title':'A'}
        deps=p.process_dependencies(candidate)
        result=p.process(candidate);m=result['classification']['dependency_manifest']
        assert deps['manifest_id']==m['manifest_id'] and result['quality']['eligible']
        assert result['classification']['outcome']=='completed'
        row=store.db.execute("SELECT dependency_hash FROM stage_results WHERE stage='classify'").fetchone()
        assert row[0]==digest(p.classification_dependencies(p._prepared[1]))
        seed(store,cache,'Q999',item('Q999'))
        fresh=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        assert fresh.process_dependencies(candidate)==deps
        assert fresh.process(candidate)==result
        # Corrupt a required physical object after successful checkpoint. It
        # must be verified before either process or classification reuse.
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=105').fetchone()
        (cache.root/json.loads(row[0])['cache_ref']['path']).write_bytes(b'bad fixture')
        with pytest.raises(SourceUnavailable) as error:fresh.process_dependencies(candidate)
        assert error.value.code=='dependency_input_invalid'


def test_manifest_binding_rejects_policy_change_and_allows_compatible_cross_run(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config)
        first=store.create(SNAP,cfg);candidate={'page_id':1,'title':'A'}
        p=HistoricalProvider(store,first,Client(),cache,identities=Identities());a=p.process(candidate)
        second=store.create(SNAP,cfg,reuse_run=first)
        q=HistoricalProvider(store,second,Client(),cache,identities=Identities());b=q.process(candidate)
        assert a==b and q.executor.metrics['classify']['reused']==1
        changed=deepcopy(cfg);changed['classification']['dependency_policy']['max_depth']=1
        q.config=changed
        with pytest.raises(Incompatible):q.process_dependencies(candidate)
        third=store.create(SNAP,changed,reuse_run=second)
        other=HistoricalProvider(store,third,Client(),cache,identities=Identities())
        c=other.process(candidate)
        assert c['classification']['manifest_id']!=a['classification']['manifest_id']
        assert other.executor.metrics['classify']['new']==1
        assert other.executor.metrics['quality']['new']==1
        assert c['quality']['eligible']==a['quality']['eligible']


def test_builder_process_key_includes_manifest_and_reports_it(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config)
        run=store.create(SNAP,cfg)
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,'{"page_id":1,"title":"A"}'))
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        builder=Builder(store,run,p,reports=tmp_path/'reports')
        report=builder._process(1,False)  # isolated one-article fixture, no pilot
        assert report['dependency_manifest_audit']['counts']['complete']==1
        assert report['fingerprint_audit']['manifest_bound_processes']==1
        assert len(builder.records())==1
        assert list((tmp_path/'reports'/run/'1'/'manifests').glob('*.json'))
        row=store.db.execute('SELECT record_json FROM source_revisions WHERE revision_id=105').fetchone()
        (cache.root/json.loads(row[0])['cache_ref']['path']).write_bytes(b'bad fixture')
        failed=builder._process(1,False)
        assert failed['dependency_manifest_audit']['counts']['input_failure']==1
        assert failed['dependency_manifest_audit']['articles'][0]['outcome']=='not_classified'
        assert failed['fingerprint_audit']['manifest_bound_processes']==0


def test_restart_between_acquisition_checkpoint_and_atomic_publication_recovers(tmp_path,config,monkeypatch):
    with PipelineStore(tmp_path/'db') as store:
        cache=RawCache(tmp_path/'raw');cfg=setup(store,cache,config);run=store.create(SNAP,cfg)
        original=store.publish_manifest
        def interrupted(*args):raise KeyboardInterrupt('fixture interruption')
        monkeypatch.setattr(store,'publish_manifest',interrupted)
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        with pytest.raises(KeyboardInterrupt):p.process_dependencies({'page_id':1})
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==0
        monkeypatch.setattr(store,'publish_manifest',original)
        p=HistoricalProvider(store,run,Client(),cache,identities=Identities())
        result=p.process({'page_id':1})
        assert result['classification']['outcome']=='completed'
        assert p.executor.metrics['acquire_manifest']['reused']==1
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==1
