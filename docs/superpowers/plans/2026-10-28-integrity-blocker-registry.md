# Integrity blocker registry and one same-run recovery

User attachment e8131f8d-1291-4a19-817c-261095f1b4f0 is the authorization: registry implementation, exact23658997 refusal after independent evidence review, and at mostONEsame-run recovery cap5000. No new run/cohort/fourth refusal/largerpilot/commit/push.

## Design

Use versioned sidecar SQLite and content-addressed diagnostic artifacts outside trusted raw. Canonical append-only event hashes link to previous hashes; each event is anchored in a new current-run `run_events` row. Old run events are immutable. Verification compares the complete anchor sequence and artifact hashes, detecting deletion, reordering, unanchored tail or body tampering. Cross-DB crash between event and anchor fails closed; no automatic truncation/repair. Pipeline DB anchors share the existing trusted metadata boundary, not a cryptographic human signature.

Observation contains exact source ref/scope/model/format/hashes, original response and failure receipt fingerprints, root/dependency context where known, invocation/cost provenance. UNKNOWN_INTEGRITY only transitions explicitly to REVIEWED_REFUSAL with a validated exact approval + actual authorization artifact, or VERIFIED_RECOVERY with independently hash-matching original payload proof. Registry itself never publishes evidence or overrides source validation/active refusals. Operational/semantic/missing-source states stay distinct and are never treated as integrity approvals.

The run entry point requires the registry after activation. Preflight validates known exact blockers before source work; source selections, fetches and cache-only dependency materialization consult it. Unknown mismatches retain the original IntegrityStop; opt-in response capture and an immutable immediate failure receipt are recorded without allowing diagnostic write failure to replace the original exception. All completed checkpoints keep the same semantic fingerprints. One sealed operator execution links all prior claims/receipts and caps additional charged attempts5000.

## Interfaces/files

- `pipeline/integrity_registry.py`: create/open/verify, observe mismatch/status, reviewed refusal/recovery transitions, exact-ref check/preflight, response/error capture and audit.
- `pipeline/reviewed_pilot.py`, `provider.py`, `source/revisions.py`, `source/dependencies.py`, `scripts/seed-only.py`: explicit optional operational registry integration; anchored runs cannot omit it.
- `tests/integration/test_integrity_registry.py`: RED/GREEN immutability/dedup/conflict/anchor/evidence/auth/scope/fractional cutoff/source/cache/restart/new failure/old approval compatibility.
- `.tools/blocker-registry/`: independent evidence review,790preflight/preservation, exact new approval, one-shot sealed operator, evaluation/handoff; no old artifacts overwritten.

## Execution ledger

- [x] Baseline43282durable files and old stable rows recorded before activation; independent exact case3 evidence reviewPASS.
- [x] RED/GREEN registry/event/artifact/transition/preflight/source integration controls;22registry cases plus13operator controls.
- [x] Full offline580PASS, independent35focused/operatorPASS, independent790replay and43282durable-file preservation before live.
- [x] Initialize scoped registry and bootstrap three existing observations; record two existing refusals and authorized exact case3 as additive transitions; no new trusted raw.
- [x] One separately claimed recovery cap5000: actual2134requests, native exit5/completed invocation;985processed162eligible13deferred2quarantined. No new unknown integrity/API failure. Source/integrity/acceptance incomplete; no further invocation.
- [x] Post-run985/985 replay/native pair equality;790old manifests/72569baseline rows/825old events preserved;43282file hashesPASS. Final native report exists but data/integrity/acceptancefalse. Main handoff `reports/verification/technical-review-handoff-integrity-registry-20261029.md`; no further invocation/10k/Production.

Ruling: work stays in the user-requested workspace with its private operational DB; no worktree/data migration. The explicit autonomous implementation/execution authorization supersedes extra design-approval prompts. Existing research semantic versions, snapshot, quotas and manifests remain unchanged.
