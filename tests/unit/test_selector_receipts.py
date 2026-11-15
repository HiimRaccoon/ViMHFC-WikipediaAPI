import hashlib
import json

from vimfc.domain import digest


def test_selector_receipt_binds_request_and_exact_revision(tmp_path):
    from vimfc.pipeline.selector_receipts import verify_selector_receipt
    query = {'action': 'query', 'prop': 'revisions', 'titles': '123', 'rvstart': '2026-10-25T03:00:00Z',
             'rvdir': 'older', 'rvlimit': 1, 'rvprop': 'ids|timestamp', 'rvslots': 'main', 'curtimestamp': 1}
    ref = {'site': 'viwiki', 'identity': '123', 'page_id': 456, 'title': '123',
           'revision_id': 88, 'revision_timestamp': '2026-10-20T00:00:00Z',
           'snapshot': '2026-10-25T03:00:00Z', 'lookup_timestamp': '2026-10-30T00:00:00Z'}
    body = json.dumps({'curtimestamp': ref['lookup_timestamp'], 'query': {'pages': [
        {'pageid': 456, 'title': '123', 'revisions': [{'revid': 88, 'timestamp': ref['revision_timestamp']}]}]}}).encode()
    sha = hashlib.sha256(body).hexdigest()
    (tmp_path / 'body.json').write_bytes(body)
    receipts = [{'status': 200, 'path': 'body.json', 'sha256': sha, 'request': query}]
    context = {'site': 'viwiki', 'kind': 'title', 'identity': '123',
               'snapshot': ref['snapshot'], 'ref': ref, 'query': query}
    value = verify_selector_receipt(tmp_path, receipts, context, None)
    assert value['response_sha256'] == sha and value['ref_hash'] == digest(ref)
    assert verify_selector_receipt(tmp_path, receipts, context, value) == value
    bad = {**query, 'rvstart': '2026-01-01T00:00:00Z'}
    assert verify_selector_receipt(tmp_path, [{**receipts[0], 'request': bad}], {**context, 'query': bad}, None) is None
    (tmp_path / 'body.json').write_bytes(b'{}')
    assert verify_selector_receipt(tmp_path, receipts, context, value) is None


def test_page_id_response_cannot_validate_a_title_query(tmp_path):
    from vimfc.pipeline.selector_receipts import verify_selector_receipt
    query = {'action': 'query', 'prop': 'revisions', 'titles': '123'}
    context = {'site': 'viwiki', 'kind': 'title', 'identity': '123', 'snapshot': 'x',
               'ref': {'page_id': 456}, 'query': query}
    assert verify_selector_receipt(tmp_path, [{'status': 200, 'path': 'x', 'sha256': 'a'*64,
                                              'request': {'action': 'query', 'prop': 'revisions', 'pageids': '123'}}],
                                   context, None) is None
