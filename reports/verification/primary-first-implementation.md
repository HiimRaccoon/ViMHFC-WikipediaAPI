# Primary-First, Optional-Subtype — implementation plan and ledger

User-authorized design change, 2026-10-25. Execute inline using writing-plans/executing-plans and TDD; one independent final review. No source API, actual pilot/production, commit or push. Preserve all three historical runs and their artifacts, including uncommitted qid-4 implementation.

## Approved intent and concrete decision policy

Primary classification is required; subtype coverage is optional. Version `qid-5` / `pipeline-5`, policy `primary-first-1`; retain lead-3, rank-1, existing roots/precedence, Fixed Snapshot, lead-only graph, quality thresholds and research targets (20,000 articles, 2,000 claims, existing NEI/human rules).

Stage A performs deterministic BFS using article P31 then class P279, never P106. Configured roots close only their own ancestor branch. Collect all proven candidates within depth 12; apply existing precedence. If primary frontier is empty, choose the highest-precedence proven group (or explicit no_supported_rule if none). If any required P31/P279 frontier remains, accept only when the best proven candidate is the first group in the **entire configured precedence**: no unknown branch can outrank it. Otherwise primary remains unresolved/ambiguous, even with a known lower-precedence root. Record the accepted decision and remaining branches. This is an explicit conservative upper-bound argument, not first/nearest-root selection.

Stage B runs after primary is proven. It searches P31/P279 for matching configured subtypes and, only for Person, P106/P279. Retain every positively proven subtype; do not require exhaustive taxonomy closure. Distinguish supported subtypes (with separate search completeness), policy-limited undetermined subtype, missing/invalid/transient optional dependency, closed search without configured subtype, and search not attempted because primary is unresolved/unsupported. Unknown subtype is not evidence of no subtype.

Primary acquisition owns the budget first; optional traversal can reuse its nodes and consume only the remaining union budget (2,048 unique logical QIDs total). Both stages retain depth 12. Optional acquisition failure is pinned with its role/reason; it never becomes primary missing evidence. Published manifest artifacts must still all validate/materialize for replay; missing physical declared optional artifact is an optional-role **input failure**, not a different classification under the same manifest. Acquisition frontier and physical artifact absence are distinct.

## Manifest, schema and memory design

New immutable manifest format records primary and subtype graph inventories/frontiers, decision policy, dependency roles, exact selected revisions/timestamps/raw hashes, full entity hash and evidence projection hash. Input fingerprint includes manifest ID/rules. qid-1/2/3/4 remain interpretable under their archived rules; new fields use a new result type so legacy dataclass serialization is unchanged.

Result retains outcome/primary_entity_type/subtypes/provenance/reason compatibility and adds primary_status/provenance/candidates/decision, subtype_status/search_status/provenance and policy_version. Classification completed means proven primary; does not imply complete subtype search. Eligibility still requires all other existing quality rules. Comparison still requires supported shared subtype/attributes; Bridge needs no subtype.

Compact evidence stores only historical identity, viwiki sitelink and consumed P31/P279/P106 statements, plus original source revision/hash references. Immutable manifest hashes bind both full source and projection. Materialization validates full raw content and verifies its projected hash before stage reuse; pure/frozen replay validates projection identity/hash and graph against the manifest. Do not drop validation. Release per-article prepared state, avoid duplicate preparation in builder/process, and keep compact evidence in stages/packages instead of repeating complete Wikidata payloads. Measure memory/serialization offline on archived data, without extrapolating production from a single warm replay.

Rejected alternatives: first/nearest root violates precedence; requiring all optional occupation branches defeats the approved policy; raising depth or relaxing quality is unauthorized. A future finer proof that an open primary branch cannot reach a higher group would require an explicit taxonomy contract; this implementation uses the conservative global precedence bound.

## Tasks and interfaces

- [x] P500: capture three-run/raw/report baseline; update SPEC and write concrete policy/plan before code.
- [x] P501: RED/GREEN primary/subtype classifier, bounded two-role manifest and result schema. New `pipeline/primary.py`; dispatch from existing dependencies without changing legacy semantics. Test direct Human+deep occupation, conflicts/dominance, P106-only, cycles/budgets/depth, missing optional, subtype provenance and compact evidence tampering.
- [x] P502: RED/GREEN source/provider/freeze integration and memory lifetime; validate full raw then compact evidence, role-specific input errors, matching fingerprints/resume/cross-run reuse, backward-compatible legacy and standalone package replay.
- [x] P503: RED/GREEN reporting and Bridge/Comparison/selection invariants; expose primary/subtype completeness/coverage and errors without promoting unsupported subtype.
- [x] P504: offline retrospective on exact archived 100 candidates/snapshot, all original43 breakdown, factual eligibility, graph/pair metrics (unavailable where historical link-resolution data is missing), isolated memory measurement and old-data preservation.
- [x] P505: synchronize PLAN/TASK/data model/contracts/README/replay instructions and scan for policy contradictions; preserve historical assessments.
- [x] P506: full offline suite, independent review corrections, exact PowerShell fixture smoke/status/report/logging checks and handoff; STOP.
- [ ] P507: USER runs new qid-5 Pilot100 and returns artifacts; not performed by agent.
- [ ] P508: explicit readiness review before Pilot1000; no automatic advance or production.

Pre-flight P501->P502: node identity and projection hashes must replay from both full raw and compact frozen evidence; manifest format/version dispatch is the compatibility seam.

Pre-flight P502->P503: existing consumers keep outcome/primary/subtypes/items keys; new primary/subtype states augment them. Frozen validation must replay the new type and preserve legacy behavior.

Pre-flight P503->P504: pairs consume only positively proven subtypes and resolved lead identities; retrospective cannot treat missing historical identity resolution as a negative edge.

Pre-flight P504->P505/P506: offline retrospective/memory results are explicitly not a pilot. Operator source is the latest actual qid-4 run, not an invented run ID/snapshot.

## Execution ledger

Ruling: user explicitly authorizes implementing the specified design and docs synchronously. Skill approval/commit defaults do not add a second approval gate or authorize commit/push. Work in current shared workspace, preserve existing changes, execute implementation inline and request one final independent read-only review.

Review focus: unresolved higher-precedence primary branch; source failure in optional traversal; modified compact evidence/full raw mismatch; frozen/current-run inputs versus mutable cache; memory retained by prepared graphs and repeated result serialization. Each is covered by regression or isolated offline measurement before acceptance.

P500 complete: baseline covers 3 actual runs, 1,477 compressed raw objects, 246 historical/report/config files and shared source/manifest tables. Actual database is opened read-only/immutable only.

P501 complete: initial new tests failed for missing module; implementation passed. Added regression exposed a separate subtype-path replay using the wrong budget reservation; fixed by pinning/rederiving optional provenance with the actual primary-first union reservation. New PrimaryFirstResult leaves legacy ClassificationResult serialization intact. Legacy tests that assert mandatory subtype closure now explicitly use their old policy; assertions were retained.

P502/P503 complete: source/provider tests failed for projection identity and prepared-state lifetime, then passed. RoleLoader stores failures per QID, uses existing content batch bounds and preserves required transient errors. Optional source frontiers are frozen in current run; a new run reacquires those inventories instead of indefinitely copying missing evidence. Compatible unchanged manifests/results still reuse. A regression demonstrated stale cross-run optional inventory before this correction. A parent manifest supplies the pinned root hint without decoding the parent's repeated full classification payload. Missing declared artifacts remain role-tagged input failures.

First full suite: 253 passed, exit0 (248.23 seconds). Later coverage/reuse regressions and final review still require a fresh final full run.

Ruling: retrospective exposed the historical article Tổ chức (Q43229) changing only because an initial implementation excluded a zero-property configured-root match. Preserve the existing explicit configured-root identity mapping and record its historical zero-property path; P106 still cannot prove primary. Added RED/GREEN regression. This avoids an unrequested taxonomy change; all other P31 siblings still obey the same precedence/frontier rule.

## Nghiệm thu cuối — Primary-First, Optional-Subtype

### Implementation và compatibility

Đã triển khai qid-5/pipeline-5. Primary bắt buộc, subtype optional; primary P31/P279 không phụ thuộc occupation P106. Direct configured-root identity tiếp tục dùng mapping đã có, với historical zero-property path. Nhánh primary chưa giải quyết chỉ được bỏ qua nếu winner đứng đầu toàn bộ precedence; các ca khác vẫn unresolved, không suy đoán.

Files trọng tâm: `pipeline/primary.py`, `source/primary_dependencies.py`, `pipeline/dependencies.py`, `source/dependencies.py`, `domain.py`, `pipeline/provider.py`, `pipeline/runner.py`, `pipeline/report.py`, `pipeline/manifest_report.py`, `source/revisions.py`, `cli.py` và configs. PrimaryFirstResult giữ outcome/primary/subtypes/provenance cũ, thêm primary status/candidates/decision/provenance, subtype status/search/provenance và policy version. Fingerprint chứa immutable manifest ID; format mới có inventory/frontier theo role, source/projection hashes và budget ownership. Manifest cũ không được diễn giải thành policy mới.

Full raw hash/identity/model/cutoff được validate trước khi tạo compact evidence. Standalone frozen replay validate projection hash và rederive graphs/decision, không phụ thuộc builder cache path. Historical references/hashes vẫn được giữ. Frozen tables/API/annotation exports không đổi; classification JSON là phần mở rộng. Legacy qid-1/2/3/4 records vẫn đọc được; qid-4 freeze validation được kiểm tra riêng. Không cần migration actual DB cho schema qid-5.

Đã đồng bộ SPEC (FR-009/012/016/024 và acceptance), PLAN, TASKS, data-model, contracts/interfaces, docs.md, README và pilot100-replay. Mô tả cũ trong lịch sử tasks/reports được giữ có version/context; không sửa assessment lịch sử. Legacy qid-4 audit script lấy archived qid-4 rules thay vì mutable defaults mới.

### Tests và independent review

Fresh full offline command sau mọi sửa từ review:

```powershell
$env:VIMFC_OFFLINE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& .tools\python312\python.exe -B -m pytest tests/unit tests/integration --basetemp .tools/primary-first/final-after-review -q --tb=short
```

**277 passed / 0 failed / 0 skipped; 310,03 giây; exit 0.** Khi tự chạy lại chọn basetemp mới. Log: `.tools/primary-first/final-after-review.log`; exit: `.tools/primary-first/final-after-review.exit-code.txt`. Default suite chặn socket thật; bao gồm lead-3 regressions, controlled exact-20,000 freeze/standalone verification. Không tạo corpus nghiên cứu hay chạy pilot thật.

Regression phủ Human+occupation sâu, missing optional, required conflicts/precedence/dominance, P106-only, positive/unknown subtype, primary-only Bridge/strict Comparison, cold/warm/order/restart, immutable current-run/cross-run dependency changes, historical hashes/cutoff, union budget/cycles/depth12/13, projection corruption và legacy/frozen compatibility.

Một independent read-only reviewer phát hiện hai P2, đều đã tái hiện RED rồi sửa: malformed P106 không được làm mất subtype proven qua P31; default materialization phải giữ QID/revision/role khi artifact thiếu/hỏng. Các regression mở rộng phủ registered/orphan corruption, wrong header/missing fields/conflicting objects, cached wrong page/site và valid batch peers. Reviewer xác nhận targeted rechecks; consistency gap cached identity cuối cùng đã được sửa với RED/GREEN và full suite cuối. Không còn finding đã biết chưa xử lý. Legacy error mode giữ nguyên khi không dùng pinned recovery.

### Retrospective — đúng 100 bài lịch sử, không phải pilot mới

Nguồn actual qid-4 run `0b8c25da-3bf5-4386-856d-d6b3b5dc46bf`; snapshot `2026-10-25T03:00:00Z`; cohort hash `96bb4880e587ecc1de2528db25bf80cb446c94e930280f56638205bb40c8a173`. Chỉ đọc archived revisions/raw cache; không gọi source APIs hoặc Builder/CLI pilot. [Per-article audit](primary-first-retrospective.json).

| Metric | Old qid-4 | Primary-First offline |
|---|---:|---:|
| Primary classified | 27 | 34 |
| Primary unclassified | 30 | 30 |
| Primary unresolved/ambiguous | 43 | 36 |
| Articles có supported subtype | 13/27 classified | 14/34 classified |
| Classification-related quality rejections | 73 | 66 |
| Eligible articles | 16 | 20 |
| Bridge candidates | 0 | unavailable; known lower bound 0 |
| Comparison candidates | 0 | 0 |

Trong 43 ca unresolved cũ: **7** nay đủ primary proof; **6** có primary graph khép kín, blocker cũ chỉ còn ở occupation/non-primary. X-Seed 4000 được proven Landmark/Facility thắng global precedence dù nhánh primary khác mở. Cả 7 có optional search chưa hoàn tất. **19** ca có candidates nhưng frontier có thể đổi precedence (bất định theo policy, không chứng minh ontology thật có conflict); **17** chưa có positive primary proof trong scope. Tổng **36** vẫn unresolved. Không thiếu archived primary/optional artifact trong scope đã xét; frontier ngoài depth không chứng minh historical revision không tồn tại.

Bốn ca mới eligible: 1ucid, 2pillz, K'Choi, 2Mex. Ba primary mới còn lại vẫn bị quality rules khác loại; không hạ thresholds. Tổ chức/Q43229 giữ supported theo direct-root mapping cũ.

Bridge đầy đủ chưa xác minh: **23** retained link occurrences của bài mới eligible chưa có historical identity-resolution records. Không tạo synthetic targets/links, không coi missing resolution là proof không có edge. Comparison count xác minh từ supported subtypes/factual attributes bằng 0, không fallback Person. Pilot mới có thể cần Wikipedia identity API requests; không hứa 0 requests. Không áp đặt target eligible/pair count.

Lệnh retrospective đã chạy, exit 0:

```powershell
& .tools\python312\python.exe -B scripts\verify-primary-first.py --database data\pipeline.sqlite --raw data\raw --source-report reports\pilots\0b8c25da-3bf5-4386-856d-d6b3b5dc46bf\100\metrics.json --output reports\verification\primary-first-retrospective.json
```

### Footprint và giới hạn measurement

Thay full Wikidata payload lặp bằng compact consumed evidence + source/full-entity/projection hashes; parent manifest hint tránh decode parent full payload; prepare một lần/release per article. Validation không bị bỏ. Shared raw giữ nguyên.

| Serialized payload 100 bài | Old qid-4 | qid-5 offline |
|---|---:|---:|
| Classification results | 94.776.131 bytes | 12.412.162 bytes |
| Process results | 95.494.052 bytes | 13.134.478 bytes |

Giảm khoảng **86,9%** classification và **86,2%** process payload. Đây là serialization measurement, không phải SQLite growth live. Projected catalog 1.014 items: 1.486.500 serialized bytes. Isolated RSS peak **168.828.928 bytes (~161 MiB)**; tracemalloc heap peak **88.862.141 bytes**. Scope gồm đọc old checkpoints, validate raw và pure replay/serialization, không Builder/CLI/network; khác scope với actual qid-4 peak574 MiB. Không tuyên bố đây là RAM live qid-5, không so throughput có tracemalloc với pilot, không ngoại suy Production. Chi tiết/resource samples nằm trong audit JSON.

Vẫn lặp compact evidence/manifest giữa một số checkpoint/package records để giữ standalone replay. Cần đo RAM/SQLite/I/O tại pilot mới và review trước Pilot1.000; chưa thêm shared-payload normalization sâu hơn.

### Preservation, handoff và readiness

[Preservation](primary-first-preservation.json): ba actual runs, 13 bảng/run (39 checks), sáu shared tables, 246 file lịch sử/config và 1.477 compressed raw files khớp baseline. Lead-3/rank-1/REVIEW.md giữ hash; roots/precedence/depth12/budget2048 giữ nguyên. Actual DB đọc ro/immutable khi WAL rỗng; không sửa dữ liệu, tạo actual run, commit/push hay gọi API.

[PowerShell handoff](../../docs/pilot100-replay.md) dùng `.tools/python312/python.exe`, source report qid-4 gần nhất và inherited snapshot/cohort/operator settings. Bước1 tạo bundle/new UUID khi người dùng tự chạy. Agent không tạo actual qid-5 run. Manifest/classification cũ không tương thích không reuse; raw/lead-3 stages tương thích có thể reuse.

Smoke-test offline install (`--no-index --no-build-isolation --no-deps`), prepare/create/status/report trên fixture DB `.tools/primary-first/operator-smoke/` exit0; bốn PowerShell blocks parse PASS. Full suite kiểm tra Tee stdout/stderr, native exit7, launch failure và từ chối context1000. Latest actual run-context pointer không bị smoke test thay đổi. Log fixture: `.tools/primary-first/operator-smoke-final.log` và `.tools/primary-first/operator-smoke-final.exit-code.txt`.

**READY để người dùng tự chạy Pilot100 qid-5 mới.** Chưa có live qid-5 pilot; chưa có quyền Pilot1.000/Production. Sau chạy cần gửi metrics/performance/template/classification/pair/manifest/fingerprint/**subtype-audit**, context/cohort, manifests/attempts, toàn bộ logs/exit-code. Missing declared optional physical artifacts vẫn phải khôi phục đúng pinned revision/hash; không đổi current-run graph âm thầm.

**STOP:** code + offline verification + documentation sync + handoff hoàn tất; P507/P508 để unchecked cho hành động/quyết định của người dùng.
