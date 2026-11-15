"""Check a complete public logevents pagination chain against saved bodies."""
import hashlib
import json
from pathlib import Path

from vimfc.domain import digest


def verify_move_receipts(root, receipts, snapshot, horizon, raw_events):
    root = Path(root).resolve()
    expected = {}
    pages, events, active = [], [], False
    for item in receipts:
        request = item.get('request', {})
        if (request.get('list') != 'logevents' or request.get('letype') != 'move'
                or request.get('ledir') != 'older' or request.get('lestart') != horizon
                or request.get('leend') != snapshot):
            continue
        allowed = {'action', 'list', 'letype', 'ledir', 'lestart', 'leend', 'lelimit',
                   'leprop', 'format', 'formatversion', 'maxlag', 'continue', 'lecontinue'}
        if (set(request) - allowed or request.get('action') != 'query'
                or set(request.get('leprop', '').split('|')) != {'ids', 'title', 'timestamp', 'details'}):
            return None
        continuation = {k: v for k, v in request.items() if k in ('continue', 'lecontinue')}
        if not active and continuation:
            continue
        if continuation != expected:
            return None
        active = True
        path = (root / item.get('path', '')).resolve()
        if not path.is_relative_to(root) or item.get('status') != 200:
            return None
        try:
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != item.get('sha256'):
                return None
            payload = json.loads(body)
            batch = payload['query']['logevents']
            if not isinstance(batch, list) or 'error' in payload:
                return None
            nxt = payload.get('continue')
            if nxt is not None and (not isinstance(nxt, dict) or not nxt):
                return None
        except (OSError, ValueError, TypeError, KeyError):
            return None
        pages.append({'request_continue': continuation, 'response_continue': nxt,
                      'response_sha256': item['sha256'], 'events_hash': digest(batch)})
        events.extend(batch)
        if nxt is None:
            break
        expected = nxt
    if not pages or pages[-1]['response_continue'] is not None:
        return None
    try:
        combined = sorted({digest(e): e for e in events}.values(), key=lambda e: (e['timestamp'], e.get('logid', 0)))
    except (TypeError, KeyError):
        return None
    if digest(combined) != digest(raw_events):
        return None
    result = {'version': 'historical-move-coverage-1', 'snapshot': snapshot, 'horizon': horizon,
              'raw_events_hash': digest(raw_events), 'complete': True,
              'interval': {'start': horizon, 'end': snapshot, 'direction': 'older'}, 'pages': pages,
              'scope': 'public_api_responses_only'}
    result['manifest_id'] = digest(result)
    return result
