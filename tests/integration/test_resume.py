import pytest
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.pipeline.runner import StageExecutor
from vimfc.domain import SourceUnavailable, Incompatible


def test_interrupted_stage_releases_lease_reuses_valid_work_and_preserves_attempts(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{'version':1})
        executor=StageExecutor(store,run)
        calls=[]
        def work():
            calls.append(1)
            return {'lead':'a'}
        assert executor.execute('extract','1',{'raw':'hash'},work)=={'lead':'a'}
        assert executor.execute('extract','1',{'raw':'hash'},work)=={'lead':'a'}
        assert len(calls)==1
        def crash():
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            executor.execute('extract','2',{'raw':'hash'},crash)
        # Simulate expired worker lease after process termination.
        store.db.execute("UPDATE stage_results SET lease_until='2000-01-01T00:00:00Z' WHERE identity='2'")
        assert StageExecutor(store,run).execute('extract','2',{'raw':'hash'},work)=={'lead':'a'}
        row=store.db.execute("SELECT status,attempt_count FROM stage_results WHERE identity='2'").fetchone()
        assert row[:]==('completed',2)


def test_new_rules_reuse_only_matching_stage_dependencies(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{'version':1})
        StageExecutor(store,run).execute('raw','1',{'snapshot':'old'},lambda:{'raw':'a'})
        newer=store.create('2026-10-14T00:00:00Z',{'version':2},reuse_run=run)
        calls=[]
        executor=StageExecutor(store,newer)
        assert executor.execute('raw','1',{'snapshot':'old'},lambda:calls.append(1))=={'raw':'a'}
        executor.execute('raw','1',{'snapshot':'changed'},lambda:calls.append(1) or {'raw':'b'})
        assert calls==[1]
        assert store.result(run,'1','raw',__import__('vimfc.domain',fromlist=['digest']).digest({'snapshot':'old'}))=={'raw':'a'}


def test_transient_failure_is_persisted_and_not_immediately_retried(tmp_path):
    with PipelineStore(tmp_path/'db') as store:
        run=store.create('2026-10-14T00:00:00Z',{})
        def fail():
            raise SourceUnavailable('rate limited',retryable=True)
        with pytest.raises(SourceUnavailable):
            StageExecutor(store,run).execute('raw','1',{},fail)
        row=store.db.execute('SELECT status,next_retry_at,attempt_count FROM stage_results').fetchone()
        assert row['status']=='deferred' and row['next_retry_at'] and row['attempt_count']==1
        with pytest.raises(SourceUnavailable,match='rate limited') as error:
            StageExecutor(store,run).execute('raw','1',{},lambda:{})
        assert error.value.retryable and error.value.retry_at==row['next_retry_at']


def test_stale_worker_cannot_overwrite_a_new_owner_result(tmp_path):
    from vimfc.domain import digest
    path=tmp_path/'db'
    with PipelineStore(path) as store,PipelineStore(path) as other:
        run=store.create('2026-10-14T00:00:00Z',{})
        def stale_work():
            store.db.execute("UPDATE stage_results SET lease_until='2000-01-01T00:00:00Z'")
            StageExecutor(other,run).execute('raw','1',{},lambda:{'value':'new owner'})
            return {'value':'stale owner'}
        with pytest.raises(SourceUnavailable,match='lease'):
            StageExecutor(store,run).execute('raw','1',{},stale_work)
        assert store.result(run,'1','raw',digest({}))=={'value':'new owner'}
