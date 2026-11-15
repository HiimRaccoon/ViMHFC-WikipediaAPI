from copy import deepcopy
import pytest

from vimfc.domain import digest
from vimfc.source.identities import title_at_snapshot

SNAP = '2026-10-25T03:00:00Z'
HORIZON = '2026-11-15T00:00:00Z'


def visible(**changes):
    event = {'logid': 53767350, 'timestamp': '2026-11-14T15:27:08Z',
             'pageid': 20046686, 'logpage': 20014766,
             'title': 'Old', 'params': {'target_title': 'New'}}
    return {**event, **changes}


def build(events, **options):
    from vimfc.source.historical_move_proof import build_move_proof
    return build_move_proof(events, SNAP, HORIZON, modern_logpage_verified=True, **options)


def test_operational_identity_uses_logged_moved_page_not_current_title_page():
    records = [visible()]
    original = deepcopy(records)
    result = build(records)
    assert result['status'] == 'VERIFIED'
    assert result['raw_events'] == original and records == original
    assert result['raw_events_hash'] == digest(original)
    assert result['operational_events'][0]['pageid'] == 20014766
    assert title_at_snapshot(20014766, 'New', result['operational_events'], SNAP) == 'Old'
    assert title_at_snapshot(20046686, 'New', result['operational_events'], SNAP) == 'New'


def test_hidden_action_in_window_blocks_all_operational_moves():
    events = [visible(), {'logid': 99, 'timestamp': '2026-11-02T15:44:24Z', 'actionhidden': True}]
    result = build(events)
    assert result['status'] == 'INCOMPLETE'
    assert result['blockers'][0]['reason'] == 'hidden_action'
    assert result['operational_events'] == []
    assert result['raw_events_hash'] == digest(events)


def test_actual_actionhidden_record_is_not_assumed_oversight_suppressed():
    event = {'logid': 53759367, 'actionhidden': True, 'timestamp': '2026-11-02T15:44:24Z'}
    result = build([event])
    assert result['blockers'] == [{'index': 0, 'reason': 'hidden_action'}]
    assert result['raw_events'] == [event]


def test_hidden_actions_outside_window_are_ignored_but_retained_in_raw():
    hidden = {'logid': 99, 'timestamp': '2026-10-25T03:00:00Z', 'actionhidden': True}
    later = {'logid': 100, 'timestamp': '2026-11-16T00:00:00Z', 'actionhidden': True}
    result = build([hidden, visible(), later])
    assert result['status'] == 'VERIFIED'
    assert len(result['operational_events']) == 1
    assert result['raw_events'] == [hidden, visible(), later]


@pytest.mark.parametrize('changes', [
    {'logpage': 0}, {'logpage': None}, {'logpage': '20014766'},
    {'title': None}, {'params': {}}, {'timestamp': None}, {'logid': None},
])
def test_invalid_or_incomplete_event_never_authorizes_a_move(changes):
    result = build([visible(**changes)])
    assert result['status'] == 'INCOMPLETE' and result['operational_events'] == []


def test_version_epoch_must_be_explicitly_proven_not_inferred_from_event_date():
    from vimfc.source.historical_move_proof import build_move_proof
    result = build_move_proof([visible()], SNAP, HORIZON)
    assert result['status'] == 'INCOMPLETE'
    assert result['blockers'][0]['reason'] == 'logpage_semantics_unverified'


def test_invalid_interval_cannot_authorize_empty_proof():
    from vimfc.source.historical_move_proof import build_move_proof
    with pytest.raises(ValueError):
        build_move_proof([], HORIZON, SNAP, modern_logpage_verified=True)
