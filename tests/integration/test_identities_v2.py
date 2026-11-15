import hashlib
import httpx
import pytest

from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAP = '2026-10-25T03:00:00Z'


def verified_fixture_coverage(snapshot, horizon, events):
    from vimfc.domain import digest
    receipt = {'version': 'historical-move-coverage-1', 'snapshot': snapshot, 'horizon': horizon,
               'raw_events_hash': digest(events), 'complete': True,
               'interval': {'start': horizon, 'end': snapshot, 'direction': 'older'},
               'pages': [{'request_continue': {}, 'response_continue': None,
                          'response_sha256': digest({'query': {'logevents': events}}),
                          'events_hash': digest(events)}]}
    return {**receipt, 'manifest_id': digest(receipt)}


def verified_fixture_hidden_coverage(snapshot, horizon, events, public):
    from vimfc.domain import digest
    receipt = {'version': 'historical-hidden-move-coverage-1', 'snapshot': snapshot, 'horizon': horizon,
               'raw_events_hash': digest(events), 'public_coverage_manifest_id': public['manifest_id'],
               'complete': True, 'authoritative': True,
               'evidence': [{'kind': 'trusted_mock_fixture', 'reference': 'complete-fixture-history',
                             'sha256': digest(events)}]}
    return {**receipt, 'manifest_id': digest(receipt)}


def test_numeric_redirect_target_uses_title_and_does_not_alias_equal_digit_page(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2, resolution_key
    calls = []
    def wire(req):
        p = dict(req.url.params); calls.append(p)
        if p.get('list') == 'logevents':
            return httpx.Response(200, json={'query': {'logevents': []}})
        selector = p.get('titles') or p.get('pageids') or p.get('revids')
        page, title, rev, content = ((90, 'Start', 690, '#REDIRECT [[123]]') if selector in ('Start', '90', '690') else
                                    (123, 'Different', 1123, 'Different is a city.') if 'pageids' in p else
                                    (456, '123', 1456, '123 is a year.'))
        r = {'revid': rev, 'timestamp': '2026-10-13T00:00:00Z'}
        if 'content' in p.get('rvprop', ''):
            r.update(sha1=hashlib.sha1(content.encode()).hexdigest(), slots={'main': {'content': content, 'contentmodel': 'wikitext'}})
        return httpx.Response(200, json={'curtimestamp': '2026-10-30T00:00:00Z', 'query': {'pages': [{'pageid': page, 'title': title, 'revisions': [r]}]}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(wire)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        identities = HistoricalIdentitiesV2(source, SNAP, coverage_verifier=verified_fixture_coverage,
                                             hidden_coverage_verifier=verified_fixture_hidden_coverage,
                                             should_acquire=lambda: True)
        result = identities.resolve('Start')
        assert result['status'] == 'resolved' and result['title'] == '123' and result['page_id'] == 456
        assert result['chain'][0]['target'] == '123'
        assert source.select_page_revision('viwiki', 123, SNAP).title == 'Different'
        assert any(p.get('titles') == '123' for p in calls)
        assert store.db.execute('SELECT count(*) FROM identity_resolutions WHERE lookup_title=?', ('Start',)).fetchone()[0] == 0
        assert store.db.execute('SELECT count(*) FROM identity_resolutions WHERE lookup_title=?', (resolution_key('Start'),)).fetchone()[0] == 1
        old_count = len(calls)
        assert identities.resolve('Start') == result and len(calls) == old_count
        from vimfc.domain import SourceUnavailable
        with pytest.raises(SourceUnavailable) as error:
            HistoricalIdentitiesV2(source, SNAP).resolve('Start')
        assert error.value.code == 'historical_hidden_move_coverage_unverified'


def test_move_index_uses_proven_logpage_and_preserves_original_digest(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import digest
    event = {'logid': 5, 'logpage': 90, 'pageid': 999, 'title': 'Old',
             'timestamp': '2026-10-29T00:00:00Z', 'params': {'target_title': 'New'}}
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'query': {'logevents': [event]}}))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        identities = HistoricalIdentitiesV2(source, SNAP, modern_logpage_verified=True,
                                             coverage_verifier=verified_fixture_coverage,
                                             hidden_coverage_verifier=verified_fixture_hidden_coverage,
                                             should_acquire=lambda: True)
        moves = identities._move_index('2026-10-30T00:00:00Z')
        assert moves[0]['pageid'] == 90
        assert identities.move_proofs['2026-10-30T00:00:00Z']['raw_events_hash'] == digest([event])
        assert identities.moves['2026-10-30T00:00:00Z'][0]['pageid'] == 999


def test_cached_move_index_without_interval_receipts_is_not_complete(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import SourceUnavailable, canonical
    horizon = '2026-10-30T00:00:00Z'
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP'))) as client:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, '__move_index__:' + horizon, canonical([])))
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP)
        with pytest.raises(SourceUnavailable) as error:
            identities._move_index(horizon)
        assert error.value.code == 'historical_hidden_move_coverage_unverified'


def test_public_coverage_alone_cannot_establish_absence_of_restricted_moves(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import SourceUnavailable, canonical
    horizon = '2026-10-30T00:00:00Z'
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP'))) as client:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, '__move_index__:' + horizon, canonical([])))
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP,
                                             coverage_verifier=verified_fixture_coverage)
        with pytest.raises(SourceUnavailable) as error:
            identities._move_index(horizon)
        assert error.value.code == 'historical_hidden_move_coverage_unverified'


def test_missing_hidden_authority_prevents_initial_identity_acquisition(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import SourceUnavailable
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('No useful acquisition without hidden authority'))) as client:
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP,
                                             coverage_verifier=verified_fixture_coverage, should_acquire=lambda: True)
        with pytest.raises(SourceUnavailable) as error:
            identities.resolve('123')
        assert error.value.code == 'historical_hidden_move_coverage_unverified'


def test_unproven_cache_is_reacquired_only_when_permitted_and_old_index_is_preserved(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2, move_index_key
    from vimfc.domain import SourceUnavailable, canonical
    horizon = '2026-10-30T00:00:00Z'
    calls, verified = [], []
    old = [{'timestamp': '2026-10-27T00:00:00Z', 'actionhidden': True}]
    def wire(req):
        p = dict(req.url.params); calls.append(p)
        assert p['lestart'] == horizon and p['leend'] == SNAP
        return httpx.Response(200, json={'query': {'logevents': []}})
    def coverage(snapshot, horizon, events):
        verified.append(events)
        return verified_fixture_coverage(snapshot, horizon, events) if calls and events == [] else None
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(wire)) as client:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, '__move_index__:' + horizon, canonical(old)))
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        blocked = HistoricalIdentitiesV2(source, SNAP, coverage_verifier=coverage)
        with pytest.raises(SourceUnavailable): blocked._move_index(horizon)
        assert calls == []
        permitted = HistoricalIdentitiesV2(source, SNAP, coverage_verifier=coverage,
                                            hidden_coverage_verifier=verified_fixture_hidden_coverage,
                                            should_acquire=lambda: True)
        assert permitted._move_index(horizon) == [] and len(calls) == 1
        assert store.db.execute('SELECT record_json FROM identity_resolutions WHERE lookup_title=?', ('__move_index__:' + horizon,)).fetchone()[0] == canonical(old)
        assert store.db.execute('SELECT record_json FROM identity_resolutions WHERE lookup_title=?', (move_index_key(horizon),)).fetchone()[0] == canonical([])


def test_acquisition_permission_is_checked_before_each_continuation_page(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import SourceUnavailable
    calls = []
    def wire(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json={'query': {'logevents': []}, 'continue': {'continue': '-||', 'lecontinue': 'next'}})
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(wire)) as client:
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP,
                                             coverage_verifier=verified_fixture_coverage,
                                             hidden_coverage_verifier=verified_fixture_hidden_coverage,
                                             should_acquire=lambda: len(calls) < 1)
        with pytest.raises(SourceUnavailable): identities._move_index('2026-10-30T00:00:00Z')
        assert len(calls) == 1


@pytest.mark.parametrize('mode', ['wrong_interval', 'wrong_raw_hash', 'unfinished_continuation', 'missing_content_hash', 'wrong_manifest'])
def test_coverage_receipt_must_bind_interval_terminal_continuation_and_hashes(tmp_path, mode):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2
    from vimfc.domain import SourceUnavailable, canonical, digest
    horizon = '2026-10-30T00:00:00Z'
    def coverage(snapshot, horizon, events):
        receipt = verified_fixture_coverage(snapshot, horizon, events)
        if mode == 'wrong_interval': receipt['interval']['end'] = '2026-10-24T00:00:00Z'
        if mode == 'wrong_raw_hash': receipt['raw_events_hash'] = '0' * 64
        if mode == 'unfinished_continuation': receipt['pages'][-1]['response_continue'] = {'lecontinue': 'next'}
        if mode == 'missing_content_hash': receipt['pages'][0].pop('response_sha256')
        if mode != 'wrong_manifest': receipt['manifest_id'] = digest({k: v for k, v in receipt.items() if k != 'manifest_id'})
        else: receipt['manifest_id'] = '0' * 64
        return receipt
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP'))) as client:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)', (SNAP, '__move_index__:' + horizon, canonical([])))
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP,
                                             coverage_verifier=coverage,
                                             hidden_coverage_verifier=verified_fixture_hidden_coverage)
        with pytest.raises(SourceUnavailable) as error:
            identities._move_index(horizon)
        assert error.value.code == 'historical_move_coverage_unverified'


def test_full_reacquisition_sorts_and_deduplicates_multiple_response_pages(tmp_path):
    from vimfc.source.identities_v2 import HistoricalIdentitiesV2, move_index_key
    from vimfc.domain import canonical
    events = [{'logid': index, 'logpage': 90, 'pageid': 999, 'title': 'Old',
               'timestamp': stamp, 'params': {'target_title': 'New'}}
              for index, stamp in [(1, '2026-10-27T00:00:00Z'), (2, '2026-10-28T00:00:00Z')]]
    calls = []
    def wire(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json={'query': {'logevents': [events[1]]}, 'continue': {'continue': '-||', 'lecontinue': 'next'}}
                              if len(calls) == 1 else {'query': {'logevents': [events[0], events[1]]}})
    def coverage(snapshot, horizon, raw):
        assert raw == events
        receipt = verified_fixture_coverage(snapshot, horizon, raw)
        # The trusted fixture attests the physical mock pages; its chain must
        # include both responses even though the assembled index is deduplicated.
        from vimfc.domain import digest
        receipt['pages'] = [
            {'request_continue': {}, 'response_continue': {'continue': '-||', 'lecontinue': 'next'},
             'response_sha256': digest({'query': {'logevents': [events[1]]}}), 'events_hash': digest([events[1]])},
            {'request_continue': {'continue': '-||', 'lecontinue': 'next'}, 'response_continue': None,
             'response_sha256': digest({'query': {'logevents': [events[0], events[1]]}}), 'events_hash': digest([events[0], events[1]])}]
        receipt['manifest_id'] = digest({k: v for k, v in receipt.items() if k != 'manifest_id'})
        return receipt
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(wire)) as client:
        identities = HistoricalIdentitiesV2(RevisionSource(client, store, RawCache(tmp_path/'raw')), SNAP,
                                             coverage_verifier=coverage, hidden_coverage_verifier=verified_fixture_hidden_coverage,
                                             should_acquire=lambda: True, modern_logpage_verified=True)
        moves = identities._move_index('2026-10-30T00:00:00Z')
        assert len(calls) == 2 and [m['logid'] for m in moves] == [1, 2]
        assert store.db.execute('SELECT record_json FROM identity_resolutions WHERE lookup_title=?',
                                (move_index_key('2026-10-30T00:00:00Z'),)).fetchone()[0] == canonical(events)
