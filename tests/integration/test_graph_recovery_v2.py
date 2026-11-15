"""Versioned historical resolution does not conflate titles with IDs."""
import hashlib
from pathlib import Path
import httpx
import pytest

from tests.integration.test_discovery_archive import fixture_archive
from tests.integration.test_graph_recovery import response
from vimfc.domain import digest


def fixture_hidden_authority(snapshot, horizon, raw, public):
    attestation = {'version': 'historical-hidden-move-coverage-1',
                   'snapshot': snapshot, 'horizon': horizon, 'raw_events_hash': digest(raw),
                   'public_coverage_manifest_id': public['manifest_id'], 'complete': True,
                   'authoritative': True,
                   'evidence': [{'kind': 'test-fixture', 'reference': 'mock authority', 'sha256': 'a' * 64}]}
    attestation['manifest_id'] = digest(attestation)
    return attestation


def test_versioned_recovery_resolves_numeric_title_and_replays(tmp_path, config):
    from vimfc.pipeline.graph_recovery_v2 import GraphRecoveryV2
    context = fixture_archive(tmp_path / 'source', config)
    before = hashlib.sha256(Path(context['database']).read_bytes()).hexdigest()
    calls = []
    def wire(req):
        calls.append(dict(req.url.params))
        return response(req, name='12345')
    output = tmp_path / 'result'
    with GraphRecoveryV2(context, output, transport=httpx.MockTransport(wire), sleep=lambda _: None) as recovery:
        recovery.identities.hidden_coverage_verifier = fixture_hidden_authority
        result = recovery.resolve('12345', {99}, allow_live=True)
        assert result['state'] == 'RESOLVED_VALID'
        assert result['target']['page_id'] == 99
        assert any(q.get('titles') == '12345' for q in calls)
        assert not any(q.get('pageids') == '12345' for q in calls)
    with GraphRecoveryV2(context, output, transport=httpx.MockTransport(lambda req: pytest.fail('Unexpected request')), sleep=lambda _: None) as recovery:
        recovery.identities.hidden_coverage_verifier = fixture_hidden_authority
        assert recovery.resolve('12345', {99}, allow_live=False)['target']['page_id'] == 99
    assert hashlib.sha256(Path(context['database']).read_bytes()).hexdigest() == before


def test_hidden_move_remains_unknown_without_wire(tmp_path, config):
    from vimfc.pipeline.graph_recovery_v2 import GraphRecoveryV2
    context = fixture_archive(tmp_path / 'source', config)
    def wire(req):
        if req.url.params.get('list') == 'logevents':
            return httpx.Response(200, json={'query': {'logevents': [
                {'logid': 7, 'actionhidden': True, 'timestamp': '2026-10-20T00:00:00Z'}]}})
        return response(req)
    with GraphRecoveryV2(context, tmp_path / 'result', transport=httpx.MockTransport(wire), sleep=lambda _: None) as recovery:
        outcome = recovery.resolve('Target', {99}, allow_live=True)
        assert outcome['state'] == 'UNRESOLVED' and outcome['target'] is None


def test_selector_cache_rejects_cross_kind_envelope(tmp_path, config):
    from vimfc.pipeline.graph_recovery_v2 import GraphRecoveryV2
    from vimfc.source.explicit_revisions import selector_key
    from vimfc.domain import Incompatible, SourceUnavailable
    context = fixture_archive(tmp_path / 'source', config)
    with GraphRecoveryV2(context, tmp_path / 'result', transport=httpx.MockTransport(response), sleep=lambda _: None) as recovery:
        recovery.online = True
        recovery.source.select_page_revision('viwiki', 99, context['snapshot'])
        recovery.online = False
        row = recovery.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
            ('viwiki', selector_key('page_id', 99), context['snapshot'])).fetchone()
        recovery.store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)',
            ('viwiki', selector_key('title', '99'), context['snapshot'], row[0]))
        with pytest.raises((Incompatible, SourceUnavailable)):
            recovery._selected_ref('title', '99')


def test_missing_hidden_coverage_is_explicit_and_never_calls_source(tmp_path, config):
    from vimfc.pipeline.graph_recovery_v2 import GraphRecoveryV2
    context = fixture_archive(tmp_path / 'source', config)
    with GraphRecoveryV2(context, tmp_path / 'result',
                         transport=httpx.MockTransport(lambda req: pytest.fail('Unexpected request')), sleep=lambda _: None) as recovery:
        result = recovery.resolve('12345', {99}, allow_live=True)
        assert result['state'] == 'UNRESOLVED'
        assert result['reason'] == 'historical_hidden_move_coverage_unverified'
        assert recovery.summary()['physical_attempts'] == 0
