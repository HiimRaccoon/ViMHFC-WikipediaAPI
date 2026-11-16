import json
from pathlib import Path
import pytest

from vimfc.domain import Incompatible
from vimfc.pipeline.runner import Builder
from vimfc.pipeline.relaxed_archive import CheckedArchive
from tests.integration.test_discovery_archive import fixture_archive


def setup(tmp_path, config, monkeypatch):
    context = fixture_archive(tmp_path / 'source', config)
    monkeypatch.setattr('vimfc.pipeline.annotation_builder.SNAPSHOT', context['snapshot'])
    return context, tmp_path / 'output'


def test_builder_checked_intake_manifest_and_resume_without_reprocessing(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    result = Builder.annotation_collection(context, output, target=1)
    assert result['manifest']['version'] == 'annotation-collection-wikigraph-1'
    assert result['articles'][0]['quality']['quality_version'] == 'quality-relaxed-1'
    assert result['articles'][0]['classification']['primary_entity_type'] is None
    assert result['metrics']['structural_edges_are_factual_evidence'] is False
    monkeypatch.setattr(CheckedArchive, 'article_source', lambda *a: pytest.fail('reprocessed source'))
    assert Builder.annotation_collection(context, output, target=1) == result
    with pytest.raises(Incompatible, match='fingerprint'):
        Builder.annotation_collection(context, output, target=2)


def test_changed_source_rejects_resume(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    Builder.annotation_collection(context, output, target=1)
    (Path(context['raw_cache']) / 'unexpected').write_bytes(b'changed source')
    with pytest.raises(Incompatible, match='fingerprint'):
        Builder.annotation_collection(context, output, target=1)


def test_corrupt_source_blocks_article_and_checkpoint_tamper_refused(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    next(Path(context['raw_cache']).rglob('*.gz')).write_bytes(b'bad')
    result = Builder.annotation_collection(context, output, target=1)
    assert result['blockers'] and result['metrics']['eligible_articles'] == 0
    path = output / 'checkpoint.json'
    saved = json.loads(path.read_text(encoding='utf-8')); saved['state']['result']['manifest']['selected_page_ids'] = [999]
    path.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises(Incompatible, match='fingerprint'):
        Builder.annotation_collection(context, output, target=1)


def test_output_overlap_refused(tmp_path, config, monkeypatch):
    context, _ = setup(tmp_path, config, monkeypatch)
    with pytest.raises(Incompatible, match='overlaps'):
        Builder.annotation_collection(context, Path(context['raw_cache']) / 'new')


def test_interrupted_before_selection_reuses_checked_article(tmp_path, config, monkeypatch):
    import vimfc.pipeline.annotation_builder as module
    context, output = setup(tmp_path, config, monkeypatch)
    select = module.select_articles
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(module, 'select_articles', stop)
    with pytest.raises(KeyboardInterrupt):
        Builder.annotation_collection(context, output, target=1)
    monkeypatch.setattr(module, 'select_articles', select)
    monkeypatch.setattr(CheckedArchive, 'article_source', lambda *a: pytest.fail('reprocessed intake'))
    result = Builder.annotation_collection(context, output, target=1)
    assert len(result['articles']) == 1
    assert len(set(result['selection']['page_ids'])) == len(result['selection']['page_ids'])


def test_windows_temporary_replace_lock_retries_without_repeating_intake(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    replace = Path.replace
    attempts = []
    def locked_once(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            error = PermissionError('temporary Windows lock'); error.winerror = 32
            raise error
        return replace(source, destination)
    monkeypatch.setattr(Path, 'replace', locked_once)
    result = Builder.annotation_collection(context, output, target=1)
    assert len(result['articles']) == 1
    assert len(attempts) >= 2 and attempts[0] == attempts[1]


def test_single_writer_refuses_nested_builder(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    original = CheckedArchive.article_source
    def intake(archive, page):
        with pytest.raises(Incompatible, match='writer'):
            Builder.annotation_collection(context, output, target=1)
        return original(archive, page)
    monkeypatch.setattr(CheckedArchive, 'article_source', intake)
    result = Builder.annotation_collection(context, output, target=1)
    assert len(result['articles']) == 1


@pytest.mark.parametrize('winerror,attempts', [(5, 8), (32, 8), (33, 8), (None, 1)])
def test_persistent_replace_failure_preserves_committed_checkpoint_and_pending(tmp_path, config, monkeypatch, winerror, attempts):
    import vimfc.pipeline.annotation_builder as module
    context, output = setup(tmp_path, config, monkeypatch)
    expected = Builder.annotation_collection(context, output, target=1)
    path = output / 'checkpoint.json'
    saved = json.loads(path.read_text(encoding='utf-8')); saved['state'].pop('result')
    from vimfc.domain import digest
    saved['sha256'] = digest(saved['state'])
    path.write_text(json.dumps(saved), encoding='utf-8')
    before = path.read_bytes()
    calls, waits = [], []
    original = Path.replace
    def denied(source, destination):
        calls.append(source)
        error = PermissionError('persistent access denied')
        if winerror is not None:
            error.winerror = winerror
        raise error
    monkeypatch.setattr(Path, 'replace', denied)
    monkeypatch.setattr(module.time, 'sleep', waits.append)
    with pytest.raises(PermissionError):
        Builder.annotation_collection(context, output, target=1)
    assert len(calls) == attempts and len(waits) == attempts - 1
    assert sum(waits) <= 2.75 and path.read_bytes() == before
    pending = calls[-1]
    envelope = json.loads(pending.read_text(encoding='utf-8'))
    assert envelope['sha256'] == digest(envelope['state'])
    monkeypatch.setattr(Path, 'replace', original)
    monkeypatch.setattr(CheckedArchive, 'article_source', lambda *a: pytest.fail('valid intake repeated'))
    assert Builder.annotation_collection(context, output, target=1) == expected
    assert pending.exists()


def test_legacy_temp_is_not_promoted_or_overwritten(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    expected = Builder.annotation_collection(context, output, target=1)
    temp = output / 'checkpoint.tmp'
    temp.write_bytes(b'not an authenticated commit')
    before = temp.read_bytes()
    assert Builder.annotation_collection(context, output, target=1) == expected
    assert temp.read_bytes() == before
    (output / 'checkpoint.json').write_text('broken', encoding='utf-8')
    with pytest.raises((ValueError, Incompatible)):
        Builder.annotation_collection(context, output, target=1)


def test_interrupt_during_replace_keeps_old_state_and_releases_writer(tmp_path, config, monkeypatch):
    context, output = setup(tmp_path, config, monkeypatch)
    expected = Builder.annotation_collection(context, output, target=1)
    path = output / 'checkpoint.json'
    envelope = json.loads(path.read_text(encoding='utf-8'))
    envelope['state'].pop('result')
    from vimfc.domain import digest
    envelope['sha256'] = digest(envelope['state'])
    path.write_text(json.dumps(envelope), encoding='utf-8')
    before = path.read_bytes(); original = Path.replace
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(Path, 'replace', interrupt)
    with pytest.raises(KeyboardInterrupt):
        Builder.annotation_collection(context, output, target=1)
    assert path.read_bytes() == before and list(output.glob('*.pending'))
    monkeypatch.setattr(Path, 'replace', original)
    assert Builder.annotation_collection(context, output, target=1) == expected
