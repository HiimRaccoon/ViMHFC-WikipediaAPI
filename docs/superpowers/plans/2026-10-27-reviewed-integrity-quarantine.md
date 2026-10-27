# Reviewed Integrity Quarantine Implementation Plan

> Execute serially using the existing source/provider/scheduler seams; no worktree, commit or push. User approved the reviewed-quarantine design and exactly one bounded Pilot1000 invocation after offline tests PASS.

**Goal:** Continue independent candidates without treating reviewed bad revision23640705 as verified input.

**Architecture:** An explicit run/snapshot/config-scoped operational review verifies the captured response hash and exact revision metadata. A non-SourceUnavailable quarantine exception escapes optional dependency handlers before raw materialization. Builder converts it to a non-evidence candidate record; scheduler/reporting exclude it from processed/data-complete and preserve an idempotent audit receipt. All other integrity failures retain the hard stop.

**Constraints:** qid-5/lead-4/quality-2.1/rank-1, depth12, snapshot2026-10-25T03:00:00Z, immutable manifests, cohort1000/order, eligibility and multi-hop rules unchanged. Preserve existing273 completed checkpoints and all historical runs/raw artifacts. No10k/Production.

- [x] Add RED tests: scoped review/evidence validation; no bad raw/manifest/classification even for optional dependencies or warm cache; unknown errors stop; counts/gates; partial checkpoint resume and repeated audit identity.
- [x] Implement operational review in `pipeline/quarantine.py`, source/provider hooks, scheduler/Builder status/audit integration, opt-in reviewed-driver flag. Do not rewrite the existing interrupted stage when cached selection already identifies the bad revision.
- [x] GREEN focused46/full419 offline tests; independent review; sync SPEC/PLAN/TASK/contracts. Snapshot11,122 durable artifacts and row digests before live work. Existing replay DB/WAL pre-run byte preservation PASS.
- [x] Run exactly one authorized guarded1000 invocation8e14eed7-5eb6-4ea6-ac87-7468e9998acd;796/3000 physical requests, no errors/retries/waits,273 restored plus718 newly processed and1 quarantine. Exit5/data incomplete; no extra invocation.
- [x] Offline991 replays and3,262 raw dependency revisions PASS;48,469 rows/11,122 durable files preserved. Immutable attempta99d9a80-c559-47d4-bcf1-813102711b73 plus comparison/quarantine audit and handoff published. No Pilot10000/Production or commit/push; STOP.
