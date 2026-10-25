import json
from pathlib import Path
import pytest
from vimfc.cli import main
from vimfc.storage.pipeline_store import PipelineStore


def test_create_status_and_invalid_production_are_offline(tmp_path,capsys):
    db=str(tmp_path/'pipeline.sqlite')
    assert main(['pipeline','create','--snapshot','2026-10-14T00:00:00Z','--config','configs/pipeline.json','--database',db])==0
    run=json.loads(capsys.readouterr().out)['run_id']
    assert main(['pipeline','status','--run',run,'--database',db])==0
    assert json.loads(capsys.readouterr().out)['candidate_count']==0
    assert main(['pipeline','run','--run',run,'--production','--database',db])==2
    assert 'report' in capsys.readouterr().err.lower()
    assert main(['pipeline','freeze','--run',run,'--database',db,'--output',str(tmp_path/'corpora')])==2
    assert not (tmp_path/'corpora').exists()


def test_changed_config_blocks_resume_and_preserves_old_run(tmp_path,capsys):
    import shutil
    cfg=tmp_path/'configs'
    shutil.copytree('configs',cfg)
    db=str(tmp_path/'db')
    assert main(['pipeline','create','--snapshot','2026-10-14T00:00:00Z','--config',str(cfg/'pipeline.json'),'--database',db])==0
    run=json.loads(capsys.readouterr().out)['run_id']
    rules=json.loads((cfg/'ranking_rules.json').read_text())
    rules['comparison_cap']=10
    (cfg/'ranking_rules.json').write_text(json.dumps(rules))
    assert main(['pipeline','pilot','--run',run,'--candidates','100','--database',db])==3
    assert 'new run' in capsys.readouterr().err.lower()
    with PipelineStore(db) as store:
        assert store.run(run)['config']['ranking']['comparison_cap']==20


def test_empty_report_and_retry_never_start_acquisition(tmp_path,capsys):
    db=str(tmp_path/'db')
    assert main(['pipeline','create','--snapshot','2026-10-14T00:00:00Z','--config','configs/pipeline.json','--database',db])==0
    run=json.loads(capsys.readouterr().out)['run_id']
    assert main(['pipeline','report','--run',run,'--database',db])==0
    assert json.loads(capsys.readouterr().out)['corpus']['valid_2hop_yield']['status']=='unassessed'
    assert main(['pipeline','retry','--run',run,'--database',db])==0
    assert json.loads(capsys.readouterr().out)['requeued']==0
