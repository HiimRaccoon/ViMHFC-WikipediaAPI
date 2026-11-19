# Phase 2D.2 offline transactional control

- `backend/src/vimfc/pipeline/transactional_control.py` adds SQLite authority, a separate SQLite witness, and a mock worker gate. It reuses the Phase 2C history checks and ownership, reservation, and publication intent transitions. Trusted COMMITTED and actual publication are outside this prototype.
- `tests/integration/test_transactional_control.py` covers hard process exits, competing writers, stale identity and version, reuse, expiry, witness outage, rollback, missing authority, scratch loss, prepared publication intent, and preservation of reservations after release.

## Transaction rules

SQLite serializes writers with `BEGIN IMMEDIATE`. The witness binds to the genesis configuration, stages the exact next record digest before authority commit, and acknowledges only that record against the expected predecessor. Recovery verifies the authority history, current head, and pending digest before acknowledgment. Ordinary open and decision calls perform no automatic recovery. A decision ID is idempotent only with its original version, action, and payload. Owner takeover requires a new explicit claim from a permitted state.

The lifetime allocation is historical debit plus every reservation. A launch can issue a worker only on its fresh acknowledged transition. The worker checks attempt, epoch, job, acknowledged launch digest, and process-local expiry. A mock request is durably charged to its reservation and acknowledged before dispatch. The gate rechecks the current authority under its write lock before calling the fixture transport. Ambiguous or failed dispatch does not return units or replay a request.

## Crash and restart matrix

| Interruption or loss | Result |
| --- | --- |
| Before witness stage | Authority transaction rolls back and the old acknowledged head remains usable. |
| After stage, before authority commit | Staged digest has no corresponding committed record. Recovery blocks for operator review. |
| After commit, before witness acknowledgment | New decisions and worker actions block. Explicit recovery can acknowledge the exact staged record. |
| After launch commit or acknowledgment, before worker issuance | Reservation stays allocated. Launch stays consumed after recovery and no worker can be reissued. |
| After dispatch commit or before mock call | Request units remain charged after recovery. Mock call count is zero. |
| After mock call | One recorded mock call. Charge stays and restart does not redispatch. |
| Scratch loss, independent DB rollback, witness outage, or missing authority | Scratch loss preserves the allocation. Rollback, outage, and missing authority deny decisions or gate activity until the state is verifiable. |

## Targeted preservation

The seven pre-edit SHA-256 values for the files below matched after implementation. The tracked patch for `tests/integration/test_annotation_builder.py` retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. Tracked status showed only that pre-existing modification and `git diff --check` passed.

| Repo file | SHA-256 |
| --- | --- |
| `backend/src/vimfc/pipeline/trusted_control.py` | `10DF70E4113C6D547595F03E725EBA776A62C70072B19DB46C3BF71843BC5A87` |
| `backend/src/vimfc/pipeline/publication_reconciliation.py` | `26CAF3D6A4758CD3BEEFC9C1FC76E99799FB127D7FC5E0D3FC57D853A2433F80` |
| `backend/src/vimfc/pipeline/generation_publication.py` | `8C85F9E49D9873A9CC27304A2E5F56A598298826240532BAA5548A2ECBC5D27C` |
| `backend/src/vimfc/pipeline/local_snapshot.py` | `E212DD5BFB1400A6AA68AFA8E179B91978379FEFB7CD2C8EDBC36913A73AD174` |
| `tests/integration/test_annotation_builder.py` | `35F3A77ED26F8039FA97124A223C6D93E6448B0B7CFAF65DC201BBF054472CAE` |
| `tests/integration/test_trusted_control.py` | `2A4893F0EBE7318476EAFE95DE58D71A009040D89E0EC47726A385FA3641FD5D` |
| `tests/integration/test_publication_reconciliation.py` | `6140D481A2E3A374E05B6409083FA44582FC553F8AAC2E082F2D2A0FD9BF5393` |
| `backend/src/vimfc/pipeline/transactional_control.py` | `BA495FAFA328145176D6090A862C19AD97E82979A370D0EB3AAC1FB5D518C4B6` |
| `tests/integration/test_transactional_control.py` | `9F925AE54F429B1E963EB804A19B1D3C9CBDCAF22E2666CAE1E4AAD476345FA1` |

## Verification

- Focused Phase 2D.2 plus Phase 2C trusted-control and reconciliation suites: 63 passed, including 19 new cases and 11 hard process exits.
- Full offline regression: 976 passed, 2 skipped in 505.98 seconds. Both skips were pre-existing Windows symlink privilege checks in `test_local_snapshot.py:75` and `:205`.
- Ran with `VIMFC_OFFLINE=1`: `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d2-full`. The default pytest socket guard was active.

## Production blockers

Coordinated rollback of both local files is outside the model. Production needs a separately administered trust anchor, authenticated operator and worker identities, durable credential expiry, real fencing, and approved recovery for a staged intent with no matching authority commit. Local process exits exercise the protocol, while storage power loss and native Linux or UIT behavior require separate validation. The process-local worker callback and fixture transport model conservative charging, with no guarantee of exactly-once external delivery.

