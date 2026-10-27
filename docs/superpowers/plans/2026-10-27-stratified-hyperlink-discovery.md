# Stratified + Hyperlink-aware Discovery Implementation Plan — đề xuất

> Kế hoạch proposal ban đầu. Controlled frozen-inventory experiment đã được người dùng yêu cầu và triển khai theo [execution plan](2026-10-27-discovery-implementation.md); [protocol](../../discovery-experiments.md). Online adaptive rounds/full broader listing acquisition và live experiments trong plan này vẫn future work. Các checkbox chưa đánh dấu không phải task đã hoàn thành; không commit/push hoặc live work tự động.

**Goal:** Tăng quota-usable eligible yield và useful lead connectivity, giữ nguyên research requirements.

**Spec/design:** [Discovery proposal](../../designs/stratified-hyperlink-discovery-proposal-20261027.md). Ontology workstream: [evidence audit](../../../reports/verification/classification-ontology-design-20261027.md).

**Architecture:** Versioned seed/hint inventory → deterministic stratified round → current historical provider → frozen eligible frontier → next round. Persistent logical manifests quyết định candidate membership/order; physical cache và worker speed không được quyết định semantic outputs.

**Tech stack:** Python/SQLite hiện tại, historical MediaWiki/Wikidata API client, immutable raw storage và manifest checks. Không thêm LLM/Cross-Encoder hoặc tăng concurrency.

**Constraints:** current run666068c8 giữ nguyên; discovery semantics mới dùng run/config/discovery version mới. Qid-5, depth12, lead-4, quality-2.1 và rank-1 giữ nguyên cho discovery-only experiment nếu còn phù hợp. Root/decision/quality/ranking changes là version boundary riêng. Snapshot2026-10-25T03:00:00Z, corpus20.000/claims2.000, NEI/human annotation policy và Lead-only Graph không đổi.

## Task1 — Review contracts, versioning và immutable inputs

**Files dự kiến:** `docs/specs.md`, `docs/plans.md`, `docs/data-model.md`, `docs/contracts/interfaces.md`; additive storage support trong `storage/pipeline_store.py`; fixtures mới.

- [ ] User review seed/neighbor mixture, inventory/round schemas, source budgets và stopping/pausing policy; chưa bật70/30 như default.
- [ ] RED tests cho wrong snapshot, changed seed catalog/weights/rules/frontier hashes, legacy input compatibility và conflicting sealed-round publication.
- [ ] Define `discovery_inventory`, `discovery_round`, `candidate_origin`, `frontier_proof` records; pin selected source identities, inventories, ordering/tie keys và statuses. Store references/hashes thay full repeated articles.
- [ ] Implement additive migration hoặc separate new-run DB; preservation test chứng minh historical rows/files không bị rewrite. No silent reuse of old derived discovery state.

**PASS:** approved input identity đủ để rebuild candidate list/order; không thể sửa một sealed round; legacy runs vẫn đọc được.

## Task2 — Stratified seed planning từ archived hint inventories

**Files dự kiến:** `pipeline/collect.py`, module mới `pipeline/discovery_policy.py`, relevant source fixtures/config schema; `tests/unit/test_discovery_policy.py`, `tests/integration/test_discovery_rounds.py`.

- [ ] RED fixture có category, namespace và duplicate page hints: expected stratum không được trở thành primary classification; cap/floor/budget và unique page count đúng.
- [ ] Pin source listing inventories/cursors/buffers, canonical resolution outcomes và every origin. Stable sorting/ties qua seed/stream/round/page ID.
- [ ] Define stratum deficits/yield estimates ở round boundary, with explicit unknown-yield exploration, total cap và exhausted-stream fallback.
- [ ] GREEN tests đảo order API listings/worker completion, cursor continuation restart, duplicate origins và exhausted narrow categories; exact same logical inputs cho same page IDs/order.

**PASS:** neither labels/category names nor cache latency assign entity group or silently replace source failures; exposure và denominator accounting rõ.

## Task3 — Lead-only frontier, canonical dedup và hub controls

**Files dự kiến:** proposed `pipeline/discovery_frontier.py`, current identity/source interfaces và round storage; `tests/unit/test_discovery_frontier.py`, integration fixtures.

- [ ] RED fixtures phân biệt prose/infobox/hatnote/body links, unresolved extraction, ineligible/quarantined seeds, redirect/move chains, self links và namespace ambiguity.
- [ ] Produce frontier references only from validated eligible seed leads; canonical historical target resolution, source/target snapshot/ref/hash, sentence/link spans.
- [ ] Dedup target pages và keep multi-parent origins; deterministic per-seed/per-hub/per-series caps. Never synthesize link or infer target primary/subtype from source.
- [ ] GREEN: warm/cold cache, same title different historical identity, title punctuation, crossed snapshot, duplicate canonical targets, unrelated invalid cache; graph edge chỉ xuất hiện khi cả endpoints eligible.

**PASS:**804 baseline resolved frontier targets vẫn chỉ là hints; không tự được admit hoặc counted eligible.

## Task4 — Deterministic rounds, resume và operational gates

**Files dự kiến:** `pipeline/runner.py`, `pipeline/scheduling.py`, `pipeline/provider.py`, `cli.py`, existing reviewed-quarantine/source controls.

- [ ] RED: slow/failed host hoặc interrupted worker không đổi round candidate membership/order; pending round không mở rộng từ opportunistically completed seeds.
- [ ] Implement explicit pause/seal-incomplete policy, leases/idempotent publish và compatible checkpoint reuse. Deferred/quarantined entries giữ denominator và audit receipts.
- [ ] Keep maxlag5, Retry-After, circuit/preflight, finite request/wait/retry budgets và serial host concurrency. No retry before negative-hint TTL.
- [ ] GREEN: restart at each boundary, reordered worker completion, exact-once candidate/stage identity, sticky reviewed quarantine and hard stop on unknown integrity.

**PASS:** inputs thay đổi phải tạo new identity/run; source outage không thể chọn seed universe theo speed/cache.

## Task5 — Measurement và independent evaluation

**Files dự kiến:** `pipeline/report.py`, manifest/audit reporting, external assessment schema/docs, report fixtures. Không thêm human-review workflow vào app.

- [ ] RED denominator tests: candidates/processed/eligible, deferred/quarantine overlaps, per-origin/actual-group separation, quota-usable yields, duplicate hints.
- [ ] Implement graph coverage/WCC/isolates/degree; comparison associations separate from directed lead edges; pair exposure/concentration and structural3-document paths never labelled validated hops.
- [ ] Report new/restored work and warm/cold source scope, exact input/replay hashes, RAM through exports và API costs. Unknown human yield = unassessed, not0%.
- [ ] Freeze proposed79-pair external review sample and inclusion probabilities; human claim/evidence reasoning, scope/date/subject, title-only and one-fact shortcuts, genuine single-document multi-hop handling.
- [ ] GREEN fixture tests: weighted strata aggregation, shared-endpoint uncertainty handling, nonzero heuristic score not automatic acceptance, claim-vs-pair denominators and unchanged NEI construction.

**PASS:** no circular ground truth from ranking predictions, no synthetic links/claims/labels, no metric can silently grant pilot acceptance.

## Task6 — Offline ablation rồi xin phép staged pilots của run mới

- [ ] Archived-inventory control for legacy, seed-only, proposed mixture: same snapshot/semantic rules, declared unique-candidate/request budgets and cache conditions.
- [ ] Evaluate type coverage, eligible yield, graph/pair coverage và costs; new discovery cohorts khác nhau không được gọi là same-input replay. Preserve original1.000 cohort as a control, not a modified current run.
- [ ] Full offline suite and independent review PASS, preservation verified, unresolved/source/precision limits documented.
- [ ] User review before any new-run Pilot100 → Pilot1.000. Pilot10.000/Production remains a separate explicit gate; current task does not authorize these runs.

## Separate ontology track — không gộp ngầm vào discovery

- [ ] Review historical meaning/inclusion/exclusion examples cho proposed near-domain roots; no adding generic entity/object roots or blindly pruning EMMO/BFO branches.
- [ ] For stronger dominance certificates, define independently supported scope/proof of every relevant higher-precedence possibility. If no sound certificate exists, keep unresolved;178 single-supported-group articles are not automatically recoverable.
- [ ] Version maps/decision semantics and manifest schema as needed; new run. Matched archived-cohort retrospective plus separate held-out, group-stratified precision review.
- [ ] Tests: sibling/multi-root precedence, positive path requirements, P106 never proving Person, invalid/missing/wrong-snapshot certificate, depth12/13, cycles/budget and cache invariance; memoization cannot extend semantic depth or imply coverage gain.

All future tasks above remain unchecked until implementation and evidence exist. Research targets, eligibility thresholds and human annotation requirements are not changed by this plan.
