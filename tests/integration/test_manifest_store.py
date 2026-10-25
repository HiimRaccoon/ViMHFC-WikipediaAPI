from copy import deepcopy
import pytest
from vimfc.domain import Incompatible,digest
from vimfc.storage.pipeline_store import PipelineStore
from tests.unit.test_dependency_manifest import article,rules,manifest
from tests.unit.test_classify import item,SNAP


def test_publication_is_immutable_atomic_and_checked_on_read(tmp_path,config):
    r=rules(config);m=manifest({'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')},r)
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config)
        with pytest.raises(KeyboardInterrupt):
            with store.transaction():
                store.publish_manifest(run,1,'request',m)
                raise KeyboardInterrupt()
        assert store.manifest_for(run,1,'request') is None
        store.publish_manifest(run,1,'request',m)
        assert store.manifest_for(run,1,'request')==m
        store.publish_manifest(run,1,'request',m)
        assert store.db.execute('SELECT count(*) FROM dependency_manifests').fetchone()[0]==1
        with pytest.raises(Incompatible): store.manifest_for(run,1,'changed request')
        changed=deepcopy(m);changed['root_qid']='Q999';changed['manifest_id']=digest({k:v for k,v in changed.items() if k!='manifest_id'})
        with pytest.raises(Incompatible): store.publish_manifest(run,1,'request',changed)
        store.db.execute("UPDATE dependency_manifests SET record_json='{}'")
        with pytest.raises(Incompatible): store.manifest_for(run,1,'request')
        for invalid in ('{bad json','[]'):
            store.db.execute('UPDATE dependency_manifests SET record_json=?',(invalid,))
            with pytest.raises(Incompatible):store.manifest_for(run,1,'request')


def test_new_run_can_bind_same_manifest_without_modifying_old_binding(tmp_path,config):
    r=rules(config);m=manifest({'Q1':item('Q1',{'P31':['Q5']},'A'),'Q5':item('Q5')},r)
    with PipelineStore(tmp_path/'db') as store:
        a=store.create(SNAP,config);store.publish_manifest(a,1,'request',m)
        b=store.create(SNAP,config,reuse_run=a);store.publish_manifest(b,1,'request',m)
        assert store.manifest_for(a,1,'request')==store.manifest_for(b,1,'request')
        assert store.db.execute('SELECT count(*) FROM dependency_manifests').fetchone()[0]==1


def test_item_hint_is_bound_to_article_identity(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create(SNAP,config)
        store.pin_item_hint(run,1,article(),'Q1')
        assert store.item_hint(run,1,article())=='Q1'
        with pytest.raises(Incompatible): store.pin_item_hint(run,1,article(),'Q2')
        with pytest.raises(Incompatible): store.item_hint(run,1,{**article(),'revision_id':78})
