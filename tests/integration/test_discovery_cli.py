import importlib.util,json
from pathlib import Path
import pytest

def driver():
    path=Path('scripts/discovery-experiment.py');spec=importlib.util.spec_from_file_location('discovery_driver',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

@pytest.mark.parametrize('action',['offline','prepare','run','status','report','compare'])
def test_discovery_cli_help_is_offline_and_documents_real_arguments(action,capsys):
    module=driver()
    with pytest.raises(SystemExit) as result:module.main([action,'--help'])
    assert result.value.code==0
    assert 'usage:' in capsys.readouterr().out

def test_offline_output_refuses_historical_and_raw_locations(tmp_path):
    module=driver();raw=tmp_path/'raw';source=tmp_path/'source';raw.mkdir();source.mkdir()
    context={'database':str(source/'db'),'raw_cache':str(raw),'run_id':'fixture','reports':str(tmp_path/'reports')}
    for output in (raw/'new',source/'new',tmp_path/'reports/fixture/new'):
        with pytest.raises(Exception):module.new_output(context,output)
    assert not (raw/'new').exists()

@pytest.mark.parametrize('mutation',['inventory','arm_label'])
def test_compare_rejects_different_sampling_universes(tmp_path,config,capsys,mutation):
    from copy import deepcopy
    from vimfc.storage.pipeline_store import PipelineStore
    from vimfc.domain import canonical,digest
    contexts=[];candidate={'page_id':1,'title':'A'}
    for index,variant in enumerate(('legacy','seed-only','hybrid')):
        cfg=deepcopy(config);cfg['discovery']={'version':'stratified-lead-1','variant':variant if mutation=='inventory' else 'legacy','inventory_id':('b' if mutation=='inventory' and index==2 else 'a')*64}
        database=tmp_path/(variant+'.sqlite')
        with PipelineStore(database) as store:
            run=store.create('2026-10-25T03:00:00Z',cfg)
            store.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,1,0,canonical(candidate)))
            report={'run_id':run,'size':100,'config_hash':digest(cfg),'snapshot':'2026-10-25T03:00:00Z','cohort_hash':digest([candidate])}
            store.db.execute('INSERT INTO pilot_reports VALUES (?,?,?)',(run,100,canonical(report)))
        path=tmp_path/(variant+'.json');path.write_text(canonical({'database':str(database),'raw_cache':str(tmp_path/'raw'),'run_id':run,'variant':variant,
            'snapshot':'2026-10-25T03:00:00Z','config_hash':digest(cfg),'cohort_hash':digest([candidate])}),encoding='utf-8');contexts.append(str(path))
    output=tmp_path/'comparison.json'
    result=driver().main(['compare','--contexts',*contexts,'--size','100','--output',str(output)])
    assert result==3 and not output.exists()
