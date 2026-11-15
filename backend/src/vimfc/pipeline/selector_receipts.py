"""Bind an explicit title/page selector to its saved historical API response."""
import hashlib
import json
from pathlib import Path

from vimfc.domain import digest


def verify_selector_receipt(root, receipts, context, saved):
    root = Path(root).resolve()
    query, ref = context['query'], context['ref']
    kind, identity = context['kind'], context['identity']
    expected_key = 'titles' if kind == 'title' else 'pageids' if kind == 'page_id' else None
    fields = {'action', 'prop', expected_key, 'rvstart', 'rvdir', 'rvlimit', 'rvprop', 'rvslots', 'curtimestamp'}
    if (context['site'] != 'viwiki' or expected_key is None or query.get(expected_key) != identity
            or set(query) != fields or query.get('action') != 'query' or query.get('prop') != 'revisions'
            or query.get('rvstart') != context['snapshot'] or query.get('rvdir') != 'older'
            or str(query.get('rvlimit')) != '1' or query.get('rvprop') != 'ids|timestamp'
            or query.get('rvslots') != 'main' or str(query.get('curtimestamp')) != '1'):
        return None
    for item in reversed(receipts):
        request = item.get('request', {})
        if item.get('status') != 200 or any(str(request.get(k)) != str(v) for k, v in query.items()):
            continue
        if set(request) - (fields | {'format', 'formatversion', 'maxlag'}):
            continue
        path = (root / item.get('path', '')).resolve()
        if not path.is_relative_to(root):
            continue
        try:
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != item.get('sha256'):
                continue
            payload = json.loads(body)
            pages = payload['query']['pages']
            if len(pages) != 1 or 'error' in payload:
                continue
            page = pages[0]
            revisions = page['revisions']
            if (page['pageid'] != ref['page_id'] or page['title'] != ref['title']
                    or payload.get('curtimestamp') != ref['lookup_timestamp']
                    or len(revisions) != 1 or revisions[0]['revid'] != ref['revision_id']
                    or revisions[0]['timestamp'] != ref['revision_timestamp']):
                continue
        except (OSError, ValueError, TypeError, KeyError):
            continue
        result = {'version': 'historical-selector-receipt-1', 'site': context['site'],
                  'kind': kind, 'identity': identity, 'snapshot': context['snapshot'],
                  'ref_hash': digest(ref), 'response_sha256': item['sha256'], 'query_hash': digest(query)}
        result['manifest_id'] = digest(result)
        if saved is None or saved == result:
            return result
    return None
