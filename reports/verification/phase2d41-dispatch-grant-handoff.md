# Phase 2D.4.1 offline dispatch grants

- `backend/src/vimfc/pipeline/transactional_control.py` uses the existing dispatch charge as the durable grant. The witnessed record binds the decision ID (request ID), operator, job, attempt, owner epoch, launch digest, reservation ID, allowance and charged units. After a short admission transaction closes, the mock transport receives those same charged units.
- `tests/integration/test_transactional_control.py` covers a blocked transport and a concurrent stop from a separate authority connection, duplicate delivery prevention, both orderings of stop versus admission, expiry after charge, timeout, and hard exit after admission but before the mock call. Existing tests cover witness refusal, rollback, authorization, and budget bounds.

## State transitions

1. **Charged and witnessed.** The expected version and current running owner are checked before the charge commits. Only the invocation that appended the new request ID may attempt admission. Explicit history recovery may acknowledge a pending charge but cannot recreate that invocation or its grant.
2. **Admitted.** A short SQLite write transaction rechecks eligible owner, launch identity, expiry and the exact acknowledged grant at the head. Successful transaction closure is the admission boundary. The grant is consumed by this invocation and no reusable token is returned. A separate durable delivery record is unnecessary in this model because all replays of the charge ID are denied.
3. **In flight or finished.** The mock runs after the transaction closes. No outcome, including an exception, restores charged units. Admission is the boundary for revocation. A later stop denies new admission while the already admitted call may still begin or finish.

## Race and failure cases

| Event | Result |
| --- | --- |
| Invalid owner, stale version, or exhausted reservation before charge | No dispatch. No new charge. |
| Stop, expiry, witness failure, or another authority decision wins before admission | No mock call. The committed charge stays spent. |
| Admission wins before stop | The admitted call may still execute. Stop or unknown ownership blocks later admission. |
| Mock is blocked | A separate connection can write and witness stop while the call remains blocked. |
| Duplicate request ID | Zero additional calls and no second charge. |
| Process exits after charge commit or after admission | Charge survives. Witness recovery acknowledges the existing record when eligible. It does not replay the request. |
| Timeout or crash after mock dispatch | Delivery may be ambiguous. No refund or automatic replay. |

## Targeted preservation

Pre/post SHA-256 matched for `backup_recovery.py`, `trusted_control.py`, `incremental_collection.py` and `test_annotation_builder.py`. The pre-existing annotation-builder diff retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

| Changed file | Before SHA-256 | After SHA-256 |
| --- | --- | --- |
| `backend/src/vimfc/pipeline/transactional_control.py` | `BA495FAFA328145176D6090A862C19AD97E82979A370D0EB3AAC1FB5D518C4B6` | `5137567F1CBBA1E17DA9C41FBB73D8F6240F6259ED840D38F56712AE95F56A5D` |
| `tests/integration/test_transactional_control.py` | `9F925AE54F429B1E963EB804A19B1D3C9CBDCAF22E2666CAE1E4AAD476345FA1` | `8CF32FE26C99785005F431724D6C8EE018BCEEB2DF8C50B10CD4F61B16289496` |

## Verification

- Focused transactional control, trusted control, publication reconciliation, and backup/restore: 109 passed, 1 skipped. The skip is the Windows symlink privilege case.
- Full offline regression: 1022 passed, 3 skipped in 498.70 seconds. All skips were Windows symlink privilege checks in the snapshot or backup suites.
- Both runs used `VIMFC_OFFLINE=1`, pytest's default socket guard, and fresh local fixture directories.
- Full command: `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d41-full`.

## Limits

Admission is local and synthetic. It cannot physically revoke an already admitted I/O or prove external delivery. The exact-head check may conservatively spend allowance without sending when a different authority record wins before admission. Persistent trust, real transport, physical fencing, and coordinated rollback of both SQLite files remain outside this phase.

