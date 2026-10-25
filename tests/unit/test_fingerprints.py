import pytest
from vimfc.domain import digest, utc


def test_canonical_fingerprint_ignores_mapping_order_but_not_values():
    assert digest({'a': 1, 'b': [2]}) == digest({'b': [2], 'a': 1})
    assert digest({'a': 1}) != digest({'a': True})


def test_snapshot_must_have_timezone():
    assert utc('2026-10-14T07:00:00+07:00') == '2026-10-14T00:00:00Z'
    with pytest.raises(ValueError):
        utc('2026-10-14T00:00:00')
