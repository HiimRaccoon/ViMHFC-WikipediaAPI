import hashlib,json
from pathlib import Path
import pytest
from vimfc.pipeline.classify import classify


def fixture():
    path=Path('tests/fixtures/pilot2_hierarchy.json')
    manifest=json.loads(Path('tests/fixtures/manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['files'][path.name]['sha256']
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('index',[0,1,2])
def test_real_archived_deep_graphs_close_or_explain_missing_frontier(config,index):
    data=fixture();case=data['cases'][index]
    result=classify(case['title'],case['qid'],data['items'],config['classification'],data['snapshot'])
    assert result.outcome==case['expected_outcome']
    assert result.primary_entity_type==case['expected_group']
    if result.outcome=='unresolved':
        assert result.reason=='missing_or_invalid_historical_dependency'
        assert {d['qid'] for d in result.diagnostics}=={'Q141438977','Q141445732'}
    else:
        legacy={**config['classification'],'cached_closure_completion':False,'terminal_unresolved':False}
        assert classify(case['title'],case['qid'],data['items'],legacy,data['snapshot']).reason=='truncated_hierarchy'
