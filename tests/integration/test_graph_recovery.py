import hashlib
import json
from pathlib import Path
import httpx
import pytest
from vimfc.domain import Incompatible, canonical
from tests.integration.test_discovery_archive import fixture_archive


def response(request, *, name='Target', ns=0, content='Target là một thành phố.', bad_hash=False, future=False):
    params = request.url.params
    stamp = '2027-01-01T00:00:00Z' if future else '2026-10-13T00:00:00Z'
    revision = {'revid': 700, 'timestamp': stamp}
    if 'content' in params.get('rvprop', ''):
        revision.update(sha1='0'*40 if bad_hash else hashlib.sha1(content.encode()).hexdigest(),
                        slots={'main': {'content': content, 'contentmodel': 'wikitext'}})
    if params.get('list') == 'logevents':
        return httpx.Response(200, json={'query': {'logevents': []}})
    return httpx.Response(200, json={'curtimestamp': '2026-10-30T00:00:00Z',
        'query': {'pages': [{'pageid': 99, 'title': name, 'ns': ns, 'revisions': [revision]}]}})


def test_recovery_resolves_outside_cohort_and_replays_without_duplicate_http(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    calls = []
    def wire(req): calls.append(req); return response(req)
    old_sha = hashlib.sha256(Path(ctx['database']).read_bytes()).hexdigest()
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        r = run.resolve('Target', {1}, allow_live=True)
        assert r['state'] == 'RESOLVED_OUTSIDE_COHORT' and r['target']['page_id'] == 99
        count = len(calls)
        assert run.resolve('Target', {1}, allow_live=True) == r and len(calls) == count
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(lambda req: pytest.fail('No repeat HTTP')), sleep=lambda _: None) as run:
        assert run.resolve('Target', {1}, allow_live=True)['state'] == 'RESOLVED_OUTSIDE_COHORT'
    assert hashlib.sha256(Path(ctx['database']).read_bytes()).hexdigest() == old_sha


@pytest.mark.parametrize('mode', ['hash', 'future', 'namespace'])
def test_unverified_targets_never_become_edges(tmp_path, config, mode):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    def wire(req): return response(req, name='Help:Target' if mode=='namespace' else 'Target',
                                    ns=12 if mode=='namespace' else 0, bad_hash=mode=='hash', future=mode=='future')
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        r = run.resolve('Help:Target' if mode=='namespace' else 'Target', {99}, allow_live=True)
        assert r['state'] in ('SOURCE_INTEGRITY_BLOCKED', 'UNRESOLVED')
        assert r['target'] is None


def test_cache_only_miss_is_not_a_title_inferred_edge(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(lambda req: pytest.fail('Offline')), sleep=lambda _: None) as run:
        r = run.resolve('Target', {99}, allow_live=False)
        assert r['state'] == 'UNRESOLVED' and run.summary()['physical_attempts'] == 0


def test_request_cap_survives_restart_and_configuration_cannot_reset_it(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    limits = {'max_requests': 1, 'max_runtime_seconds': 60, 'max_targets': 2, 'min_interval_seconds': 0}
    calls = []
    def wire(req): calls.append(req); return response(req)
    out = tmp_path/'recovery'
    with GraphRecovery(ctx, out, limits=limits, transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=True)['state'] == 'UNRESOLVED'
        assert len(calls) == 1
    with GraphRecovery(ctx, out, limits=limits, transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        run.resolve('Other', {99}, allow_live=True)
        assert len(calls) == 1 and run.summary()['physical_attempts'] == 1
    with pytest.raises(Incompatible): GraphRecovery(ctx, out, limits={**limits, 'max_requests': 2})


@pytest.mark.parametrize('status', [429, 503])
def test_source_stop_is_durable_and_never_automatically_retried(tmp_path, config, status):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    calls = []
    def wire(req):
        calls.append(req)
        return httpx.Response(status, headers={'Retry-After': '60'}, json={})
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        result = run.resolve('Target', {99}, allow_live=True)
        assert result['target'] is None and result['state'] == 'UNRESOLVED'
        assert run.resolve('Other', {99}, allow_live=True)['target'] is None
        assert len(calls) == 1 and run.summary()['online_stopped']
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        run.resolve('Third', {99}, allow_live=True)
        assert len(calls) == 1


def test_negative_identity_cache_is_retried_only_in_clone_and_retained_as_prior(tmp_path, config):
    import sqlite3
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    prior = {'status': 'unresolved', 'reason': 'ambiguous_or_missing_owner', 'snapshot': ctx['snapshot']}
    with sqlite3.connect(ctx['database']) as db:
        db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (ctx['snapshot'], 'Target', canonical(prior)))
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        result = run.resolve('Target', {99}, allow_live=True)
        assert result['state'] == 'RESOLVED_VALID'
        assert result['prior_resolution'] == prior
    with sqlite3.connect(ctx['database']) as db:
        assert json.loads(db.execute('SELECT record_json FROM identity_resolutions WHERE lookup_title=?', ('Target',)).fetchone()[0]) == prior


def test_replay_revalidates_raw_artifacts_and_checkpoint_checksums(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        result = run.resolve('Target', {99}, allow_live=True)
        assert result['state'] == 'RESOLVED_VALID'
    raw = next((output/'raw'/'viwiki'/'700').glob('*.json.gz'))
    raw.write_bytes(b'corrupt')
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(lambda req: pytest.fail('No refetch')), sleep=lambda _: None) as run:
        result = run.resolve('Target', {99}, allow_live=True)
        assert result['state'] == 'SOURCE_INTEGRITY_BLOCKED' and result['target'] is None


def test_checkpoint_scope_is_not_reused_for_a_different_source(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None):
        pass
    with pytest.raises(Incompatible):
        GraphRecovery({**ctx, 'snapshot': '2026-10-01T00:00:00Z'}, output)


def test_missing_identity_can_be_reconstructed_entirely_from_checked_cache(tmp_path, config):
    import sqlite3
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=True)['state'] == 'RESOLVED_VALID'
    # Simulate a source cache that acquired refs/raw/moves but did not publish
    # an identity resolution. Build another independent overlay over that source.
    ctx2 = {**ctx, 'database': str(output/'source.sqlite')}
    with sqlite3.connect(ctx2['database']) as db:
        db.execute('DELETE FROM identity_resolutions WHERE lookup_title=?', ('Target',))
    ctx2['raw_cache'] = str(output/'raw')
    with GraphRecovery(ctx2, tmp_path/'cache-only', transport=httpx.MockTransport(lambda req: pytest.fail('No wire')), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=False)['state'] == 'RESOLVED_VALID'
        assert run.summary()['physical_attempts'] == 0


def test_generic_missing_owner_diagnostic_does_not_claim_proven_ambiguity(tmp_path, config):
    import sqlite3
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    with sqlite3.connect(ctx['database']) as db:
        db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)',
                   (ctx['snapshot'], 'Target', canonical({'status': 'unresolved', 'reason': 'ambiguous_or_missing_owner'})))
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=False)['state'] == 'UNRESOLVED'


def test_output_inside_original_source_directory_is_refused(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    with pytest.raises(Incompatible):
        GraphRecovery(ctx, tmp_path/'source'/'new-overlay', transport=httpx.MockTransport(response))


def test_initial_ref_scope_is_revalidated_on_replay(tmp_path, config):
    import sqlite3
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=True)['target']
    with sqlite3.connect(output/'source.sqlite') as db:
        record = json.loads(db.execute('SELECT record_json FROM snapshot_revision_refs WHERE identity=?', ('Target',)).fetchone()[0])
        record['snapshot'] = '2026-01-01T00:00:00Z'
        db.execute('UPDATE snapshot_revision_refs SET record_json=? WHERE identity=?', (canonical(record), 'Target'))
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(lambda req: pytest.fail('No wire')), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=True)['state'] == 'SOURCE_INTEGRITY_BLOCKED'


def test_later_redirect_lookup_horizon_cannot_hide_unverified_owner_hint(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    def wire(req):
        params = req.url.params
        if params.get('list') == 'logevents':
            moves = [] if params['lestart'] == '2026-10-30T00:00:00Z' else [
                {'pageid': 100, 'title': 'Target', 'timestamp': '2026-10-31T00:00:00Z',
                 'params': {'target_title': 'Other'}}]
            return httpx.Response(200, json={'query': {'logevents': moves}})
        redirect = params.get('titles') == 'Start' or params.get('pageids') == '90' or params.get('revids') == '690'
        name, page, rev = ('Start', 90, 690) if redirect else ('Target', 99, 700)
        content = '#REDIRECT [[Target]]' if redirect else 'Target is a city.'
        revision = {'revid': rev, 'timestamp': '2026-10-13T00:00:00Z'}
        if 'content' in params.get('rvprop', ''):
            revision.update(sha1=hashlib.sha1(content.encode()).hexdigest(), slots={'main': {'content': content, 'contentmodel': 'wikitext'}})
        return httpx.Response(200, json={'curtimestamp': '2026-10-30T00:00:00Z' if redirect else '2026-11-01T00:00:00Z',
            'query': {'pages': [{'pageid': page, 'title': name, 'ns': 0, 'revisions': [revision]}]}})
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        result = run.resolve('Start', {99}, allow_live=True)
        assert result['state'] == 'UNRESOLVED' and result['target'] is None


def test_namespace_receipt_for_current_title_cannot_certify_historical_prefixed_title(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    def wire(req):
        if req.url.params.get('list') == 'logevents':
            return httpx.Response(200, json={'query': {'logevents': [
                {'pageid': 99, 'title': 'Historical:Target', 'timestamp': '2026-10-29T00:00:00Z',
                 'params': {'target_title': 'Target'}}]}})
        return response(req)
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(wire), sleep=lambda _: None) as run:
        result = run.resolve('Historical:Target', {99}, allow_live=True)
        assert result['state'] == 'UNRESOLVED' and result['target'] is None


def test_resumed_raw_overlay_junction_cannot_alias_original_source(tmp_path, config):
    import os
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None):
        pass
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(ctx['raw_cache'], str(output/'raw'))
    else:
        (output/'raw').symlink_to(ctx['raw_cache'], target_is_directory=True)
    old_sha = hashlib.sha256(Path(ctx['database']).read_bytes()).hexdigest()
    with pytest.raises(Incompatible):
        GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None)
    assert hashlib.sha256(Path(ctx['database']).read_bytes()).hexdigest() == old_sha


def test_resumed_database_symlink_is_refused_before_writable_open(tmp_path, config, monkeypatch):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None):
        pass
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == output/'source.sqlite' or original(p))
    with pytest.raises(Incompatible):
        GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None)


def test_checkpoint_publish_retries_transient_windows_sharing_failure_without_http(tmp_path, config, monkeypatch):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    sleeps, calls = [], []
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP')),
                       sleep=sleeps.append) as run:
        original = Path.replace
        def sharing_failure(path, target):
            if path == output/'checkpoint.pending':
                calls.append(path)
                if len(calls) <= 3:
                    error = PermissionError('Temporary Windows checkpoint sharing violation')
                    error.winerror = 5
                    raise error
            return original(path, target)
        monkeypatch.setattr(Path, 'replace', sharing_failure)
        result = run.resolve('Target', {99}, allow_live=False)
        assert result['state'] == 'UNRESOLVED' and run.summary()['physical_attempts'] == 0
        persisted = json.loads((output/'checkpoint.json').read_text(encoding='utf-8'))['checkpoint']
        assert persisted['results']['Target']['result'] == result
        assert len(calls) == 4 and len(sleeps) == 3 and sum(sleeps) < 5


def test_persistent_checkpoint_sharing_failure_stops_bounded_and_keeps_previous_checkpoint(tmp_path, config, monkeypatch):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    sleeps, calls = [], []
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP')),
                       sleep=sleeps.append) as run:
        before = (output/'checkpoint.json').read_bytes()
        original = Path.replace
        def sharing_failure(path, target):
            if path == output/'checkpoint.pending':
                calls.append(path)
                error = PermissionError('Persistent Windows checkpoint sharing violation')
                error.winerror = 32
                raise error
            return original(path, target)
        monkeypatch.setattr(Path, 'replace', sharing_failure)
        with pytest.raises(PermissionError):
            run.resolve('Target', {99}, allow_live=False)
        assert 2 <= len(calls) <= 10 and len(sleeps) == len(calls) - 1 and sum(sleeps) < 5
        assert (output/'checkpoint.json').read_bytes() == before
        pending = json.loads((output/'checkpoint.pending').read_text(encoding='utf-8'))['checkpoint']
        assert pending['results']['Target']['result']['state'] == 'UNRESOLVED'
        assert run.summary()['physical_attempts'] == 0


@pytest.mark.parametrize('event', [
    {'timestamp': '2026-10-29T00:00:00Z', 'actionhidden': True},
    {'pageid': 99, 'title': 'Old', 'params': {'target_title': 'Target'}},
    {'pageid': 99, 'logpage': 100, 'title': 'Old', 'timestamp': '2026-10-29T00:00:00Z', 'params': {'target_title': 'Target'}},
])
def test_incomplete_cached_move_index_is_explicitly_unresolved_without_wire(tmp_path, config, event):
    import sqlite3
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    output = tmp_path/'recovery'
    with GraphRecovery(ctx, output, transport=httpx.MockTransport(response), sleep=lambda _: None) as run:
        assert run.resolve('Target', {99}, allow_live=True)['target']
    # Construct an independent source fixture with an incomplete archived move
    # index and no published target decision; original Pilot inputs are untouched.
    with sqlite3.connect(output/'source.sqlite') as db:
        db.execute('UPDATE identity_resolutions SET record_json=? WHERE lookup_title LIKE ?',
                   (canonical([event]), '__move_index__:%'))
        db.execute('DELETE FROM identity_resolutions WHERE lookup_title=?', ('Target',))
    ctx2 = {**ctx, 'database': str(output/'source.sqlite'), 'raw_cache': str(output/'raw')}
    with GraphRecovery(ctx2, tmp_path/'checked', transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP')), sleep=lambda _: None) as run:
        result = run.resolve('Target', {99}, allow_live=False)
        assert result['state'] == 'UNRESOLVED' and result['target'] is None
        assert result['reason'] == 'historical_move_proof_incomplete'
        assert run.summary()['physical_attempts'] == 0


def test_numeric_title_is_never_dispatched_as_a_page_id(tmp_path, config):
    from vimfc.pipeline.graph_recovery import GraphRecovery
    ctx = fixture_archive(tmp_path/'source', config)
    with GraphRecovery(ctx, tmp_path/'recovery', transport=httpx.MockTransport(lambda req: pytest.fail('Numeric title must not reach source')), sleep=lambda _: None) as run:
        result = run.resolve('12345', {12345}, allow_live=True)
        assert result['state'] == 'UNRESOLVED' and result['target'] is None
        assert result['reason'] == 'unsupported_numeric_title'
        assert run.summary()['physical_attempts'] == 0 and run.summary()['live_target_attempts'] == 0
