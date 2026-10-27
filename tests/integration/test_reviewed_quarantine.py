"""The sole reviewed bad revision remains non-evidence; independent work can finish."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vimfc.domain import Incompatible,InvalidConfig,SourceUnavailable,SourceRevisionRef,canonical,digest
from vimfc.pipeline.quarantine import ReviewedIntegrityQuarantine,QuarantinedIntegrity
from vimfc.pipeline.reviewed_pilot import PilotGuard,IntegrityStop
from vimfc.pipeline.runner import Builder,StageExecutor,PilotControls
from vimfc.pipeline.scheduling import CandidateScheduler
from vimfc.source.revisions import RevisionSource
from vimfc.source.dependencies import DependencySource
from vimfc.source.primary_dependencies import RoleLoader
from vimfc.pipeline.provider import HistoricalProvider
from vimfc.storage.raw_cache import RawCache
from vimfc.storage.pipeline_store import PipelineStore
from tests.helpers import article

SNAP='2026-10-25T03:00:00Z'


def ref():return SourceRevisionRef('wikidata','Q10761835',12035838,'Q10761835',23640705,'2013-04-09T03:15:10Z',SNAP)


def review_file(tmp_path,run):
    evidence=Path('tests/fixtures/wikidata-23640705-unverified-response.json').resolve()
    body=json.loads(evidence.read_bytes());revision=body['query']['pages'][0]['revisions'][0]
    encoded=revision['slots']['main']['content'].encode('utf-8')
    record={'format':'vimfc-reviewed-integrity-quarantine-1','run_id':run['pipeline_run_id'],
        'snapshot':run['snapshot'],'config_hash':run['config_hash'],
        'review':{'reviewed_by':'user-request-2026-10-27','reason':'Reviewed source hash mismatch; not permission to accept content'},
        'revisions':[{**asdict(ref()),'reported_sha1':revision['sha1'],'slot_sha1':revision['slots']['main']['sha1'],
            'computed_sha1':hashlib.sha1(encoded).hexdigest(),'content_sha256':hashlib.sha256(encoded).hexdigest(),
            'utf8_length':len(encoded),'evidence_path':str(evidence),'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    path=tmp_path/'review.json';path.write_text(canonical(record),encoding='utf-8')
    return path,record


@pytest.mark.parametrize('field',['run_id','snapshot','config_hash','evidence_sha256','computed_sha1','revision_id'])
def test_review_rejects_wrong_scope_or_unproven_response(tmp_path,config,field):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));path,data=review_file(tmp_path,run)
        if field in ('run_id','snapshot','config_hash'):data[field]='wrong'
        else:data['revisions'][0][field]='wrong'
        path.write_text(canonical(data),encoding='utf-8')
        with pytest.raises(Incompatible):ReviewedIntegrityQuarantine.from_file(path,run)


def test_quarantine_is_exact_and_never_accepts_a_different_integrity_error(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run)
        with pytest.raises(QuarantinedIntegrity):gate.check(ref())
        other=asdict(ref());other['revision_id']=23640706
        gate.check(SourceRevisionRef(**other))
        other=asdict(ref());other['page_id']=999
        with pytest.raises(Incompatible):gate.check(SourceRevisionRef(**other))
        with pytest.raises(IntegrityStop):PilotGuard(1,progress=False).source_failure(SourceUnavailable('new bad source',code='source_hash_mismatch',details={'revision_id':23640706}))


@pytest.mark.parametrize('warm',[False,True])
def test_reviewed_dependency_is_blocked_before_any_cache_or_network_use(tmp_path,config,warm):
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run)
        class Client:
            def query(self,*args):raise AssertionError('Quarantined content reached HTTP')
        class Cache:
            def get(self,*args):raise AssertionError('Quarantined content materialized')
            def put(self,*args):raise AssertionError('Quarantined content was trusted')
            def find(self,*args):raise AssertionError('Quarantined orphan considered')
        source=RevisionSource(Client(),store,Cache());source.reviewed_quarantine=gate
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',('wikidata','Q10761835',SNAP,canonical(ref())))
        if warm:store.db.execute('INSERT INTO source_revisions VALUES (?,?,?)',('wikidata',23640705,canonical({'cache_ref':{'path':'untrusted-fixture'}})))
        before=store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]
        with pytest.raises(QuarantinedIntegrity):source.select_revision('wikidata','Q10761835',SNAP)
        with pytest.raises(QuarantinedIntegrity):source.fetch_revisions('wikidata',[ref()])
        loader=RoleLoader(DependencySource(source,SNAP,config['classification']))
        with pytest.raises(QuarantinedIntegrity):loader.prepare(['Q10761835'],'subtype')
        assert not loader.items and not loader.errors
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==before
        assert store.db.execute('SELECT count(*) FROM article_manifests').fetchone()[0]==0


def test_independent_work_and_repeated_resume_have_honest_gates_and_stable_quarantine_audit(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        fixture_cfg=deepcopy(config);fixture_cfg['classification'].pop('dependency_policy',None)
        run_id=store.create(SNAP,fixture_cfg);run=store.run(run_id);path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run);gate.bind(store)
        candidates=[{'page_id':i,'title':f'fixture{i}'} for i in (1,2,3)]
        for i,c in enumerate(candidates):store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run_id,c['page_id'],i,canonical(c)))
        # Existing completed result/queue must be restored without any worker.
        deps={'snapshot':SNAP,'config':run['config_hash'],'candidate':candidates[1]}
        StageExecutor(store,run_id).execute('process',2,deps,lambda:article(2,links=(1,3)))
        CandidateScheduler(store,run_id).save({'page_id':2,'status':'completed'},invoked=True)
        stage_before=[dict(r) for r in store.db.execute("SELECT * FROM stage_results WHERE identity='2'")]
        queue_before=dict(store.db.execute('SELECT * FROM candidate_work WHERE page_id=2').fetchone())
        calls=[]
        class Provider:
            reviewed_quarantine=gate
            def process_dependencies(self,c):
                calls.append(c['page_id'])
                if c['page_id']==1:gate.check(ref())
                return {}
            def process(self,c):return article(c['page_id'],links=(1,2))
            def validate_cached_result(self,r):pass
        builder=Builder(store,run_id,Provider(),tmp_path/'reports',restore_completed=True)
        report=builder._process(3,True)
        assert calls==[1,3]
        assert report['work_counts']['processed']==2 and report['quarantined_count']==1
        assert report['work_counts']['quarantined_integrity']==1
        assert not report['data_complete'] and not report['pilot_accepted'] and report['critical_defects']
        assert report['acceptance_status']=='blocked_integrity_quarantine'
        assert store.run(run_id)['status']=='awaiting_integrity_review'
        assert report['quarantine_audit']['articles'][0]['page_id']==1
        pairs=[json.loads(r[0]) for r in store.db.execute('SELECT record_json FROM pair_candidates WHERE run_id=?',(run_id,))]
        assert pairs and all(p['endpoint_a']!=1 and p['endpoint_b']!=1 for p in pairs)
        assert report['pair_audit']['directed_internal_edges']==[(2,3),(3,2)]
        assert report['pair_audit']['external_or_ineligible_link_occurrences']==2
        assert all(r['page_id']!=1 for r in report['classification_audit']['articles'])
        assert not store.db.execute("SELECT 1 FROM stage_results WHERE identity='1' AND stage IN ('process','classify','quality','extract')").fetchone()
        assert not store.db.execute('SELECT 1 FROM article_manifests WHERE page_id=1').fetchone()
        assert [dict(r) for r in store.db.execute("SELECT * FROM stage_results WHERE identity='2'")]==stage_before
        assert dict(store.db.execute('SELECT * FROM candidate_work WHERE page_id=2').fetchone())==queue_before
        case_id=report['quarantine_audit']['articles'][0]['case_id']
        quarantine_before=dict(store.db.execute('SELECT * FROM candidate_work WHERE page_id=1').fetchone())
        calls.clear();again=Builder(store,run_id,Provider(),tmp_path/'reports',restore_completed=True)._process(3,True)
        assert calls==[] and again['quarantine_audit']['articles'][0]['case_id']==case_id
        assert dict(store.db.execute('SELECT * FROM candidate_work WHERE page_id=1').fetchone())==quarantine_before
        assert again['scheduler']['candidate_attempts']==0
        assert again['scheduler']['checkpoint_restored']==3
        assert store.db.execute("SELECT count(*) FROM run_events WHERE stage='integrity_quarantine'").fetchone()[0]==1
        assert (tmp_path/'reports'/run_id/'3'/'attempts'/report['attempt_id']/'quarantine-audit.json').exists()
        # Completion/production controls still reject an integrity-limited pilot.
        with pytest.raises(InvalidConfig,match='Final completed'):PilotControls(store,run_id).production({**report,'size':10000})


def test_restore_quarantine_rejects_forged_or_missing_immutable_receipt(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id))
        gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run_id));gate.bind(store)
        error=QuarantinedIntegrity(gate.review_id,gate.source_ref)
        record=gate.candidate_record(store,1,error)
        CandidateScheduler(store,run_id).save(record,invoked=True)
        assert hasattr(gate,'restore_candidate'), 'Quarantine needs a non-writing validated restore'
        assert gate.restore_candidate(store,1,record)==record
        wrong=deepcopy(record);wrong['error_details']['computed_sha1']='0'*40
        with pytest.raises(Incompatible):gate.restore_candidate(store,1,wrong)
        store.db.execute("DELETE FROM run_events WHERE stage='integrity_quarantine'")
        with pytest.raises(Incompatible):gate.restore_candidate(store,1,record)


def test_known_bad_root_stops_before_claiming_the_old_interrupted_stage(tmp_path,config):
    from tests.integration.test_manifest_provider import Identities,Client
    from tests.integration.test_cached_hierarchy_source import seed
    from tests.unit.test_classify import item
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);run=store.run(run_id);path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run)
        provider=HistoricalProvider(store,run_id,Client(),RawCache(tmp_path/'raw'),identities=Identities(),reviewed_quarantine=gate)
        provider._pinned_qid=lambda raw:'Q10761835'
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',('wikidata','Q10761835',SNAP,canonical(ref())))
        store.db.execute("INSERT INTO stage_results(run_id,identity,stage,dependency_hash,status,last_error) VALUES (?,'1','acquire_manifest','old','pending','interrupted')",(run_id,))
        before=dict(store.db.execute("SELECT * FROM stage_results WHERE stage='acquire_manifest'").fetchone())
        with pytest.raises(QuarantinedIntegrity):provider.process_dependencies({'page_id':1,'title':'Alpha'})
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE stage='acquire_manifest'").fetchone())==before


def test_policy_cannot_be_silently_omitted_on_a_later_invocation(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id))
        gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run_id));gate.bind(store)
        with pytest.raises(Incompatible,match='quarantine'):
            HistoricalProvider(store,run_id,SimpleNamespace(),RawCache(tmp_path/'raw'))


def test_cache_only_manifest_materialization_cannot_restore_reviewed_dependency(tmp_path,config):
    from vimfc.pipeline.dependencies import make_manifest
    from vimfc.pipeline.primary import project_item
    from tests.unit.test_classify import item
    with PipelineStore(tmp_path/'db') as store:
        run=store.run(store.create(SNAP,config));path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run)
        raw_item=item('Q10761835',{},'A')
        raw_item.update(page_id=12035838,revision_id=23640705,revision_timestamp=ref().revision_timestamp,raw_hash='a'*64,content_model='wikibase-item')
        projected=project_item(raw_item,SNAP)
        manifest=make_manifest({'site':'viwiki','page_id':1,'title':'A','revision_id':77,'revision_timestamp':'2026-10-13T00:00:00Z','content_hash':'b'*64},'Q10761835',lambda q:projected,config['classification'],SNAP)
        class Cache:
            def get(self,*args):raise AssertionError('Quarantined manifest reached raw cache')
        source=SimpleNamespace(store=store,cache=Cache(),reviewed_quarantine=gate)
        with pytest.raises(QuarantinedIntegrity):DependencySource(source,SNAP,config['classification']).materialize(manifest,allow_fetch=False)


def test_cached_manifest_is_refused_before_new_binding_is_published(tmp_path,config):
    from vimfc.pipeline.dependencies import article_identity,make_manifest,policy_for
    from vimfc.pipeline.primary import project_item
    from tests.unit.test_classify import item
    from tests.integration.test_manifest_provider import Identities,Client
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id))
        gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run_id))
        provider=HistoricalProvider(store,run_id,Client(),RawCache(tmp_path/'raw'),identities=Identities(),reviewed_quarantine=gate)
        candidate={'page_id':1,'title':'A'};raw=provider._raw(candidate)
        provider._pinned_qid=lambda raw:'Q10761835'
        node=item('Q10761835',{},'A');node.update(page_id=12035838,revision_id=23640705,revision_timestamp=ref().revision_timestamp,raw_hash='a'*64,content_model='wikibase-item')
        projected=project_item(node,SNAP);article_ref=article_identity(raw)
        manifest=make_manifest(article_ref,'Q10761835',lambda q:projected,config['classification'],SNAP)
        request=digest({'article':article_ref,'root_qid':'Q10761835','policy':policy_for(config['classification']),'snapshot':SNAP})
        store.complete(run_id,1,'acquire_manifest',digest({'request_hash':request}),manifest)
        before=dict(store.db.execute("SELECT * FROM stage_results WHERE stage='acquire_manifest'").fetchone())
        with pytest.raises(QuarantinedIntegrity):provider.process_dependencies(candidate)
        assert not store.db.execute('SELECT 1 FROM article_manifests').fetchone()
        assert not store.db.execute('SELECT 1 FROM dependency_manifests').fetchone()
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE stage='acquire_manifest'").fetchone())==before


def test_restored_completed_evidence_becomes_quarantine_without_rewriting_the_checkpoint(tmp_path,config):
    cfg=deepcopy(config);cfg['classification'].pop('dependency_policy',None)
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,cfg);run=store.run(run_id);path,_=review_file(tmp_path,run)
        gate=ReviewedIntegrityQuarantine.from_file(path,run)
        candidate={'page_id':1,'title':'A'};store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run_id,1,0,canonical(candidate)))
        StageExecutor(store,run_id).execute('process',1,{'snapshot':SNAP,'config':run['config_hash'],'candidate':candidate},lambda:article(1))
        CandidateScheduler(store,run_id).save({'page_id':1,'status':'completed'},invoked=True)
        before=dict(store.db.execute("SELECT * FROM stage_results WHERE stage='process'").fetchone())
        class Provider:
            reviewed_quarantine=gate
            def validate_cached_result(self,record):gate.check(ref())
            def process(self,c):raise AssertionError('Restored candidate ran a worker')
        report=Builder(store,run_id,Provider(),tmp_path/'reports',restore_completed=True)._process(1,True)
        assert report['work_counts']['processed']==0 and report['quarantined_count']==1
        assert report['corpus']['article_count']==0
        assert dict(store.db.execute("SELECT * FROM stage_results WHERE stage='process'").fetchone())==before
        assert store.db.execute('SELECT status FROM candidate_work').fetchone()[0]=='quarantined_integrity'


def test_restored_unreviewed_integrity_failure_still_stops_before_workers(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id));gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run_id))
        for i in (1,2):store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run_id,i,i-1,canonical({'page_id':i})))
        CandidateScheduler(store,run_id).save({'page_id':1,'status':'source_failed','error_code':'source_hash_mismatch','error':'Another revision failed'},invoked=True)
        class Provider:
            reviewed_quarantine=gate
            source=SimpleNamespace(integrity_guard=PilotGuard(1,progress=False).source_failure)
            def process(self,c):raise AssertionError('A new worker ran after existing integrity failure')
        with pytest.raises(IntegrityStop):Builder(store,run_id,Provider(),tmp_path/'reports',restore_completed=True)._process(2,True)


def test_unknown_new_failure_after_quarantine_stops_one_guarded_invocation(tmp_path,config,monkeypatch):
    from vimfc.pipeline import reviewed_pilot
    config=deepcopy(config);config['contact']='fixture@example.test'
    with PipelineStore(tmp_path/'db') as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id));calls=[]
        for i in range(1,1001):store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run_id,i,i-1,canonical({'page_id':i})))
        class Provider:
            def __init__(self,*args,reviewed_quarantine):
                self.reviewed_quarantine=reviewed_quarantine;self.source=SimpleNamespace()
            def process_dependencies(self,c):
                calls.append(c['page_id'])
                if c['page_id']==1:self.reviewed_quarantine.check(ref())
                self.source.integrity_guard(SourceUnavailable('new revision mismatch',code='source_hash_mismatch',details={'revision_id':23640706}))
            def process(self,c):raise AssertionError('No classification should be reached')
        monkeypatch.setattr(reviewed_pilot,'HistoricalProvider',Provider)
        report=reviewed_pilot.run_reviewed(store,run_id,tmp_path/'raw',100,reports=tmp_path/'reports',max_requests=1,reviewed_quarantine=path)
        assert report['stopped'] and report['exit_code']==3 and not report['data_complete']
        assert report['failure']['details']['revision_id']==23640706 and calls==[1,2]
        assert report['quarantined_count']==1 and report['source_http_attempts']==0
        assert json.loads(Path(report['quarantine_audit_path']).read_text())['quarantined_count']==1
        assert not (tmp_path/'reports'/run_id/'100'/'metrics.json').exists()


def test_checkpoint_report_never_revives_preserved_quarantined_evidence(tmp_path,config,capsys):
    from vimfc.cli import main
    database=tmp_path/'db'
    with PipelineStore(database) as store:
        run_id=store.create(SNAP,config);path,_=review_file(tmp_path,store.run(run_id))
        gate=ReviewedIntegrityQuarantine.from_file(path,store.run(run_id));gate.bind(store)
        store.complete(run_id,1,'process','preserved',article(1))
        with pytest.raises(QuarantinedIntegrity) as error:gate.check(ref())
        record=gate.candidate_record(store,1,error.value)
        CandidateScheduler(store,run_id).save(record,invoked=False)
    output=tmp_path/'checkpoint.json'
    assert main(['pipeline','report','--database',str(database),'--run',run_id,'--output',str(output)])==0
    report=json.loads(output.read_text())
    assert report['corpus']['article_count']==0 and report['quarantined_count']==1
    assert report['quarantine_audit']['articles'][0]['page_id']==1
    assert report['template_audit']['attempted']==0 and not report['data_complete']
