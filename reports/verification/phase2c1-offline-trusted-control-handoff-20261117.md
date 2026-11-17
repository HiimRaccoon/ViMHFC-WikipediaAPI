# Phase 2C.1 offline trusted-control handoff

Baseline: user explicitly authorized the existing local working tree after the approved GitHub commit could not be verified locally. Local HEAD is `b520f11`. Phase 2A/2B modules and their tests were already present as untracked files. The pre-existing modification to `tests/integration/test_annotation_builder.py` was retained.

Created `backend/src/vimfc/pipeline/trusted_control.py`, `tests/integration/test_trusted_control.py`, and this handoff. Test output uses new `.tools/phase2c1-*` directories. Existing production callers, request guards, Slurm configuration, snapshot and publication modules were not edited or integrated.

## Contract and interfaces

`SyntheticAuthority` and `FakeWitness` model state in memory only. The initial state binds authority ID, schema 1, workspace/run/bundle, runtime, budget ID, ceiling and historical debit. It holds owner identity/epoch, used attempts, reservations and allocation, current/previous pointers and publication state. Each decision record has exactly schema, version, decision ID, expected version, predecessor digest, action, payload and resultant state. The existing canonical digest and Phase 2B pointer validation are reused.

`propose` validates but does not store; `store` validates the exact proposal and appends it; `acknowledge` advances the fake witness; `inspect` reports whether the head is acknowledged and therefore eligible. Identical decision replay is idempotent; altered replay, stale versions, unacknowledged or inconsistent heads and invalid chain/schema are refused. A failed witness acknowledgment leaves a pending decision that cannot authorize a later decision. Fake witness availability, failure, predecessor and replay are independently modeled; it is neither persistent nor an authentication mechanism.

Owner states follow UNOWNED → CLAIMED → RUNNING → STOPPED → PUBLISHING → RECONCILED → RELEASED. Claims require unique attempt, increasing epoch and a nonempty caller-supplied fencing-evidence reference. UNKNOWN blocks new claims. This records an assertion only; it does not prove physical fencing or implement takeover. An unverified authority is exposed as `AUTHORITY_UNVERIFIED`; a stored unacknowledged decision is exposed as `PENDING_ACKNOWLEDGMENT`; an acknowledged unknown owner is `RECONCILIATION_REQUIRED` and ineligible.

Each attempt has at most one uniquely named one-shot reservation; allocation includes historical debit and the full allowance, cannot exceed the ceiling and is never refunded. Loss of scratch leaves the reservation allocated. A cloned authority using the same advanced fake witness is rejected. This does not prevent a caller from fabricating a different fake witness, budget ID or authority; independent persistence and anti-rollback are outside the model.

Generation authorization checks the Phase 2B pointer shape, binding, parent and distinct generation ID. Publication follows GENERATION_AUTHORIZED → PREPARED → RECONCILED_COMMITTED. Reconciliation requires an exactly matching observed pointer and caller-provided verification assertion, and is eligible only after witness acknowledgment. Unknown publication becomes UNKNOWN_REQUIRES_OPERATOR_REVIEW. The model does not read storage, validate physical bytes, replace a pointer, prove durability or implement fallback.

## Offline fault coverage

The dedicated tests cover initialization, monotonic progression, stale and conflicting decisions, idempotent replay, chain and schema corruption, unavailable/failing/stale/ahead witness, authority rollback, pending acknowledgment, invalid ownership, attempt/epoch reuse, absent fencing evidence, budget overflow, duplicate reservation and launch, retained allocation after scratch loss, cloned state with an advanced witness, invalid publication, mismatched pointer, missing verification and pending commit. The synthetic assertions are not evidence of UIT fencing, durable storage or operator identity.

## Verification

Focused final: `.tools/python312/python.exe -m pytest tests/integration/test_trusted_control.py -q -p no:cacheprovider --basetemp=.tools/phase2c1-focused-v6` → 10 passed.

Full final: `.tools/python312/python.exe -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2c1-full-v6` → **923 passed, 2 skipped in 460.44s**. Skips: `tests/integration/test_local_snapshot.py:75` and `:205`, both because Windows symlink privilege was unavailable. Earlier full runs are not evidence for the final code.

The Phase 2B preservation baseline was independently rechecked: 46,744 files, zero changed or missing. This historical inventory does not include every later untracked artifact and is not a complete pre-Phase-2C.1 inventory. Phase 2B publication source and test hashes were also checked against `.tools/phase2b-evidence/final-code-hashes.json` before implementation and matched. No complete independent before/after scan of every pre-existing file was captured, so overall protected-artifact status remains UNVERIFIED.

## Limits and next phase

No real authority, witness, cross-process transaction, durable storage, external rollback protection, authenticated operator, Slurm integration, fence or network acquisition is implemented. No production call imports the synthetic module. Phase 2C.2 requires separate authorization and should design and test actual reservation/owner orchestration, including the chosen trusted persistence and operational fencing boundary. Phase 2C.3 must separately address real publication reconciliation and crash boundaries.

`PHASE_2C_1_IMPLEMENTATION = PARTIAL`; `CONTROL_CONTRACT = PASS`; `MONOTONIC_DECISIONS = PASS`; `FAKE_WITNESS = PASS`; `OWNERSHIP_STATE_MACHINE = PASS`; `BUDGET_CONTRACT = PASS`; `OFFLINE_REGRESSION = PASS`; `PROTECTED_ARTIFACTS = UNVERIFIED`; `PRODUCTION_AUTHORITY = NOT_IMPLEMENTED`; `UIT_EXECUTION = NOT_AUTHORIZED`; `PILOT_10000 = NO_GO`. PASS applies to the bounded synthetic tests only. Overall status remains PARTIAL because full pre-existing-file preservation was not independently established.

