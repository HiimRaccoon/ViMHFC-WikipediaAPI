# Inventory503 reliability and one recovery

Explicit user authorization28Oct: implement/test, then ONE new recovery on existing listing-checkpoint.sqlite, old75 attempts included in total800. Stop on first new503, unexpected source/integrity/circuit/budget failure; no pilot/Production/commit/push. Conditional freeze only at >=60000 valid identities. No source switch/dump merge.

## Design

Keep seed-only-2 sampling/snapshot/8quotas and qid-5/lead-4/quality-2.1/rank-1 unchanged. Add operational inventory-host-1 pacing1second between request starts, one HTTP attempt per query, persistent host cooldown/circuit, capped exponential backoff+jitter for NEXT explicitly authorized recovery. Never retry a new503 in this invocation. Retry-After is a lower bound even above backoff cap. Add per-checkpoint process-aware lease; cannot overlap requests or reset budget after crash. New diagnostics tables do not rewrite old attempts/responses/origins.

HTTP diagnostics record allowlisted headers/context, status/endpoint/body SHA256 and proxy configuration booleans, without cookies/tokens/contact credentials. Old response origin remains unproven when headers/body were not captured. ResourceSampler covers acquisition, validation, optional freeze and outcome export on both success/failure; final receipt/telemetry flush declared outside measurement.

## Ledger

- [x] Read old logs/client/checkpoint and build RED regressions:7failed, exact missing diagnostics/pacing/telemetry symptoms.
- [x] Implement diagnostics, durable operational host policy, atomic attempt receipts and failure-inclusive telemetry. Initial focused22PASS.
- [x] Expand boundary regressions; final514 offline tests PASS/exit0/328.66s. Independent review lease/publication/telemetry findings reproduced then fixed; no remaining live blocker.
- [x] Read-only preservation/old75 row/provenance baseline, policy/snapshot/host/resource preflight:73responses/6848identities validate;33650 durable-file baseline; cooldown expired.
- [x] One actual recovery same checkpoint, invocation4902be55-c784-4ed5-b505-20d74ac934ae: first/new attempt76 returnedHTTP503; stopped withcounter76/800 and6848identities, no freeze/minimum not met. No second invocation.
- [x] Offline old-row/cursor/hash/historical-file preservation PASS and resources captured on abort; official dumps proposal and [report](../../../reports/verification/inventory503-reliability-recovery-20261028.md) handed off. STOP; no pilot/source switch/commit/push.

Hypotheses: old source503 could be upstream/proxy/load; old logs cannot select one. No proactive pacing is confirmed in client/collector, but is not proof that rate caused503. Telemetry-loss root cause is confirmed: exception exits before write. Mock503 controls reproduce missing diagnostics and prove fixes, not actual source recovery.
