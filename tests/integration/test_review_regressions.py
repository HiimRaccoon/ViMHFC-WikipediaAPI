from copy import deepcopy
import pytest
from vimfc.domain import SourceUnavailable
from vimfc.pipeline.runner import StageExecutor
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.source.identities import resolve_historical
from vimfc.pipeline.freeze import validate_article
from vimfc.domain import InvalidCorpus
from tests.helpers import article


def test_transient_identity_error_is_propagated_and_can_be_retried():
    def unavailable(title):
        raise SourceUnavailable('temporary timeout',retryable=True)
    with pytest.raises(SourceUnavailable):
        resolve_historical('A',unavailable)


def test_pending_metadata_is_not_a_completed_stage(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{})
        executor=StageExecutor(store,run)
        result=executor.execute('classify',1,{},lambda:{'outcome':'pending','reason':'missing_history','retryable':True})
        assert result['outcome']=='pending'
        row=store.db.execute('SELECT status,result_json,next_retry_at FROM stage_results').fetchone()
        assert row['status']=='pending' and row['next_retry_at']
        store.db.execute('UPDATE stage_results SET next_retry_at=NULL')
        assert executor.execute('classify',1,{},lambda:{'outcome':'completed'})['outcome']=='completed'


def test_same_snapshot_new_rules_reuse_archived_manifest_and_cursors(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,'{"page_id":1,"title":"Original"}'))
        store.db.execute('INSERT INTO discovery_streams VALUES (?,?,?)',(run,'manifest','{"continuation":{"apcontinue":"Next"}}'))
        new_cfg=deepcopy(config)
        new_cfg['extraction']['version']='fixture-next-version'
        newer=store.create('2026-10-14T00:00:00Z',new_cfg,reuse_run=run)
        assert store.candidates(newer)==[{'page_id':1,'title':'Original'}]
        assert store.db.execute('SELECT state_json FROM discovery_streams WHERE run_id=?',(newer,)).fetchone()[0]=='{"continuation":{"apcontinue":"Next"}}'
        changed=store.create('2026-10-15T00:00:00Z',new_cfg,reuse_run=run)
        assert store.candidates(changed)==[]


@pytest.mark.parametrize('mutation',['short_lead','bad_target_revision','wrong_entity','unbacked_root'])
def test_freeze_revalidates_quality_and_classification_proofs(config,mutation):
    a=article(1,links=[2])
    if mutation=='short_lead':
        a['extraction']['full_lead']='A sinh năm 1980. A làm việc tại Hà Nội.'
        a['extraction']['paragraphs']=[a['extraction']['full_lead']]
        from vimfc.pipeline.sentences import split_sentences
        a['extraction']['sentences']=split_sentences(a['extraction']['paragraphs'],config['extraction'])
        a['extraction']['links']=[]
    if mutation=='bad_target_revision': a['extraction']['links'][0]['target']['revision_id']=-123
    if mutation=='wrong_entity':
        key=next(iter(a['classification']['items']))
        a['classification']['items'][key]['entity']['id']='Q999'
    if mutation=='unbacked_root': a['classification']['provenance'][0]['root']='Q9999'
    with pytest.raises(InvalidCorpus):
        validate_article(a,'2026-10-14T00:00:00Z',config['extraction']['version'],rules=config)
