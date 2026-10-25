# Deterministic Dependency Manifests — Implementation Plan and Report

> Implement inline with `writing-plans` / `executing-plans`, TDD per task and one independent read-only review at the end. The user explicitly requested implementation without another approval pause. No commit/push or real pilot is part of this work.

**Goal:** Fix F1–F5 from [REVIEW.md](../../REVIEW.md) with deterministic acquisition, immutable versioned dependency manifests and manifest-bound checkpoint reuse.

**Architecture, decided before code:** `qid-4` uses the same bounded logical BFS for cache and network: depth 12, 2,048 logical nodes, configured roots closing only their own ancestor branches, every required sibling retained. Cache never extends semantic scope. Acquisition pins revisions/content hashes and records policy/history frontiers, publishes a canonical manifest atomically, and only then materializes/validates its declared artifacts and classifies. Classification and outer process fingerprints include the manifest identity. Published input bindings are immutable within a run; incompatible inputs require a new run. Legacy `qid-1/2/3` interpretation remains available.

**Trade-off:** Strict depth-12 scope deliberately gives up `qid-3`'s ambient warm-cache extension. The previous 47/47/6 retrospective is not an acceptance target. Coverage may be lower; policy-limited cases remain unresolved rather than being guessed supported/unsupported. No network-depth increase, new taxonomy or eligibility relaxation.

**Invariants:** same logical manifest/rules -> same classification and eligibility; missing/invalid manifest artifacts block classification instead of shrinking its graph; only selected revisions <= snapshot are accepted; negative lookup evidence differs from unmaterialized input/policy frontier; unrelated invalid raw cache is not inspected by the article dependency reader; raw/cache paths and lookup wall times are not logical input identity; old run rows/reports/raw data remain immutable.

**Spec:** User implementation request, [REVIEW.md](../../REVIEW.md), existing `docs/specs.md` and `docs/plans.md`. Research requirements remain unchanged. Python 3.12/SQLite/HTTPX fixtures; no new product dependency.

## Tasks and verification gates

- [x] Q400: record design/invariants/trade-offs and immutable baselines before implementation.
- [x] Q401: `pipeline/dependencies.py`, source acquisition/materialization: RED tests for manifests, deterministic depth/budget/cycles, source states, missing artifact, corrupt metadata, unrelated-cache independence; implement and verify GREEN.
- [x] Q402: `storage/pipeline_store.py`: immutable atomic manifest publication and historical root binding; RED/GREEN tests for interruption, hash corruption, incompatibility, cross-run reuse.
- [x] Q403: provider/runner/freeze/report integration: manifest-bound classification and process dependencies; downstream invalidation; legacy/frozen compatibility; manifest/fingerprint/completeness audits. RED/GREEN provider/checkpoint/end-to-end tests.
- [x] Q404: version `qid-4` / pipeline config; retain `lead-3` and taxonomy/ranking/thresholds. Re-evaluate all 43 archived cases offline with bounded manifests, including original 37/6; preserve legacy regression fixtures.
- [x] Q405: full offline unit/integration suite, independent review/fix pass, old-data fingerprints, tested PowerShell handoff and STOP before a real run.

## Interface and file map

- `pipeline/dependencies.py`: logical article identity, canonical manifest construction/validation and pure `classify_manifest(manifest, items, rules)`; no source network.
- `source/dependencies.py`: deterministic acquisition from `RevisionSource`, scoped validation, pinned-artifact materialization; no global mutable cache index.
- `PipelineStore`: content-addressed manifest records + immutable run/article/request bindings; root hints pinned per article and validated against historical entity data.
- `HistoricalProvider.process_dependencies(candidate)`: prepare manifest/materialization before outer process cache lookup; `process` uses the same prepared input and manifest identity.
- Runner/report/CLI: existing report/status commands plus manifest/fingerprint artifacts; existing bounded PowerShell logger retained and tested.
- Tests: new manifest/source/store/provider cases plus legacy suites and existing bridge/comparison/selection/freeze tests.

Review focus: incomplete/invalid artifacts, cache warmed by other articles, input changed under a current-run checkpoint, deep siblings hidden by roots, corrupted unrelated cache and loss of legacy interpretation. No implementation may create a real pilot/run to test these conditions.

## Execution ledger

Ruling: use the requested current workspace and preserve the existing untracked `REVIEW.md`; no branch/commit/push operations. All test stores are isolated and real data is read-only during audits.

Ruling: root association is an explicit pinned input (prefer the archived parent run's validated historical association, otherwise acquire and pin a hint before validating it). A mutable whole-cache scan is not authoritative for `qid-4` root selection.

Ruling: final manifests are immutable; transient acquisition failure is not published as a completed smaller manifest. Physical artifact absence/corruption raises a typed input failure and never causes pure classification on a reduced graph.

Task Q401/Q402: complete. Manifest/source/store regressions first failed for absent interfaces, then passed. Node budget test data originally included configured Q11424 accidentally; corrected the synthetic QID range instead of changing taxonomy/assertions. Empty PHP maps serialized as exact `[]` are accepted explicitly; arbitrary falsy scalars/null/nonempty lists are rejected. Actual historical catalog contains 372 empty-list sitelinks, so rejecting every list would break valid archived data.

Task Q403: complete. Provider regressions first failed for missing process_dependencies/reports, then passed. Article association is an explicit pinned hint and still requires the selected historical sitelink. Acquire-manifest checkpoint can recover publication interruption. Manifest publication and binding share an atomic transaction. Required physical artifacts are checked before process/classification reuse; logical inputs cannot be replaced inside a run. Exact-revision materialization restores missing physical blobs in content batches <=20, without reselecting revisions. Per-depth acquisition batches content for required frontier nodes; common selected refs/raw artifacts are shared through the existing revision/cache store. Network concurrency/maxlag/retry etiquette is unchanged.

Task Q404: complete. Legacy fixture tests now explicitly select qid-3 where their assertions describe qid-3 cached extension; no archived assertion was weakened or deleted. Default fixture corpus packages now embed qid-4 manifests, so full freeze/standalone verification exercises the new path while retaining the exact 20,000 gate. Classification taxonomy/precedence, extraction lead-3, ranking and SPEC/PLAN are unchanged.

Final review: independent read-only reviewer via requesting-code-review. Reproduced and corrected malformed selected-reference JSON/fields, invalid-vs-missing metadata labeling, misleading resumed-input audits, malformed consumed claim/sitelink shapes and null/scalar timestamps. RED/GREEN fixtures cover each correction. Reviewer found no further confirmed issue in traversal/siblings/cycles/budgets, atomic publication, manifest reuse or frozen replay. Reviewer independently ran 40 focused tests before the last timestamp guard; author verification after all corrections ran 51 manifest/source/store/provider tests and the full 229-test suite. No live source, production coverage or future pilot yield was reviewed.

## Báo cáo nghiệm thu — 2026-10-25

### 1. Lỗi gốc đã sửa

- F1: cache không còn mở rộng semantic closure. qid-4 dùng cùng BFS depth 12/budget 2.048 cho nguồn và cache; required siblings vẫn được xét.
- F2: classification và outer process fingerprints chứa manifest identity. Current-run binding là bất biến; input/policy khác bị báo Incompatible, cần run mới. Cross-run chỉ reuse dependency hash tương thích; quality/link dependencies nhận classification/extraction hash mới, pairs/selection được tính lại từ eligible records của invocation.
- F3: qid-4 không dùng global/stale WikidataSource cache index để chọn root/closure. Root hint được pin theo article revision/hash; inventory đã publish không đổi khi cache warm thêm.
- F4: chỉ đọc/validate artifacts thuộc manifest/required frontier. Invalid cache entry ngoài graph không làm hỏng article khác.
- F5: tách dependency_input_missing, dependency_policy_limit, history_unavailable với evidence, dependency_input_invalid/invalid_historical_cache, transient codes/deadline và hierarchy_node_budget_exhausted. Báo cáo lỗi materialization vẫn giữ manifest ID nhưng không trình bày classification cũ là kết quả hợp lệ hiện tại.

### 2. Kiến trúc và invariants

Acquisition deterministic -> manifest content-addressed/binding immutable -> materialization exact revisions/hashes -> pure classify_manifest -> manifest-bound stages -> eligibility/pairs/selection. Manifest chứa stable article identity, snapshot, rules/policy hash, root QID, selected QID revision/timestamp/content+entity hashes, edges, completeness và frontier. Lookup wall time, cache paths, mutable global cache không phải logical input identity. Blob vắng/hỏng không làm giảm graph được đem phân loại. Negative historical evidence được freeze cùng input; không đánh đồng một node chưa lookup ngoài bound với source unavailable.

### 3. Files thay đổi

Implementation mới: `backend/src/vimfc/pipeline/dependencies.py`, `source/dependencies.py`, `pipeline/manifest_report.py`.

Integration sửa: `pipeline/provider.py`, `pipeline/runner.py`, `pipeline/freeze.py`, `storage/pipeline_store.py`, `source/revisions.py`, `cli.py`. Store bổ sung ba bảng manifest/binding/hint theo kiểu additive, không sửa nội dung run cũ. Agent không mở actual DB qua writable store; migration chỉ xảy ra khi người dùng tự chạy CLI sau bàn giao.

Version: `configs/classification_rules.json` -> qid-4 và policy bounded-bfs-1; `configs/pipeline.json` -> pipeline-4. `pipeline/classify.py`/legacy `source/wikidata.py` không đổi.

Tests mới: `tests/unit/test_dependency_manifest.py`, `tests/integration/test_dependency_source.py`, `test_manifest_store.py`, `test_manifest_provider.py`. Fixtures/helper và regression cũ được chọn đúng version; `test_pair_generation_pipeline.py` bổ sung kiểm tra manifest/quality/Bridge/Comparison/selection bất biến.

Tài liệu và audit mới: file này, `docs/tasks.md`, `docs/pilot100-replay.md`, `scripts/verify-qid4-history.py`, `qid4-historical-audit.json`, `qid4-preservation.json`. Root `REVIEW.md` được giữ nguyên theo hash baseline.

### 4. Tests và bằng chứng offline

Lệnh thực tế từ repository:

```powershell
$env:VIMFC_OFFLINE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& .tools\python312\python.exe -B -m pytest tests/unit tests/integration --basetemp .tools/qid4-fix/final-suite -q --tb=short
```

**229 passed, 0 failed, 0 skipped; 134.41 giây; exit 0.** Log thực tế: `.tools/qid4-fix/final-suite.log`; exit code: `.tools/qid4-fix/final-suite.exit-code.txt`. Basetemp trong lệnh lưu vết là lần chạy đã hoàn tất; khi tự kiểm thử lại nên chọn basetemp mới.

Tests mặc định chặn socket thật. Phủ đủ cold/warm cùng manifest, missing artifact, depth 12/13, đảo processing order, external warm-up, current-run incompatibility, cross-run matching/different manifest, unrelated invalid cache, required siblings/subtypes/precedence, cycles/budget sát 2.048, revision/hash/cutoff, Bridge+Comparison+selection và archived legacy fixtures. Full suite kiểm tra freeze controlled 20.000, không tạo corpus nghiên cứu thật.

PowerShell: đã cài backend bằng `--no-index --no-build-isolation --no-deps`, chạy block prepare/create/status/report **trên fixture DB trong .tools**, không chạy pilot. Bốn block tài liệu parse PASS. Các regression wrapper xác minh stdout+stderr có trong log và native exit 7 được ghi đúng; launch failure không dùng lại exit 0, context 1.000 bị từ chối. Log smoke: `.tools/qid4-fix/operator-smoke.log`, exit 0.

### 5. Đối chiếu historical graphs — không phải pilot mới

| Phạm vi | Graph khép kín | Policy-limited | Missing manifest artifacts | Historical unavailable có evidence | Invalid cache | Outcomes |
|---|---:|---:|---:|---:|---:|---|
| 43 truncated của Pilot 2 | 0 | 43 | 0 | 0 | 0 | 43 unresolved |
| 37 ca từng được qid-3 cached extension giải quyết | 0 | 37 | 0 | 0 | 0 | 37 unresolved |
| 6 ca còn unresolved trong qid-3 | 0 | 6 | 0 | 0 | 0 | 6 unresolved |
| Cả 100 đồ thị lịch sử | 57 | 43 | 0 | 0 | 0 | 27 supported / 30 unclassified / 43 unresolved |

Tất cả so sánh declared/scoped artifacts với full warm catalog cho cùng manifest/outcome. Physical cold-cache restoration được kiểm tra bằng fixture source/MockTransport, không phải truy xuất API thật. Source catalog có 1.014 selected historical QIDs tại cùng snapshot. Q141438977 và Q141445732 vẫn có 0 refs và 0 recorded source failures; không thể kết luận chúng không có historical revision. qid-3 retrospective 47/47/6 vẫn là kết quả của policy cũ, không bị ghi đè và không được trình bày là qid-4 live yield.

Lệnh offline đã chạy:

```powershell
& .tools\python312\python.exe -B scripts\verify-qid4-history.py --database data\pipeline.sqlite --raw data\raw --source-report reports\pilots\9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e\100\metrics.json --output reports\verification\qid4-historical-audit.json
```

### 6. Bảo toàn và giới hạn

[Preservation audit](qid4-preservation.json): hai actual runs vẫn giữ fingerprints của 10 bảng/run, 20 report files không đổi; năm shared source tables không đổi. Kiểm tra lại toàn bộ 1.477 registered raw objects theo envelope/content SHA-256/SHA-1. Actual run count vẫn 2. Lead-3, ranking, SPEC/PLAN và REVIEW.md khớp SHA baseline. Taxonomy/precedence, max_depth=12, max_cached_nodes=2.048 giữ nguyên. Không commit/push; không Wikipedia/Wikidata API; không tạo actual qid-3/qid-4 run hay pilot.

Giới hạn coverage: strict depth 12 mất phần coverage qid-3 chỉ nhận được do ambient warm cache. 43 graph mở phải unresolved theo policy, không supported/unclassified. Muốn tăng coverage cần review/version hóa acquisition policy hoặc phương pháp taxonomy trong phạm vi nghiên cứu; không tăng bound hay hạ eligibility trong lượt này. Không có bằng chứng số pairs/yield production thay đổi bao nhiêu; không kết luận pair algorithm kém vì zero pairs lịch sử. Matching logical manifest bảo đảm replay; manifest khác là input khác, không phải cùng replay. Pinned missing artifact có thể phục hồi bằng exact revision nhưng không thay bằng revision khác.

### 7. Readiness và bàn giao

**READY cho người dùng tự chạy Pilot 100 kiểm chứng qid-4 trên run mới; chưa đạt mục tiêu coverage của corpus và chưa có live qid-4 pilot.** Snapshot/cohort lấy từ actual Pilot 2 `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e`, `2026-10-25T03:00:00Z`. Run ID mới chỉ được tạo khi người dùng chạy bước 1; agent không chọn/đoán UUID trước.

[Bốn block PowerShell chính xác, đã kiểm tra](../../docs/pilot100-replay.md): 1) offline install/prepare/create và run-context; 2) user chạy script bounded 100, Tee stdout/stderr và capture exit code; 3) status/export/audits; 4) retry/resume cùng context khi transient đến hạn. Không chạy lại create khi chỉ resume. Không sửa rules/manifests của run đang dùng. `configs/replays/latest-context.txt` trỏ tới report folder của run mới; mỗi invocation có log/exit-code mới và attempt folder riêng.

Artifacts cần gửi: toàn bộ `reports/pilots/<new_run_id>/100/` gồm context, cohort, metrics/performance/template/classification/pair/dependency-manifest/fingerprint audits, `manifests/`, `attempts/`, stdout/stderr `.log`, `.exit-code.txt`, summary và `pilot100-export.json`.

**STOP:** code + offline tests + report + operator handoff hoàn tất. Người dùng tự chạy Pilot 100; chưa được chạy Pilot 1.000/Production.
