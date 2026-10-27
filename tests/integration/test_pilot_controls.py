from copy import deepcopy
import pytest
from vimfc.domain import InvalidConfig, Incompatible, digest
from vimfc.pipeline.runner import PilotControls, Builder
from vimfc.storage.pipeline_store import PipelineStore
from tests.helpers import article


def test_manual_pilot_progression_and_production_report_gate(tmp_path,config):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        controls=PilotControls(store,run)
        with pytest.raises(InvalidConfig): controls.pilot(1000)
        assert controls.pilot(100)==100
        with pytest.raises(InvalidConfig): controls.pilot(20000)
        with pytest.raises(InvalidConfig): controls.production(None)
        report={'run_id':run,'config_hash':digest(config),'size':100,'completed':True,'critical_defects':[],'cohort_hash':'a'}
        controls.save_report(report)
        assert controls.pilot(1000)==1000
        with pytest.raises(InvalidConfig): controls.production(report)
        report={**report,'size':10000,'critical_defects':['bad graph']}
        with pytest.raises(InvalidConfig): controls.production(report)
        report['critical_defects']=[]
        controls.save_report(report)
        report['config_hash']='stale'
        with pytest.raises(Incompatible): controls.production(report)


def test_bounded_fixture_pilot_never_calls_production_and_resume_is_cached(tmp_path,config):
    class FixtureProvider:
        def __init__(self): self.processed=[]; self.discovered=[]
        def discover(self,state,limit):
            start=state.get('offset',0)
            ids=list(range(start+1,start+limit+1))
            self.discovered.extend(ids)
            return [{'page_id':i,'title':f'Bài {i}'} for i in ids],{'offset':start+limit},False
        def process(self,candidate):
            self.processed.append(candidate['page_id'])
            return article(candidate['page_id'])
    source=FixtureProvider()
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        builder=Builder(store,run,source,tmp_path/'reports')
        report=builder.pilot(100)
        assert len(source.processed)==100 and len(source.discovered)==100
        assert report['size']==100 and report['completed']
        assert store.run(run)['production_report'] is None
        replay=builder.pilot(100)
        assert len(source.processed)==100 and len(source.discovered)==100
        assert replay['performance']['stages']['process']['reused']==100
        with pytest.raises(InvalidConfig): builder.extend(10000)
        assert len(store.candidates(run))==100


def test_audit_counts_extraction_completed_before_downstream_source_failure(tmp_path,config):
    from vimfc.domain import canonical,SourceUnavailable
    class Provider:
        def discover(self,state,limit): return [{'page_id':i,'title':f'Bài {i}'} for i in range(1,limit+1)],{},False
        def process(self,candidate):
            a=article(candidate['page_id'])
            store.complete(run,str(a['page_id']),'acquire','raw',{'ref':{'revision_id':a['revision_id']},'local_sha256':a['raw_hash']})
            store.db.execute('INSERT OR IGNORE INTO lead_results VALUES (?,?,?,?)',(run,a['page_id'],'extract',canonical(a['extraction'])))
            raise SourceUnavailable('later identity timeout',retryable=True)
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        report=Builder(store,run,Provider(),tmp_path/'reports').pilot(100)
        assert report['template_audit']['attempted']==100
        assert report['template_audit']['successful']==100
        assert report['source_failure_count']==0
        assert report['deferred_count']==100 and not report['data_complete']


def test_warm_report_counts_only_this_invocations_source_requests(tmp_path,config):
    from types import SimpleNamespace
    from collections import Counter
    class Provider:
        def __init__(self): self.client=SimpleNamespace(metrics=Counter()); self.source=SimpleNamespace(cache_hits=0,cache_lookups=0)
        def discover(self,state,limit): return [{'page_id':i,'title':f'Bài {i}'} for i in range(1,limit+1)],{},False
        def process(self,candidate):
            self.client.metrics['viwiki:acquire:revisions:requests']+=1
            self.source.cache_lookups+=1
            return article(candidate['page_id'])
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',config)
        builder=Builder(store,run,Provider(),tmp_path/'reports')
        assert builder.pilot(100)['performance']['request_rates']['physical_request_count']==100
        replay=builder.pilot(100)
        assert replay['performance']['request_rates']['physical_request_count']==0
        assert replay['performance']['cache']['lookups']==0
        attempts=sorted((tmp_path/'reports'/run/'100'/'attempts').glob('*/metrics.json'))
        assert len(attempts)==2
        import json
        assert sorted(json.loads(p.read_text(encoding='utf-8'))['performance']['request_rates']['physical_request_count'] for p in attempts)==[0,100]
