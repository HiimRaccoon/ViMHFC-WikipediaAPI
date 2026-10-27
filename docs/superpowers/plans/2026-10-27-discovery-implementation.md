# Discovery experiments and source lifecycle — implementation plan

**Goal:** Ship offline-verifiable discovery alternatives and manual evaluation artifacts without changing historical runs or research semantics.
**Spec:** [Approved discovery design](../../designs/stratified-hyperlink-discovery-proposal-20261027.md); latest user request authorizes implementation and offline verification only.
**Architecture:** Read-only archived inventory → pure, versioned stratified/lead-neighbor cohort planner → immutable experiment manifest → isolated replay database → existing guarded historical provider. Human review packaging is separate from pair scoring. No discovery hint assigns primary type.
**Constraints:** qid-5/lead-4/quality-2.1/rank-1; snapshot2026-10-25T03:00:00Z; depth12; eight quota values still total20,000. User clarified: reporting alone may merge Location+Facility; Science/Technology stays separate/unmapped, not Creative Work. No network/pilot/commit/push in this task.

## Tasks and verification

- [x] 1. Quarantine lifecycle: RED repeat-resume must not enter worker/change queue attempts/timestamps; wrong review/case/receipt must fail. Add cache-only validated restoration in `pipeline/quarantine.py`, `runner.py`, `scheduling.py`.
- [x] 2. Missing associations: `source/association_audit.py` plus offline command; examine selected historical revisions, exact sitelinks, article identity and hashes. No title guesses/label matches, no negative absence proof from partial cache; do not pin results into real runs. Tests: unique historical match, duplicate matches, future/corrupt input, no-source coverage.
- [x] 3. Review pack: pure `pipeline/pair_evaluation.py`, deterministic29+50 stratified sample, immutable evidence/provenance/risk inventory, two blank external review sheets, protocol and weighted sampling metadata. Tests: exact allocation, reordered input stability, no primary fallback/no invented human labels, spans and changed source checksum refusal.
- [x] 4. Discovery planner: `pipeline/discovery_policy.py`, archived input adapter and experiment driver. Validate snapshot/input identities; legacy archived order, seed-only strata balancing and hybrid70/30; canonical dedup with all origins; eligible retained-lead-only frontier, verified historical target, declared per-seed caps; deterministic inventory/round/manifest identity, exhaustion/shortfall explicit. No live processing-order/cache membership decisions.
- [x] 5. Isolated execution/handoff: new discovery config/version, `PipelineStore.create` must not inherit an incompatible discovery cohort. Prepare separate DB/run per variant, stage size100→1000 only when user invokes. Source request cap/maxlag/circuit/integrity guards and exact rules retained. Do not auto-inherit run-scoped quarantine approvals.
- [x] 6. Offline archived-input comparison: equal unique-candidate budgets, cache-only coverage/eligible yield/graph/pairs/cost/RAM, unseen/missing source marked unverified. No extrapolation or live performance claims. Report six-core/eight-strata ambiguity explicitly; mapping is configuration, not a taxonomy rewrite.
- [x] 7. Docs and verification: sync contracts/spec/plan/tasks, run full offline suite, inspect preservation/checkpoint hashes, independent final review; publish PowerShell preparation/run/status/resume/report commands tested with offline fixtures. No real pilot execution.

## Review focus

1. Existing quarantined queue cannot revive evidence or increment attempts through another resume path.
2. Partial historical association inventory cannot establish global QID uniqueness or nonexistence.
3. A duplicate namespace/category/neighbor origin cannot consume two candidate slots or discard proof.
4. New discovery semantics cannot silently clone the old cohort via `reuse_run`.
5. Missing source/namespace evidence, budget shortage or optional sampling mapping must remain explicit; never pad a cohort or claim experimental yield for unknown records.

Native execution in current checkout: needed uncommitted source implementation/data are present here; no worktree or git operations will discard/copy away that state. Tests use isolated temporary stores. Record task decisions/evidence in `.tools/discovery-next/progress.md`.
