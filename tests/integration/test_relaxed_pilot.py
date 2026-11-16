import hashlib
import json
from pathlib import Path
import pytest
from vimfc.domain import Incompatible
from tests.integration.test_discovery_archive import fixture_archive


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_same_cohort_offline_replay_preserves_original_without_external_requests(tmp_path, config):
    from vimfc.pipeline.relaxed_pilot import evaluate_archive
    ctx = fixture_archive(tmp_path, config)
    before = sha(ctx['database'])
    result = evaluate_archive(ctx, expected_count=1)
    assert result['cohort_page_ids'] == [1] and result['snapshot'] == ctx['snapshot']
    assert result['metrics']['processed'] == 1
    assert result['api_requests'] == {'wikimedia': 0, 'other_llm': 0}
    assert result['pilot_accepted'] is False and sha(ctx['database']) == before


def test_invalid_historical_raw_is_blocked_instead_of_relaxed(tmp_path, config):
    from vimfc.pipeline.relaxed_pilot import evaluate_archive
    ctx = fixture_archive(tmp_path, config)
    next((Path(ctx['raw_cache']) / 'viwiki').rglob('*.json.gz')).write_bytes(b'corrupt')
    result = evaluate_archive(ctx, expected_count=1)
    assert result['metrics']['processed'] == 0 and result['metrics']['eligible'] == 0
    assert result['blockers'][0]['stage'] == 'source_or_dependency_validation'
    assert result['status'] == 'PARTIAL'
    assert result['baseline_metrics']['processed'] == 'NOT_AVAILABLE'


def test_wrong_cohort_or_snapshot_refused_before_evaluation(tmp_path, config):
    from vimfc.pipeline.relaxed_pilot import evaluate_archive
    ctx = fixture_archive(tmp_path, config)
    with pytest.raises(Incompatible): evaluate_archive(ctx, expected_count=1000)
    with pytest.raises(Incompatible): evaluate_archive({**ctx, 'snapshot': '2026-10-25T03:00:00Z'}, expected_count=1)


def test_uncached_title_match_is_never_invented_as_hyperlink_edge(tmp_path, config):
    from vimfc.pipeline.relaxed_archive import CheckedArchive
    ctx = fixture_archive(tmp_path, config)
    with CheckedArchive(ctx) as archive:
        result = archive.resolve('A')
        assert result['status'] == 'unresolved' and result['reason'] == 'no_archived_identity_resolution'


def test_new_output_must_not_overlap_source_tree(tmp_path, config):
    from vimfc.pipeline.relaxed_pilot import safe_output
    ctx = fixture_archive(tmp_path, config)
    with pytest.raises((ValueError, Incompatible)):
        safe_output(Path(ctx['raw_cache']) / 'new', [ctx['raw_cache']])


@pytest.mark.parametrize('field,value', [('revision_id', 999), ('raw_hash', 'a' * 64), ('page_id', 99),
                                        ('title', 'Fabricated'), ('article_kinds', ['disambiguation'])])
def test_saved_process_source_identity_cannot_override_checked_raw(tmp_path, config, field, value):
    import sqlite3
    from vimfc.domain import canonical, digest
    from vimfc.pipeline.relaxed_pilot import evaluate_archive
    ctx = fixture_archive(tmp_path, config)
    with sqlite3.connect(ctx['database']) as db:
        row = db.execute("SELECT rowid,result_json FROM stage_results WHERE stage='process'").fetchone()
        record = json.loads(row[1]); record[field] = value
        db.execute('UPDATE stage_results SET result_json=?,result_hash=? WHERE rowid=?', (canonical(record), digest(record), row[0]))
    result = evaluate_archive(ctx, expected_count=1)
    assert result['metrics']['processed'] == 0 and result['status'] == 'PARTIAL'


def test_preservation_detects_change_and_never_reads_dotenv(tmp_path):
    from vimfc.pipeline.relaxed_preservation import snapshot_files, compare_files
    (tmp_path / '.env').write_text('DO_NOT_READ', encoding='utf-8')
    data = tmp_path / 'source'; data.write_text('old', encoding='utf-8')
    before = snapshot_files([tmp_path])
    assert list(before) == [str(data.resolve())]
    data.write_text('new', encoding='utf-8')
    assert compare_files(before, snapshot_files([tmp_path]))['passed'] is False


def test_baseline_graph_corruption_reports_partial_without_losing_safe_article(tmp_path, config):
    import sqlite3
    from vimfc.domain import canonical, digest
    from vimfc.pipeline.relaxed_pilot import evaluate_archive
    ctx = fixture_archive(tmp_path, config)
    with sqlite3.connect(ctx['database']) as db:
        row = db.execute("SELECT rowid,result_json FROM stage_results WHERE stage='process'").fetchone()
        record = json.loads(row[1])
        record['extraction']['links'][0]['target'] = {'status': 'resolved', 'page_id': 99, 'raw_hash': 'a' * 64}
        db.execute('UPDATE stage_results SET result_json=?,result_hash=? WHERE rowid=?', (canonical(record), digest(record), row[0]))
    result = evaluate_archive(ctx, expected_count=1)
    assert result['metrics']['processed'] == 1 and result['status'] == 'PARTIAL'
    assert result['baseline_metrics']['eligible_edges'] == 'NOT_AVAILABLE'


def test_source_only_intake_checks_registry_and_does_not_invent_primary(tmp_path, config):
    import sqlite3
    from vimfc.pipeline.relaxed_archive import CheckedArchive
    ctx = fixture_archive(tmp_path, config)
    with sqlite3.connect(ctx['database']) as db:
        db.execute('DELETE FROM article_manifests')
    class Refusal:
        def check(self, ref): raise Incompatible('Exact Wikipedia revision refused')
        def close(self): pass
    with CheckedArchive(ctx) as archive:
        admitted = archive.source_only(1)
        assert admitted['classification']['primary_entity_type'] is None
        archive.registry = Refusal()
        with pytest.raises(Incompatible, match='refused'): archive.source_only(1)


def test_relaxed_config_records_the_actual_algorithm(config):
    from vimfc.config import load_config
    from vimfc.pipeline.relaxed_pilot import relaxed_rules
    from vimfc.pipeline.hyperlink_rank import SELECTION_WEIGHTS
    cfg = load_config('configs/relaxed/pipeline.json')
    assert cfg['extraction'] == relaxed_rules(config)
    assert cfg['ranking']['selection_weights'] == SELECTION_WEIGHTS
    assert cfg['classification'] == config['classification']


@pytest.mark.parametrize('mutation', ['none', 'different_lookup_horizon', 'selected_page', 'cache_revision', 'final_redirect'])
def test_cached_graph_identity_proof_requires_exact_ref_and_closed_redirect(tmp_path, config, mutation):
    import sqlite3
    from vimfc.domain import canonical, digest
    from vimfc.pipeline.relaxed_archive import CheckedArchive
    from vimfc.storage.raw_cache import RawCache
    ctx = fixture_archive(tmp_path, config)
    with sqlite3.connect(ctx['database']) as db:
        acquired = json.loads(db.execute("SELECT result_json FROM stage_results WHERE stage='acquire'").fetchone()[0])
        ref = {**acquired['ref'], 'lookup_timestamp': '2026-10-30T00:00:00Z'}
        raw = RawCache(ctx['raw_cache']).get(acquired['cache_ref'])
        if mutation == 'final_redirect':
            raw['content'] = '#REDIRECT [[B]]'
            raw['source_sha1'] = hashlib.sha1(raw['content'].encode()).hexdigest()
            raw['local_sha256'] = hashlib.sha256(raw['content'].encode()).hexdigest()
            acquired['cache_ref'] = RawCache(ctx['raw_cache']).put('viwiki', ref['revision_id'], raw)
        if mutation == 'selected_page': ref['page_id'] = 99
        if mutation == 'cache_revision': ref['revision_id'] = 999
        db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', '1', ctx['snapshot'], canonical(ref)))
        db.execute('INSERT INTO source_revisions VALUES (?,?,?)', ('viwiki', ref['revision_id'], canonical({'cache_ref': acquired['cache_ref']})))
        db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (ctx['snapshot'], '__move_index__:' + ref['lookup_timestamp'], canonical([])))
        resolved = {'status': 'resolved', 'page_id': 1, 'title': 'A', 'revision_id': ref['revision_id'],
                    'revision_timestamp': ref['revision_timestamp'], 'raw_hash': raw['local_sha256'],
                    'content': raw['content'], 'snapshot': ctx['snapshot'], 'chain': [],
                    'proof': {'kind': 'historical_revision_and_move_log', 'move_index_hash': digest([])}}
        if mutation == 'different_lookup_horizon':
            other_moves = [{'timestamp': '2026-10-29T00:00:00Z', 'pageid': 999, 'title': 'Other', 'params': {'target_title': 'New'}}]
            db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (ctx['snapshot'], '__move_index__:2026-10-31T00:00:00Z', canonical(other_moves)))
            resolved['proof']['move_index_hash'] = digest(other_moves)
        db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (ctx['snapshot'], 'A', canonical(resolved)))
    with CheckedArchive(ctx) as archive:
        if mutation in ('none', 'different_lookup_horizon'): assert archive.resolve('A')['status'] == 'resolved'
        else:
            with pytest.raises(Incompatible): archive.resolve('A')


def test_final_audit_seal_covers_receipts_and_refuses_changed_report(tmp_path, config):
    from vimfc.pipeline.relaxed_pilot import evaluate_archive, write_report, seal_audit
    ctx = fixture_archive(tmp_path, config)
    result = evaluate_archive(ctx, expected_count=1)
    output = tmp_path / 'new-results'
    write_report(result, output)
    (output / 'execution-receipt.json').write_text('{}', encoding='utf-8')
    (output / 'preservation.json').write_text('{}', encoding='utf-8')
    seal = seal_audit(output)
    assert {'execution-receipt.json', 'preservation.json', 'manifest.json'} <= set(seal['files'])
    second = tmp_path / 'tampered'
    write_report(result, second)
    (second / 'metrics.json').write_text('{}', encoding='utf-8')
    with pytest.raises(Incompatible): seal_audit(second)
