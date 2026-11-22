# Phase 2E.3 — Offline unified budget transport adapter

Scope: current accepted Phase 2D.7 working tree, retaining the annotation-builder fixture patch verified in Phase 2E.1. This phase uses local synthetic fixtures only. No production acquisition caller, CLI, request guard, source policy, selection policy or scheduler integration changed. No SSH, UIT, Slurm, NFS, dependency installation or Git mutation was performed.

## Files and contract

- `backend/src/vimfc/pipeline/budget_transport.py`: isolated `SyntheticBudgetTransport`, final-request fingerprint, and HTTP launch/charge/outcome validators.
- `backend/src/vimfc/pipeline/transactional_control.py`: optional HTTP policy on the existing synthetic launch; optional HTTP metadata and validation callback on the existing dispatch closure; witnessed `http_outcome` decisions. Legacy synthetic dispatch retains its existing behavior outside pinned HTTP mode.
- `tests/integration/test_budget_transport.py`: HTTPX boundary, accounting, races, rollback and process-exit tests.
- This handoff. Local final suite log: `.tools/phase2e3-regression.log`.

The transport accepts exactly an HTTPX `MockTransport`, checked again at admission and dispatch. Trusted fixture setup supplies authority, budget/workspace/run/bundle identity, workspace limit, invocation ID/limit and mock handler. A fresh one-shot launch creates a process-local worker capability and TTL. Launch pins workspace policy in durable state and invocation policy in its acknowledged record. HTTP mode refuses legacy unbound dispatch; subsequent HTTP launches cannot change the pinned workspace policy or reuse an invocation ID.

`authorize(request, request_id=..., expected_version=...)` pins a request and creates an opaque process-local token. Actual durable authorization occurs in `handle_request()`. The authority decision ID is the lifetime request ID; its dispatch payload carries owner/operator/job/attempt/epoch, launch digest, reservation and allowance, exactly one unit, pinned policy, canonical request and SHA-256, and charge timestamp.

The final fingerprint includes GET method, HTTPS scheme, `vi.wikipedia.org`, effective port 443, `/w/api.php`, sorted query pairs, encoded URL, raw headers in order and empty-body SHA-256. Exactly one `maxlag=5` and a matching Host header are required. The encoded URL additionally pins duplicate-parameter ordering and percent-encoding differences. URL userinfo, fragments, other endpoints, nonempty bodies and changed requests are rejected. HTTPX hook mutation is checked at the transport boundary.

## Admission and accounting

1. Consume the process-local token once and verify the final request.
2. Read verified authority and conservative workspace counters. A restored lower counter grants no extra allowance; an unexplained counter above durable accounting blocks for cutover review.
3. Wait outside the write transaction for durable pacing. Charge and completion timestamps survive restored local `last_request`. Backward clocks beyond the existing five-second tolerance fail closed.
4. Validate owner/launch/TTL, request pin, invocation/workspace/reservation limits and expected version; commit one irreversible unit and acknowledge its witness.
5. Revalidate the exact request and current authority at final admission. The grant must remain current. No transport executes within the authority SQLite write transaction.
6. Call the mock handler exactly once with a frozen copy of the admitted request. Stop after admission cannot physically retract this in-flight operation; mutations to the original request cannot redirect it.
7. Record and witness a response/timeout/error outcome, bound to the original grant digest. Store status/body digest where available and a completion timestamp. This is audit information, never a refund.

`charged = historical_debit + sum(spent)`; `allocated = historical_debit + sum(reservation allowances)`; `charged <= allocated <= ceiling`. Reservations remain allocated after stop/release. Workspace and invocation limits add constraints; they cannot expand the lifetime reservation. Independently checking the state-pinned workspace policy also prevents a low-level activation assertion from expanding that policy.

Pacing waits at least one second after the preceding durable completion, conservatively covering transport or witness latency. An attempt without an acknowledged outcome blocks further HTTP attempts, including a fresh request ID. No automatic operator disposition, replay, relaunch or refund exists.

## Failure matrix

| Boundary or failure | Behavior |
| --- | --- |
| Missing/foreign/used token; changed request; invalid host/maxlag/method/URL | Zero mock transport calls. |
| Duplicate ID, including altered payload | Zero additional calls or spending. |
| Invalid/stale owner, epoch or TTL; stopped/unknown owner; unavailable witness or rollback | No new admission. |
| Invocation/workspace/reservation exhausted; restored counters below ledger; policy downgrade | Limits remain durable; denied attempts add no transport calls. |
| Crash before charge staging | No charge or HTTP. Reservation remains allocated; fresh process cannot relaunch the old worker. |
| Witness staged, authority commit interrupted | Mismatched state blocks; no automatic recovery or reset. |
| Charge committed, acknowledgment interrupted | Unit remains spent. Explicit authority recovery can acknowledge, never dispatch. |
| After acknowledgment/before admission | No HTTP on interruption or revocation; charge stays spent. |
| After admission/during transport/after transport | At most one mock call. Delivery may be uncertain; charge stays spent and is not replayed. |
| Outcome staging/commit/ack interrupted | Preserve the charge; block new attempts while unresolved or unverified. |
| HTTP timeout, 429, 5xx, maxlag or continuation | Each explicitly authorized retry/continuation uses a new lifetime request ID and another unit. No automatic HTTP retry. |

Hard-exit tests use `os._exit(71)` in separate Python processes at 16 bounded charge/admission/transport/outcome points. Restart tests create fresh SQLite authority/witness objects and confirm that no token, worker launch or allowance is resurrected. Additional tests verify blocked mock transport allows a concurrent authority stop, before/after-admission stop semantics, real HTTPX request hooks, all three durable caps, two owner epochs, and pinned-policy downgrade rejection.

## Targeted preservation

Only the isolated adapter, transactional dispatch extension, dedicated tests and handoff changed. Pre/post SHA-256 matched for existing transactional tests, annotation-builder tests, collection implementation, MediaWiki client, trusted-control model and activation module. The annotation-builder diff retains Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. Tracked Git status retains only that pre-existing modification; phase modules are already untracked relative to the old local HEAD. No repository/data inventory was performed.

| Source/test | SHA-256 |
| --- | --- |
| `transactional_control.py` before | `851D01FD636055609C612F6B23ED41970BE2AC05EEEA3C9E2A2C8F30C2C16C6E` |
| `transactional_control.py` final | `D8E7FE34B0C7D3AF890E1245871436C6EA92AB46E4475FB3E9C7F04A5A0C27DB` |
| `budget_transport.py` final | `1C189AB527EE02B1446E6E2F9A08A9B339708B4777B1CDB78E45FD0ADCA61B98` |
| `test_budget_transport.py` final | `BB3B5F0063D783436981D8ECFBB2CA9AECAD7FFF2E6F08E03617BD9D02D6273A` |

## Final verification

- Final focused run: **202 passed** in 108.36 seconds. Includes all **78 new adapter cases**, existing transactional control, worker activation, selection binding/recovery and durable publication tests.
- Final full offline run: **1199 passed, 3 skipped** in 511.82 seconds. The three skips are existing Windows symlink privilege cases in backup recovery and local snapshot tests. No failure occurred.
- Both runs used `VIMFC_OFFLINE=1`, the existing `.tools/python312/python.exe -B`, pytest `-q --tb=short -p no:cacheprovider` and separate local synthetic `--basetemp` directories. Final full basetemp: `.tools/phase2e3-regression`; full output: `.tools/phase2e3-regression.log`.
- Earlier runs superseded during boundary hardening are not used for the final verdict. Final implementation/test hashes above were fixed before the reported final runs and verified afterward.
- Targeted preservation checks matched all nine expected final/protected source/test hashes. Annotation-builder diff and tracked status were preserved; `git diff --check` passed.

## Remaining limitations

This is a cooperating local fixture model, with process-local capabilities and synthetic SQLite witness trust. Python private APIs, direct file edits, malicious mock handlers and direct calls to another transport are outside its security boundary. Trusted installation on every real egress path and independently protected production trust anchors are not implemented. Coordinated rollback of both control databases is not detected by this model.

The adapter supports one workspace-bound authority and explicit fresh launches; it is not a global multi-workspace production ledger. Historical-debit/counter cutover still requires separately verified expectations. Workspace-counter fixtures are small dictionaries; production counters and restore APIs are not wired to this adapter.

Existing source retry/cooldown/Retry-After/maxlag classification remains unchanged and unintegrated. Tests explicitly schedule fresh fake attempts; the adapter records outcomes and enforces conservative pacing without becoming an acquisition retry scheduler. Real HTTP, proxy behavior, hidden lower-level transport retries, native Linux behavior of this new adapter and production power-loss durability were not validated here. The earlier Phase 2E.1 Linux PASS predates these changes.

No physical fencing is supplied. Already admitted I/O can remain in flight after stop/revocation. Unresolved attempts require operator disposition; this phase supplies no automatic resolution or next-batch advancement.

```text
PHASE_2E_3 = PASS
DURABLE_HTTP_ATTEMPT_CHARGE = PASS
REQUEST_IDENTITY_BINDING = PASS
DUPLICATE_REPLAY_REJECTION = PASS
CRASH_RETRY_ACCOUNTING = PASS
RESTORE_BUDGET_PRESERVATION = PASS
ZERO_TRANSPORT_ON_DENIAL = PASS
OFFLINE_REGRESSION = PASS
PRODUCTION_INTEGRATION = NOT_AUTHORIZED
PILOT_10000 = NO_GO
```

PASS applies to the offline synthetic contract only. Production trust, egress enforcement, physical fencing and historical-accounting cutover remain separate gates.
