import pytest
from vimfc.domain import Incompatible
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.storage.migrations import annotation_database


def test_run_config_is_immutable_and_stage_commit_rolls_back(tmp_path):
    with PipelineStore(tmp_path / 'pipeline.sqlite') as store:
        run = store.create('2026-10-14T00:00:00Z', {'version': 1})
        with pytest.raises(Incompatible):
            store.require_config(run, {'version': 2})
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.complete(run, '1', 'extract', 'dep', {'text': 'old'})
                raise RuntimeError('power loss')
        assert store.result(run, '1', 'extract', 'dep') is None
        store.complete(run, '1', 'extract', 'dep', {'text': 'new'})
        store.complete(run, '1', 'extract', 'dep', {'text': 'new'})
        assert store.result(run, '1', 'extract', 'dep') == {'text': 'new'}
        assert store.db.execute('SELECT count(*) FROM stage_results').fetchone()[0] == 1
        new = store.create('2026-10-14T00:00:00Z', {'version': 2}, reuse_run=run)
        assert new != run
        assert store.run(run)['config'] == {'version': 1}


def test_durable_blob_reuse_and_corruption(tmp_path):
    cache = RawCache(tmp_path)
    ref = cache.put('viwiki', 123, {'content': 'Tiếng Việt'})
    assert cache.get(ref) == {'content': 'Tiếng Việt'}
    assert cache.put('viwiki', 123, {'content': 'Tiếng Việt'}) == ref
    (tmp_path / ref['path']).write_bytes(b'broken')
    with pytest.raises(ValueError):
        cache.get(ref)


def test_annotation_migration_preserves_sessions_and_rejects_rebinding(tmp_path):
    path = tmp_path / 'annotations.sqlite'
    db = annotation_database(path, 'corpus-a')
    db.execute("INSERT INTO sessions VALUES ('session-a','corpus-a','2026-10-14T00:00:00Z')")
    db.commit()
    db.close()
    db = annotation_database(path, 'corpus-a')
    assert db.execute('SELECT session_id FROM sessions').fetchone()[0] == 'session-a'
    db.close()
    with pytest.raises(Incompatible):
        annotation_database(path, 'corpus-b')
