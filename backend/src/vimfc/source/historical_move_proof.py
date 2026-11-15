"""Validate supplied move records without changing their archived representation.

The caller must separately establish that the records cover the entire interval
and that every in-window event was recorded under deployed semantics that use
log_page for the moved page. A modern server alone does not establish semantics
of historical records. Neither fact follows from a timestamp or public response.
"""
from copy import deepcopy

from vimfc.domain import at_or_before, digest, utc

VERSION = 'historical-move-proof-1'


def build_move_proof(events, snapshot, horizon, *, modern_logpage_verified=False):
    """Check events in (snapshot, horizon]; never guess a hidden move's owner.

    ``raw_events_hash`` is compatible with the digest of the complete original
    list. The derived events are suitable for the existing title reconstruction
    only when status is VERIFIED and the caller also proved interval coverage.
    Set ``modern_logpage_verified`` only when evidence covers every event's
    recording time, not simply the version currently serving the API.
    """
    snapshot, horizon = utc(snapshot), utc(horizon)
    if not at_or_before(snapshot, horizon):
        raise ValueError('Move-log horizon precedes snapshot')
    raw = deepcopy(events)
    if not isinstance(raw, list):
        raise ValueError('Move events must be an ordered list')
    blockers, derived = [], []
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            blockers.append({'index': index, 'reason': 'invalid_record'})
            continue
        stamp = event.get('timestamp')
        try:
            if not isinstance(stamp, str):
                raise ValueError('missing timestamp')
            if at_or_before(stamp, snapshot) or not at_or_before(stamp, horizon):
                continue
        except (ValueError, TypeError, OverflowError):
            blockers.append({'index': index, 'reason': 'invalid_timestamp'})
            continue
        if event.get('actionhidden') or event.get('suppressed'):
            blockers.append({'index': index, 'reason': 'hidden_action'})
            continue
        if modern_logpage_verified is not True:
            blockers.append({'index': index, 'reason': 'logpage_semantics_unverified'})
            continue
        page, logid, params = event.get('logpage'), event.get('logid'), event.get('params')
        if (type(page) is not int or page <= 0 or type(logid) is not int or logid <= 0
                or not isinstance(event.get('title'), str) or not event['title'].strip()
                or not isinstance(params, dict) or not isinstance(params.get('target_title'), str)
                or not params['target_title'].strip()):
            blockers.append({'index': index, 'reason': 'invalid_move_identity_or_title'})
            continue
        # The legacy helper expects pageid, while the original API field joins
        # the current page at the logged old title. Keep raw bytes separate.
        derived.append({**deepcopy(event), 'pageid': page})
    proof = {'version': VERSION, 'snapshot': snapshot, 'horizon': horizon,
             'raw_events': raw, 'raw_events_hash': digest(raw),
             'modern_logpage_verified': modern_logpage_verified is True,
             'coverage': 'caller_must_prove_complete_interval',
             'status': 'INCOMPLETE' if blockers else 'VERIFIED',
             'blockers': blockers, 'operational_events': [] if blockers else derived}
    proof['manifest_id'] = digest(proof)
    return proof
