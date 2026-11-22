# Phase 2D.6 offline durable publication and reconciliation

## Implementation

- `backend/src/vimfc/pipeline/durable_publication.py` adds `capture_generation`, `FixtureQuiescence`, `PublicationVerification` and `DurablePublication`.
- `backend/src/vimfc/pipeline/transactional_control.py` requires the concrete filesystem verifier before storing reconciliation and again before witness acknowledgment. Generic recovery refuses to acknowledge a pending reconciliation without this verifier.
- `tests/integration/test_durable_publication.py` adds 33 synthetic cases covering hard exits, corruption, unchecked assertions, revocation, actual in-flight mock work and two owner epochs with the same control databases.

Capture verifies a separately supplied fixture quiescence contract and the acknowledged stopped owner before invoking the existing snapshot contract. It rechecks quiescence and unchanged authority after capture. Trusted fixture setup supplies the evidence verifier. The live-work test derives quiescence from a blocked mock request's completion, independently of logical STOPPED.

Publication requires acknowledged generation authorization, matching owner/attempt/epoch/job, expected prior pointer and quiescence. It acknowledges publish and PREPARED decisions before calling the existing Phase 2B publisher. Publication fault boundaries also recheck acknowledged PREPARED and quiescence.

The verifier checks the authority intent, owner, binding, runtime, target pointer and independently expected ancestry. It verifies current and immediate parent bytes using the existing publication recovery contract. It then copies the current generation into a fresh local audit directory and runs the full snapshot verifier there, including actual SQLite schema, checkpoint, bundle and provenance checks. Published SQLite is never opened. It rechecks pointer, bytes and quiescence afterward.

The existing reconciliation payload marker `verified=True` is retained for compatibility with the Phase 2C state machine. It is insufficient to invoke durable reconciliation. The authority invokes the concrete verifier before storage and before acknowledgment, including explicit recovery. Caller receipts are never consulted.

## Crash and recovery matrix

| Boundary | Explicit recovery behavior |
| --- | --- |
| Before PREPARED storage | No publication. Pointer remains prior. Operator disposition required. |
| Witness staged PREPARED, authority commit missing | Control state cannot be verified. Block for operator review. |
| PREPARED committed before/after witness ack | Pointer remains prior. Leave intent and files intact for operator disposition. |
| Before/after promotion, before pointer replacement | Pointer remains prior. No republish, retry, fallback, cleanup or automatic acknowledgment. |
| Pointer replaced, receipt creation incomplete | Verify actual target and ancestry. Record and acknowledge reconciliation only after verification. |
| Before reconciliation storage | Reverify before recording the decision. |
| Reconciliation staged in witness, authority commit missing | Block because the staged digest has no matching record. |
| Reconciliation committed, acknowledgment missing | Reverify actual storage and quiescence before acknowledging the exact pending record. |
| Witness acknowledgment completed | Reverify and return COMMITTED without adding a second decision. |
| Bytes, manifest, compatibility, ancestry, owner, pointer, witness or quiescence differs | Deny without advancing the witness. |

Hard exit tests use `os._exit` in separate Python processes at 16 boundaries. The subprocess explicitly denies socket connections. Recovery is performed through freshly opened authority and witness objects. Additional tests corrupt storage between initial verification and record storage, and between storage and acknowledgment.

## Two-epoch lifecycle

The fixture starts with historical debit 2 and ceiling 10. Epoch 1 reserves 3, dispatches 1 simulated unit, stops and captures g1. A child process exits after pointer replacement. A new process view verifies and reconciles g1.

The same control files release epoch 1, claim epoch 2 and reserve another 3. The fixture transfers g1, restores into a fresh local workspace and activates epoch 2 with separately verified synthetic termination evidence. Epoch 2 dispatches 1 unit, stops, captures g2 with parent g1, and publishes it. A child exits after reconciliation storage. A new authority/witness view verifies and acknowledges the pending record.

Assertions confirm current g2, previous g1, owner attempt a2/epoch 2, allocated budget 8, historical debit 2, both original reservations and spent values of 1 each. The g1 bytes, original workspace and bundle remain unchanged. Restored selection remains stale.

## Verification

Focused durable publication, transactional authority and earlier publication reconciliation suites passed 76 tests. Full offline regression passed 1092 tests with 3 skips in 473.46 seconds. All skips were existing Windows symlink privilege cases.

Both runs used `VIMFC_OFFLINE=1`, the existing socket guard and fresh local fixture directories. The full command was `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d6-full`.

## Targeted preservation

Pre/post SHA-256 matched for `generation_publication.py`, `local_snapshot.py`, `worker_activation.py`, `selection_epoch.py` and `test_annotation_builder.py`. The pre-existing annotation-builder patch retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. Tracked status showed only that pre-existing modification. `git diff --check` passed.

| Source/test | SHA-256 |
| --- | --- |
| `transactional_control.py` before | `4898E18C02DF7DE477C10A105FC6959FBA89D3E14096191EFA64B516B173313A` |
| `transactional_control.py` final | `851D01FD636055609C612F6B23ED41970BE2AC05EEEA3C9E2A2C8F30C2C16C6E` |
| `durable_publication.py` final | `C2A88F679BDB22298F23920A3C29B49C1CC14ED39C8F3C96BBF8F6AEE7374BA2` |
| `test_durable_publication.py` final | `5531610794C98185DF1E32ABEFECDF4C566A8AFB75CAC997D245BDD8BD553EAC` |

## Limitations

The verifier and quiescence provider are trusted local fixture dependencies. Low-level fixture APIs and direct control-file writes remain outside an adversarial security boundary. Storage must remain private and stable across verification and publication. This contract supplies no physical fencing or power-loss durability.

Filesystem and SQLite audit work runs under the authority write lock during record/ack verification. Mock transport remains outside that lock. Production verification latency, authenticated durable trust anchors, approved storage and fencing require separate design and validation. The existing production collection counter and request guard are unchanged. Process-local selection approval recovery remains unresolved from the earlier phase.

