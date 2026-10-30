"""Reviewed additions preserve legacy cases; unknown integrity never becomes evidence."""
from copy import deepcopy
from dataclasses import asdict
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from vimfc.domain import Incompatible,SourceRevisionRef,SourceUnavailable,canonical,digest
from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine,QuarantinedIntegrity,require_review
from vimfc.pipeline.reviewed_pilot import PilotGuard,IntegrityStop
from vimfc.pipeline.runner import StageExecutor,Builder
from vimfc.pipeline.scheduling import CandidateScheduler
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from tests.integration.test_reviewed_quarantine import review_file,ref,SNAP
from tests.helpers import article


def additions(tmp_path,run):
    oldpath,old=review_file(tmp_path,run)
    evidence=Path('tests/fixtures/wikidata-24242903-unverified-response.json').resolve()
    payload=json.loads(evidence.read_bytes());page=payload['query']['pages'][0];rev=page['revisions'][0];slot=rev['slots']['main'];raw=slot['content'].encode('utf-8')
    new=deepcopy(old);new['review']['reason']='Explicit user refusal of independently verified new case only'
    new['revisions']=[{'site':'wikidata','identity':page['title'],'title':page['title'],'page_id':page['pageid'],
        'revision_id':rev['revid'],'revision_timestamp':rev['timestamp'],'snapshot':run['snapshot'],'lookup_timestamp':None,
        'reported_sha1':rev['sha1'],'slot_sha1':slot['sha1'],'computed_sha1':hashlib.sha1(raw).hexdigest(),
        'content_sha256':hashlib.sha256(raw).hexdigest(),'utf8_length':len(raw),'evidence_path':str(evidence),
        'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]
    record={'format':'vimfc-reviewed-integrity-quarantine-set-1','run_id':run['pipeline_run_id'],
        'config_hash':run['config_hash'],'snapshot':run['snapshot'],'approvals':sorted([old,new],key=digest)}
    path=tmp_path/'set.json';path.write_text(canonical(record),encoding='utf-8')
    return oldpath,old,new,path,record


def new_ref():return SourceRevisionRef('wikidata','Q10841261',12113859,'Q10841261',24242903,'2013-04-10T19:39:10Z',SNAP)


def test_addition_is_append_only_and_old_single_file_cannot_downgrade(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));oldpath,old,new,path,record=additions(tmp_path,run)
        legacy=ReviewedIntegrityQuarantine.from_file(oldpath,run);legacy.bind(store)
        before=dict(store.db.execute("SELECT * FROM run_events WHERE stage='reviewed_integrity_policy'").fetchone())
        gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store);gate.bind(store)
        policies=list(store.db.execute("SELECT * FROM run_events WHERE stage='reviewed_integrity_policy'"))
        assert len(policies)==2 and dict(policies[0])==before
        require_review(store,run['pipeline_run_id'],gate)
        with pytest.raises(Incompatible):require_review(store,run['pipeline_run_id'],legacy)
        with pytest.raises(Incompatible):legacy.bind(store)
        with pytest.raises(Incompatible):gate.check(ref())
        with pytest.raises(QuarantinedIntegrity) as caught:gate.check(new_ref())
        assert caught.value.review_id==digest(new)
        other=asdict(new_ref());other['revision_id']+=1;gate.check(SourceRevisionRef(**other))
        with pytest.raises(IntegrityStop):PilotGuard(1,progress=False).source_failure(SourceUnavailable('unknown SHA-1 mismatch',code='source_hash_mismatch'))


def test_restore_legacy_and_new_cases_without_worker_or_attempt_increment(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));oldpath,old,new,path,record=additions(tmp_path,run)
        legacy=ReviewedIntegrityQuarantine.from_file(oldpath,run);legacy.bind(store)
        legacy_case=legacy.candidate_record(store,1,QuarantinedIntegrity(legacy.review_id,legacy.source_ref))
        CandidateScheduler(store,run['pipeline_run_id']).save(legacy_case,invoked=True)
        StageExecutor(store,run['pipeline_run_id']).execute('process',3,{'identity':'fixed'},lambda:{'evidence':'valid'})
        completed=[dict(r) for r in store.db.execute("SELECT * FROM stage_results WHERE identity='3'")]
        gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store)
        assert gate.restore_candidate(store,1,legacy_case)==legacy_case
        with pytest.raises(QuarantinedIntegrity) as caught:gate.check(new_ref())
        case=gate.candidate_record(store,2,caught.value);CandidateScheduler(store,run['pipeline_run_id']).save(case,invoked=True)
        before=[dict(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
        gate.restore_candidate(store,1,legacy_case);gate.restore_candidate(store,2,case)
        assert before==[dict(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
        assert completed==[dict(r) for r in store.db.execute("SELECT * FROM stage_results WHERE identity='3'")]
        audit=gate.audit([legacy_case,case]);assert audit['quarantined_count']==2 and not audit['integrity_complete']


@pytest.mark.parametrize('mutation',['duplicate','tampered_evidence','foreign_run','omitted_old','reordered'])
def test_set_scope_tamper_and_active_member_omission_refused(tmp_path,config,mutation):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));oldpath,old,new,path,record=additions(tmp_path,run)
        legacy=ReviewedIntegrityQuarantine.from_file(oldpath,run);legacy.bind(store)
        if mutation=='duplicate':record['approvals']=[old,old]
        elif mutation=='tampered_evidence':record['approvals'][0]['revisions'][0]['evidence_sha256']='0'*64
        elif mutation=='foreign_run':record['approvals'][0]['run_id']='other'
        elif mutation=='omitted_old':record['approvals']=[new]
        else:record['approvals'].reverse()
        path.write_text(canonical(record),encoding='utf-8')
        with pytest.raises(Incompatible):
            gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store)
        assert store.db.execute("SELECT count(*) FROM run_events WHERE stage='reviewed_integrity_policy'").fetchone()[0]==1


@pytest.mark.parametrize('warm',[False,True])
def test_added_revision_refused_before_cache_network_or_manifest_use(tmp_path,config,warm):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));_,_,_,path,_=additions(tmp_path,run);gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store)
        class Forbidden:
            def query(self,*a,**k):raise AssertionError('HTTP must not run')
            def get(self,*a,**k):raise AssertionError('Unverified raw must not materialize')
            def find(self,*a,**k):raise AssertionError('Unverified orphan must not materialize')
            def put(self,*a,**k):raise AssertionError('Unverified raw must not be published')
        source=RevisionSource(Forbidden(),store,Forbidden());source.reviewed_quarantine=gate
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',('wikidata','Q10841261',SNAP,canonical(new_ref())))
        if warm:store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)',('wikidata',24242903,canonical({'cache_ref':{'path':'unverified'}})))
        with pytest.raises(QuarantinedIntegrity):source.select_revision('wikidata','Q10841261',SNAP)
        with pytest.raises(QuarantinedIntegrity):source.fetch_revisions('wikidata',[new_ref()])
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==0


def test_builder_restart_restores_completed_and_both_refusals_without_duplicate_work(tmp_path,config):
    cfg=deepcopy(config);cfg['classification'].pop('dependency_policy',None)
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,cfg));oldpath,_,_,path,_=additions(tmp_path,run)
        legacy=ReviewedIntegrityQuarantine.from_file(oldpath,run);legacy.bind(store)
        candidates=[{'page_id':i,'title':f'fixture{i}'} for i in (1,2,3)]
        for i,c in enumerate(candidates):store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run['pipeline_run_id'],c['page_id'],i,canonical(c)))
        deps={'snapshot':SNAP,'config':run['config_hash'],'candidate':candidates[2]}
        StageExecutor(store,run['pipeline_run_id']).execute('process',3,deps,lambda:article(3,links=(1,2)))
        CandidateScheduler(store,run['pipeline_run_id']).save({'page_id':3,'status':'completed'},invoked=True)
        gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store);calls=[]
        class Provider:
            reviewed_quarantine=gate
            def process_dependencies(self,c):
                calls.append(c['page_id']);gate.check(ref() if c['page_id']==1 else new_ref());return {}
            def process(self,c):raise AssertionError('Refusal must not create corpus evidence')
            def validate_completed_record(self,c,r):return r
        builder=Builder(store,run['pipeline_run_id'],Provider(),reports=tmp_path/'reports',restore_completed=True)
        result=builder._process(3,True)
        assert not result['data_complete'] and not result['pilot_accepted'] and result['quarantined_count']==2
        assert set(calls)=={1,2} and 3 not in calls
        before=[dict(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
        calls.clear();again=builder._process(3,True)
        assert not calls and again['quarantined_count']==2
        assert before==[dict(r) for r in store.db.execute('SELECT * FROM candidate_work ORDER BY page_id')]
