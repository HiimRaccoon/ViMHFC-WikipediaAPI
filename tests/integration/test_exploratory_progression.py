from copy import deepcopy
import json
import pytest
from vimfc.domain import Incompatible, InvalidConfig, canonical, digest
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.runner import PilotControls


def setup(store, config):
    cfg=deepcopy(config);cfg['discovery']={'version':'seed-only-2','variant':'seed-only'}
    run=store.create('2026-10-25T03:00:00Z',cfg)
    candidates=[{'page_id':n,'title':str(n)} for n in range(1,1001)]
    store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',[(run,c['page_id'],i,canonical(c)) for i,c in enumerate(candidates)])
    records=[{'page_id':n,'status':'processed','error_code':None} for n in range(1,100)]
    records.append({'page_id':100,'status':'deferred','error_code':'missing_item_hint'})
    report={'run_id':run,'size':100,'snapshot':'2026-10-25T03:00:00Z','config_hash':digest(cfg),
        'cohort_hash':digest(candidates[:100]),'completed':True,'data_complete':False,'pilot_accepted':False,
        'pinned_transient_manifest_count':0,'source_failure_count':0,'critical_defects':[],
        'work_counts':{'processed':99,'deferred':1,'quarantined_integrity':0,'source_failed':0},
        'work_audit':{'articles':records}}
    PilotControls(store,run).save_report(report)
    return run,report,candidates


def approval(run, report, candidates):
    return {'format':'vimfc-exploratory-progression-1','run_id':run,'config_hash':report['config_hash'],
        'snapshot':report['snapshot'],'cohort_hash':digest(candidates),'from_size':100,'to_size':1000,
        'prior_report_hash':digest(report),'allowed_hint_pages':[100], 'reviewed_quarantine_id':None,
        'replay':{'report_hash':digest(report),'validated_process_count':99,'errors':[]},
        'authorization':'Explicit user request 2026-10-28: bounded Seed-only Pilot100 to1000; no completeness override'}


def test_exploratory_progression_is_scoped_and_never_changes_flags(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run,report,candidates=setup(store,config);gate=PilotControls(store,run)
        with pytest.raises(InvalidConfig):gate.pilot(1000)
        assert gate.pilot(1000,exploratory=approval(run,report,candidates))==1000
        assert gate.report(100)==report
        assert gate.report(100)['data_complete'] is False
        assert gate.report(100)['pilot_accepted'] is False
        with pytest.raises((Incompatible,InvalidConfig)):gate.pilot(10000,exploratory=approval(run,report,candidates))
        with pytest.raises(InvalidConfig):gate.production(report)


def test_even_complete_seed_pilot_requires_replay_permission_before_1000(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run,report,candidates=setup(store,config)
        report['data_complete']=True
        report['work_counts'].update(processed=100,deferred=0)
        report['work_audit']['articles'][-1]={'page_id':100,'status':'completed','error_code':None}
        gate=PilotControls(store,run);gate.save_report(report)
        with pytest.raises(InvalidConfig,match='replay'):gate.pilot(1000)
        permit=approval(run,report,candidates);permit['allowed_hint_pages']=[]
        permit['replay']['validated_process_count']=100
        assert gate.pilot(1000,exploratory=permit)==1000


@pytest.mark.parametrize('bad',['report','cohort','scope','replay','unknown_hint','source_frontier','source_failure','not_seed','unknown_status','counts'])
def test_progression_rejects_unverified_or_unexpected_incompleteness(tmp_path,config,bad):
    with PipelineStore(tmp_path/'db') as store:
        run,report,candidates=setup(store,config)
        permit=approval(run,report,candidates)
        if bad=='report': permit['prior_report_hash']='0'*64
        elif bad=='cohort': permit['cohort_hash']='0'*64
        elif bad=='scope': permit['run_id']='other'
        elif bad=='replay': permit['replay']['validated_process_count']=98
        elif bad=='unknown_hint': permit['allowed_hint_pages']=[]
        elif bad=='source_frontier': report['pinned_transient_manifest_count']=1
        elif bad=='source_failure': report['work_audit']['articles'][-1]['status']='source_failed'
        elif bad=='unknown_status':report['work_audit']['articles'][0]['status']='unverified'
        elif bad=='counts':report['work_counts']['processed']=98
        else:
            cfg=store.run(run)['config'];cfg['discovery']['version']='stratified-lead-1'
            store.db.execute('UPDATE pipeline_runs SET config_json=? WHERE pipeline_run_id=?',(canonical(cfg),run))
        if bad in ('source_frontier','source_failure','unknown_status','counts'):
            PilotControls(store,run).save_report(report);permit=approval(run,report,candidates)
        with pytest.raises((Incompatible,InvalidConfig)):PilotControls(store,run).pilot(1000,exploratory=permit)
