"""Typed historical title lookups cannot collide with numeric page identities."""
from dataclasses import asdict
import json
import httpx
import pytest

from vimfc.domain import SourceRevisionRef, SourceUnavailable, canonical
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache

SNAP = '2026-10-25T03:00:00Z'
STAMP = '2026-10-13T00:00:00Z'


def payload(page, title, revision=None):
    return {'curtimestamp': '2026-10-30T00:00:00Z', 'query': {'pages': [
        {'pageid': page, 'title': title, 'ns': 0,
         'revisions': [{'revid': revision or page + 1000, 'timestamp': STAMP}]}]}}


def test_numeric_title_and_equal_digit_page_id_are_independent_and_survive_restart(tmp_path):
    calls = []
    def serve(req):
        params = dict(req.url.params); calls.append(params)
        assert 'redirects' not in params and params['rvstart'] == SNAP
        return httpx.Response(200, json=payload(456, '123') if 'titles' in params else payload(123, 'Different'))
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        by_title = source.select_title_revision('viwiki', '123', SNAP)
        by_page = source.select_page_revision('viwiki', 123, SNAP)
        assert by_title.page_id == 456 and by_page.page_id == 123
        assert calls[0]['titles'] == '123' and 'pageids' not in calls[0]
        assert calls[1]['pageids'] == '123' and 'titles' not in calls[1]
        assert store.db.execute('SELECT count(*) FROM snapshot_revision_refs WHERE identity IN (?,?)', ('123', '456')).fetchone()[0] == 0
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('Typed replay is cached'))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        assert source.select_title_revision('viwiki', '123', SNAP) == by_title
        assert source.select_page_revision('viwiki', 123, SNAP) == by_page


def test_ambiguous_legacy_numeric_cache_is_neither_reused_nor_modified(tmp_path):
    legacy = SourceRevisionRef('viwiki', '123', 123, 'Different', 1123, STAMP, SNAP, '2026-10-30T00:00:00Z')
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload(456, '123')))) as client:
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', '123', SNAP, canonical(legacy)))
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        assert source.select_title_revision('viwiki', '123', SNAP).page_id == 456
        assert json.loads(store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE identity=?', ('123',)).fetchone()[0]) == asdict(legacy)


def test_nonnumeric_legacy_title_is_reused_with_persisted_receipt(tmp_path):
    from vimfc.source.explicit_revisions import selector_key
    legacy = SourceRevisionRef('viwiki', 'Target', 456, 'Target', 1456, STAMP, SNAP, '2026-10-30T00:00:00Z')
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('No HTTP for proven legacy title'))) as client:
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', 'Target', SNAP, canonical(legacy)))
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        assert source.select_title_revision('viwiki', 'Target', SNAP) == legacy
        row = json.loads(store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE identity=?', (selector_key('title', 'Target'),)).fetchone()[0])
        assert row['provenance']['kind'] == 'validated_legacy_nonnumeric_title'
        assert row['provenance']['legacy_identity'] == 'Target'


def test_explicit_page_id_rejects_mismatched_source_page(tmp_path):
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload(999, 'Wrong')))) as client:
        with pytest.raises(SourceUnavailable):
            RevisionSource(client, store, RawCache(tmp_path/'raw')).select_page_revision('viwiki', 123, SNAP)
        assert store.db.execute('SELECT count(*) FROM snapshot_revision_refs').fetchone()[0] == 0


def test_explicit_failure_cache_cannot_poison_other_selector_kind(tmp_path):
    calls = []
    def serve(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json={'query': {'pages': [{'missing': True}]}} if 'titles' in req.url.params else payload(123, 'Different'))
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable): source.select_title_revision('viwiki', '123', SNAP)
        with pytest.raises(SourceUnavailable): source.select_title_revision('viwiki', '123', SNAP)
        assert source.select_page_revision('viwiki', 123, SNAP).page_id == 123
        assert len(calls) == 2


def test_legacy_numeric_selector_still_uses_pageids(tmp_path):
    calls = []
    def serve(req):
        calls.append(dict(req.url.params)); return httpx.Response(200, json=payload(123, 'Different'))
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve)) as client:
        assert RevisionSource(client, store, RawCache(tmp_path/'raw')).select_revision('viwiki', '123', SNAP).page_id == 123
        assert calls[0]['pageids'] == '123' and 'titles' not in calls[0]


def test_legacy_numeric_page_ref_is_reused_only_when_exact_page_identity_matches(tmp_path):
    from vimfc.source.explicit_revisions import selector_key
    legacy = SourceRevisionRef('viwiki', '123', 123, 'Different', 1123, STAMP, SNAP, '2026-10-30T00:00:00Z')
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('Exact checked legacy page is reusable'))) as client:
        store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', ('viwiki', '123', SNAP, canonical(legacy)))
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        assert source.select_page_revision('viwiki', 123, SNAP) == legacy
        row = json.loads(store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE identity=?', (selector_key('page_id', 123),)).fetchone()[0])
        assert row['provenance']['kind'] == 'validated_legacy_exact_page'


def fixture_selector_receipt(context, saved):
    from vimfc.domain import digest
    receipt = {'version': 'historical-selector-receipt-1', 'site': context['site'],
               'kind': context['kind'], 'identity': context['identity'], 'snapshot': context['snapshot'],
               'ref_hash': digest(context['ref']), 'query_hash': digest(context['query']),
               'response_sha256': digest({'fixture_response': context['ref']})}
    return {**receipt, 'manifest_id': digest(receipt)}


def test_strict_new_selector_without_receipt_verifier_never_reaches_http(tmp_path):
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: pytest.fail('Unbound selector cannot dispatch'))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        source.require_selector_receipts = True
        with pytest.raises(SourceUnavailable) as error:
            source.select_title_revision('viwiki', '123', SNAP)
        assert error.value.code == 'historical_selector_receipt_unverified'


def test_selector_receipt_is_verified_for_live_alias_and_cached_replay(tmp_path):
    calls, verified = [], []
    def wire(req):
        calls.append(dict(req.url.params)); return httpx.Response(200, json=payload(456, '123'))
    def verifier(context, saved):
        verified.append((context, saved)); return fixture_selector_receipt(context, saved)
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(wire)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        source.require_selector_receipts = True
        source.selector_receipt_verifier = verifier
        title = source.select_title_revision('viwiki', '123', SNAP)
        assert source.select_page_revision('viwiki', 456, SNAP).page_id == title.page_id
        assert source.select_title_revision('viwiki', '123', SNAP) == title
        assert len(calls) == 1 and len(verified) == 3
        assert all(context['kind'] == 'title' and context['query']['titles'] == '123' for context, _ in verified)
        assert verified[0][1] is None and all(saved is not None for _, saved in verified[1:])


def test_strict_cached_alias_cannot_bypass_missing_or_bad_response_receipt(tmp_path):
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload(456, '123')))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        source.select_title_revision('viwiki', '123', SNAP)
        source.require_selector_receipts = True
        with pytest.raises(SourceUnavailable) as error:
            source.select_page_revision('viwiki', 456, SNAP)
        assert error.value.code == 'historical_selector_receipt_unverified'
        def wrong_receipt(context, saved):
            from vimfc.domain import digest
            receipt = fixture_selector_receipt(context, saved)
            receipt['ref_hash'] = '0' * 64
            receipt['manifest_id'] = digest({k: v for k, v in receipt.items() if k != 'manifest_id'})
            return receipt
        source.selector_receipt_verifier = wrong_receipt
        with pytest.raises(SourceUnavailable) as error:
            source.select_page_revision('viwiki', 456, SNAP)
        assert error.value.code == 'historical_selector_receipt_unverified'


@pytest.mark.parametrize('field,value', [('title', 'Forged current title'),
                                         ('lookup_timestamp', '2026-11-01T00:00:00Z')])
def test_alias_receipt_binds_full_original_ref_including_title_and_horizon(tmp_path, field, value):
    from vimfc.domain import digest
    from vimfc.source.explicit_revisions import selector_key
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload(456, '123')))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        source.require_selector_receipts = True
        source.selector_receipt_verifier = fixture_selector_receipt
        source.select_title_revision('viwiki', '123', SNAP)
        key = selector_key('page_id', 456)
        record = json.loads(store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE identity=?', (key,)).fetchone()[0])
        record['ref'][field] = value
        record['ref_hash'] = digest(record['ref'])
        store.db.execute('UPDATE snapshot_revision_refs SET record_json=? WHERE identity=?', (canonical(record), key))
        with pytest.raises(SourceUnavailable) as error:
            source.select_page_revision('viwiki', 456, SNAP)
        assert error.value.code == 'invalid_historical_cache'
