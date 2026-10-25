import json
from copy import deepcopy
from vimfc.cli import main
from vimfc.domain import canonical,digest
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.report import classification_audit


def test_classification_audit_keeps_pending_roots_and_retry_context(config):
    records=[{'page_id':1,'classification':{'outcome':'pending','reason':'truncated_hierarchy','items':{'Q5':{}},'diagnostics':[{'qid':'Q99','depth':12}]}},
        {'page_id':2,'classification':{'outcome':'pending','reason':'maxlag','retryable':True,'retry_at':'2099-01-01T00:00:00Z','error_code':'maxlag','error_details':{'titles':'Q7'},'items':{}}}]
    result=classification_audit(records,3,config['classification'])
    assert result['attempted']==2 and result['not_attempted']==1
    assert result['outcome_counts']=={'pending':2}
    assert result['pending_with_configured_root']==1
    assert result['articles'][1]['error_details']['titles']=='Q7'


def test_prepare_replay_copies_archived_settings_and_current_rules_without_creating_run(tmp_path,config,capsys):
    db=tmp_path/'db'
    old=deepcopy(config)
    old['contact']='operator@example.test'
    old['extraction']['version']='old-extraction'
    old['classification']['version']='old-classification'
    old['seed']='archived-seed'
    with PipelineStore(db) as store:
        run=store.create('2026-10-25T03:00:00Z',old)
    report=tmp_path/'metrics.json'
    report.write_text(canonical({'run_id':run,'size':100,'snapshot':'2026-10-25T03:00:00Z','config_hash':digest(old)}),encoding='utf-8')
    output=tmp_path/'replay'
    assert main(['pipeline','prepare-replay','--database',str(db),'--source-report',str(report),'--config','configs/pipeline.json','--output-dir',str(output)])==0
    context=json.loads(capsys.readouterr().out)
    from vimfc.config import load_config
    prepared=load_config(context['config'])
    assert context['snapshot']=='2026-10-25T03:00:00Z' and context['source_run']==run
    assert prepared['contact']=='operator@example.test' and prepared['seed']=='archived-seed'
    assert prepared['extraction']==config['extraction'] and prepared['classification']==config['classification']
    assert prepared['ranking']==old['ranking']
    with PipelineStore(db) as store:
        assert store.db.execute('SELECT count(*) FROM pipeline_runs').fetchone()[0]==1
        assert store.run(run)['config']==old
    assert main(['pipeline','prepare-replay','--database',str(db),'--source-report',str(report),'--config','configs/pipeline.json','--output-dir',str(output)])==2


def test_readonly_report_export_does_not_require_original_rules_and_includes_pending(tmp_path,config,capsys):
    db=tmp_path/'db'
    with PipelineStore(db) as store:
        run=store.create('2026-10-25T03:00:00Z',config)
        store.event(run,'configuration_source',{'path':str(tmp_path/'missing-config.json')})
        pending={'page_id':1,'classification':{'outcome':'pending','reason':'maxlag','retryable':True,'items':{}}}
        store.db.execute("INSERT INTO stage_results(run_id,identity,stage,dependency_hash,status,result_json,result_hash) VALUES (?,?,'process',?,'pending',?,?)",(run,'1','dep',canonical(pending),digest(pending)))
    output=tmp_path/'export.json'
    assert main(['pipeline','report','--run',run,'--database',str(db),'--output',str(output)])==0
    assert json.loads(output.read_text())['classification_audit']['outcome_counts']=={'pending':1}
