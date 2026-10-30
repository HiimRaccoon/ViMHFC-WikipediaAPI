# Seed-only wide inventory and bounded pilots

User execution authorization: the 28/10/2026 request explicitly authorizes implementation, offline tests, live listing acquisition and one staged Pilot100 -> Pilot1000; no further design approval is required. No commit/push or larger pilot.

## Design and constraints

Keep qid-5/lead-4/quality-2.1/rank-1, snapshot 2026-10-25T03:00:00Z, ontology depth12, 8 quotas totaling20000. Location/Facility combine only in reporting; Science remains separate. Legacy/Hybrid remain readable for historical reproducibility, with no new development/comparison runs.

Acquire up to80000 unique ns0 page identities through serial category BFS (bounded category depth/count) and allpages exploration fallback. Category membership and current titles are discovery observations, never historical evidence. Persist responses, hashes, continuation and dedup in a NEW SQLite inventory checkpoint atomically. Freeze observed inventory before sampling; no historical dependency fetch during listing. Stop on source failure or request cap without an automatic retry loop.

Use versioned seed-only-2 weighted soft quotas, deterministic hashed order and 20% exploration slots, redistributing exhausted buckets. Freeze exactly1000 candidates for a NEW isolated run. Current discovery titles cannot assert historical titles; the provider resolves page identity at snapshot and validates historical namespace independently. Existing sealed historical cohorts keep their strict title guard.

Exploratory progression is separate from completeness/acceptance. It can authorize only100 ->1000 on this run/config/cohort, after offline replay of the100 prefix. Only explicitly enumerated missing-hint pages and the exact reviewed refusal can explain incompleteness. Unknown failures, transient source frontiers, mismatched checksums, active host circuit or new integrity stop execution. Production gates remain unchanged.

## Tasks / ledger

- [x] T1: Offline RED/GREEN tests for resumable listings, dedup/provenance, category cycles/cursors, namespace and finite budgets; implement source inventory.
- [x] T2: Deterministic scalable Seed-only planner and preparation compatibility; test quota fallback/exploration, current-vs-historical title guard, legacy preservation.
- [x] T3: Scoped exploratory progression + bounded driver; test refused progression, unchanged flags, checkpoint/quarantine restoration and source hard stops.
- [x] T4: SPEC/PLAN/TASK/operations sync; final500 offline tests PASS, exit0,325.12s. Independent review source-boundary/replay/cap findings corrected; no remaining live blocker.
- [ ] T5: Bounded live inventory acquisition, freeze, verify hashes; new isolated1000 cohort/run and exact refusal approval if independent evidence review PASS.
- [ ] T6: One Pilot100; offline reproducibility/integrity/resource checks; if safe, one Pilot1000 same run/cohort. Stop safely at any blocker/cap.
- [x] T7: Detailed [report](../../../reports/verification/seed-only-wide-inventory-20261028.md) records6848 actual identities/75attempts/2HTTP503, preserved checkpoint and no new run/pilot. Stop on second source failure; T5/T6 remain incomplete, not silently marked done.

Budgets: inventory at most800 physical HTTP attempts, no application-level retry rounds, one attempt per listing query and no probe in cooldown; Pilot100 at most3000 attempts; Pilot1000 at most10000 attempts; retry_rounds0, scheduler wait60s, HTTP existing limits and maxlag5/Retry-After unchanged. These are upper bounds, never targets. Old runs/raw objects are read-only inputs; private database/cache copies receive new work.

Review focus: continuation replay/cycles; cross-stream duplicates and changed titles; category membership after snapshot; expired/inherited negative hints; permissive progression accidentally opening production; quarantine evidence revival; report exports included in memory measurements.

Operational ruling after live attempt28: viwiki returned transientHTTP503 with persisted retry deadline02:22:00.707831Z; no content/hash inconsistency or accepted response occurred. Allow exactly ONE controlled inventory recovery after that deadline, under the original800 cumulative persisted attempts, retaining cursor/observations. No product rules changed, no HTTP attempt limit increased. Any further failure stops acquisition/pilots. This finite source recovery falls within the user's explicit retry/resume authorization; it is not an unbounded application retry loop. Original invocation/log/exit preserved.
