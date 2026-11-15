import hashlib
import json

from vimfc.domain import digest

SNAP = '2026-10-25T03:00:00Z'
HORIZON = '2026-10-30T00:00:00Z'


def receipt(root, payload, continuation=None):
    body = json.dumps(payload, sort_keys=True).encode()
    sha = hashlib.sha256(body).hexdigest()
    path = root / (sha + '.json')
    path.write_bytes(body)
    return {'path': path.name, 'sha256': sha, 'status': 200, 'request': {
        'action': 'query', 'list': 'logevents', 'letype': 'move', 'ledir': 'older',
        'lestart': HORIZON, 'leend': SNAP, 'leprop': 'ids|title|timestamp|details',
        **(continuation or {})}}


def test_full_paginated_receipts_bind_exact_events_and_continuations(tmp_path):
    from vimfc.pipeline.move_coverage_receipts import verify_move_receipts
    a = {'logid': 1, 'timestamp': '2026-10-26T00:00:00Z'}
    b = {'logid': 2, 'timestamp': '2026-10-27T00:00:00Z'}
    token = {'continue': '-||', 'lecontinue': 'next'}
    receipts = [receipt(tmp_path, {'continue': token, 'query': {'logevents': [b]}}),
                receipt(tmp_path, {'query': {'logevents': [a]}}, token)]
    proof = verify_move_receipts(tmp_path, receipts, SNAP, HORIZON, [a, b])
    assert proof['complete'] and proof['raw_events_hash'] == digest([a, b])
    assert len(proof['pages']) == 2 and proof['pages'][-1]['response_continue'] is None


def test_truncated_or_tampered_receipts_never_prove_coverage(tmp_path):
    from vimfc.pipeline.move_coverage_receipts import verify_move_receipts
    token = {'continue': '-||', 'lecontinue': 'next'}
    first = receipt(tmp_path, {'continue': token, 'query': {'logevents': []}})
    assert verify_move_receipts(tmp_path, [first], SNAP, HORIZON, []) is None
    (tmp_path / first['path']).write_text('{}', encoding='utf8')
    assert verify_move_receipts(tmp_path, [first], SNAP, HORIZON, []) is None


def test_filtered_log_query_is_not_global_move_coverage(tmp_path):
    from vimfc.pipeline.move_coverage_receipts import verify_move_receipts
    item = receipt(tmp_path, {'query': {'logevents': []}})
    item['request']['letitle'] = 'Only one page'
    assert verify_move_receipts(tmp_path, [item], SNAP, HORIZON, []) is None
