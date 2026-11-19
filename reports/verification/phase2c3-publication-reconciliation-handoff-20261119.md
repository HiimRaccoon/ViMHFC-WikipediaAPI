# Phase 2C.3 offline publication reconciliation

The new `publication_reconciliation.py` coordinator binds a stopped `SyntheticOwnership` owner and caller-supplied target, prior pointer and compatibility expectations to the existing authority intent. It acknowledges PREPARED before calling the Phase 2B publisher. Before storing or acknowledging reconciliation, it verifies the actual pointer and artifact bytes, the manifest digest and compatibility, and the prior generation. It returns trusted `COMMITTED` only after checking the acknowledged authority and the bytes again. The Phase 2B receipt is outside that decision.

The new `test_publication_reconciliation.py` exercises the following boundaries locally:

| Fault boundary | Result |
|---|---|
| Before or after PREPARED acknowledgment | Pointer stays at prior. Explicit reconciliation and publication retry are refused. |
| Before or after promotion, or before pointer replacement | Pointer stays at prior. Any promoted generation remains unselected. |
| After pointer replacement or during final receipt creation | Explicit reconciliation can verify existing bytes and commit. |
| Before or after storing reconciliation, or before witness acknowledgment | Explicit reconciliation rechecks bytes before storing or acknowledging. |
| After reconciliation acknowledgment | Explicit reconciliation verifies the commit without writing another decision. |
| Missing pointer, corrupt bytes, conflicting expectations, stale owner or unavailable witness | Reconciliation refuses and leaves the witness head unchanged. |

Tests also cover an authority-only `verified=True` assertion, two-generation ancestry, and preservation of owner identity, epoch, attempt, reservations, and lifetime debit. Reconciliation writes only to the in-memory authority and witness. It never retries publication or changes ownership or budget.

Focused passed all **18 tests**. Full offline with `VIMFC_OFFLINE=1` passed **957 tests** in 468.50 seconds. Two Windows symlink cases were skipped (`test_local_snapshot.py:75` and `:205`). The full command was `.tools/python312/python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2c3-full`.

Targeted preservation: Git status and targeted diff showed the same existing modification in `test_annotation_builder.py`. The Phase 2B publication and Phase 2C.2 authority source/test pairs matched their before/after SHA-256. No whole-tree preservation claim is made.

| Added file | SHA-256 |
|---|---|
| `backend/src/vimfc/pipeline/publication_reconciliation.py` | `26CAF3D6A4758CD3BEEFC9C1FC76E99799FB127D7FC5E0D3FC57D853A2433F80` |
| `tests/integration/test_publication_reconciliation.py` | `6140D481A2E3A374E05B6409083FA44582FC553F8AAC2E082F2D2A0FD9BF5393` |

This model assumes a single-threaded, cooperating synthetic caller. The authority and witness are in memory, and the caller must supply independently trusted expectations. Concurrent writers, physical fencing, durable authority and witness recovery, filesystem power-loss guarantees and UIT storage remain unresolved. The coordinator does not make the low-level fixture authority or the Phase 2B publisher into production trust boundaries.

```text
PHASE_2C_3 = PASS
PUBLICATION_RECONCILIATION = PASS
TRUSTED_COMMIT_MODEL = PASS
CRASH_RECOVERY = PASS
BUDGET_PRESERVATION = PASS
OFFLINE_REGRESSION = PASS
PRODUCTION_AUTHORITY = NOT_IMPLEMENTED
PHYSICAL_FENCING = UNVERIFIED
UIT_EXECUTION = NOT_AUTHORIZED
PILOT_10000 = NO_GO
```

