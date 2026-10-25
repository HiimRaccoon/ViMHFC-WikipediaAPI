# Root Cause Review: Cache-dependent Classification trong `qid-3`

**Ngày điều tra:** 2026-10-25.  
**Commit được review:** `f4d6df24abe27c75bdba90b25b1807025f2781f5`.  
**Vai trò:** Senior Backend Engineer — Data Pipeline, Wikidata, Reproducibility.  
**Phạm vi:** Điều tra read-only, chưa sửa implementation. File này ghi lại findings theo yêu cầu tiếp theo của người dùng.

## 1. Verdict

**CONFIRMED BUG — về reproducibility và dependency fingerprint của `qid-3`.**

Đã tái hiện rằng cùng article, Fixed Snapshot, rules và tập source revisions cố định, trạng thái cache khác nhau có thể làm classification chuyển:

- `unresolved` → `completed`;
- `unresolved` → `unclassified/no_supported_rule`.

Processing order, cache warm-up và việc dùng lại instance/checkpoint cũng ảnh hưởng kết quả. Đã xác minh eligibility của một fixture thay đổi từ `false` sang `true` vì classification được hoàn tất sau warm-up.

**Giới hạn của verdict:** Chưa có bằng chứng rằng một closure đầy đủ, hợp lệ bị suy ra sai supported group/subtype chỉ vì cache. Cũng chưa tái hiện việc cùng một đồ thị revisions cố định, đầy đủ và hợp lệ chuyển trực tiếp giữa `completed` và `unclassified` chỉ do cache. Pure `classify()` vẫn deterministic khi nhận cùng tập `items` và rules.

Cached completion có thể là một lựa chọn thiết kế hợp lệ nếu dependency inventory là input được khai báo và đóng băng. Implementation hiện tại chưa đáp ứng điều đó: cache là đầu vào ngữ nghĩa ngầm, nhưng closure/inventory không nằm trong classification fingerprint. Vì vậy đây là bug so với hợp đồng reproducibility đã chốt, không chỉ là khác biệt performance.

## 2. Ràng buộc đã tuân thủ

Trong lượt RCA:

- Không sửa code, config, docs, database hay raw cache của repository.
- Không commit/push.
- Không gọi Wikipedia/Wikidata API hoặc mạng thực tế.
- Không gọi CLI/Builder để chạy Pilot 100/1.000 hoặc Production.
- Không thay đổi Fixed Snapshot, eligibility, Lead-only Graph hay mục tiêu 20.000 articles.
- Đối chứng sử dụng SQLite/cache trong bộ nhớ hoặc thư mục tạm bên ngoài repository.
- Python chạy với `-B`/`PYTHONDONTWRITEBYTECODE=1`; pytest tắt cache provider và dùng basetemp tách biệt.
- Đọc database thật bằng `mode=ro&immutable=1`, sau khi xác nhận WAL rỗng. Kích thước và modification time của database/WAL/SHM không đổi trước/sau đối chứng dữ liệu thật.
- Git HEAD giữ nguyên và working tree vẫn sạch khi hoàn tất RCA.

**Thay đổi duy nhất của yêu cầu hiện tại:** tạo `REVIEW.md` này để lưu báo cáo. Không áp dụng các phương án sửa bên dưới.

## 3. Bối cảnh và các mốc cần phân biệt

| Mốc | Versions | Kết quả liên quan | Loại bằng chứng |
|---|---|---|---|
| Pilot 100 ban đầu | `qid-1/lead-1` | 66 pending, gồm 46 truncated và 20 maxlag | Run thực tế đã lưu |
| Pilot 100 lần 2 — `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e` | `qid-2/lead-2` | 43 truncated; 26 có configured root; 92 extraction hoàn chỉnh; 16 eligible; 531 requests; errors/retries 0 | Người dùng chạy thực tế |
| Offline repair sau lần 2 | `qid-3/lead-3` | Retrospective lưu trong repo: 47 supported / 47 unclassified / 6 unresolved | Phân tích historical cache, không phải pilot mới |
| Lượt RCA này | Code `qid-3` hiện tại | Đối chứng cache/state/order trên fixtures và 43 đồ thị lịch sử | Thí nghiệm offline, không phải Pilot 100 |

Database thật hiện có các run dùng **`qid-1` và `qid-2`**. **Chưa có Pilot 100 mới bằng `qid-3`.** Không được dùng kết quả retrospective hoặc đối chứng dưới đây làm số liệu live của một run `qid-3`.

Fixed Snapshot đang xét: **`2026-10-25T03:00:00Z`**.

Nguồn tham khảo:

- [Assessment của Pilot 2](reports/pilots/9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e/assessment.md).
- [Curated review evidence](reports/verification/pilot100-review-evidence.json).
- [Offline repair acceptance](reports/verification/pilot2-remediation.md).
- [Per-article retrospective audit](reports/verification/pilot2-remediation-audit.json).
- Database/raw cache local: `data/pipeline.sqlite`, `data/raw` — không nằm trên Git.

## 4. Trace luồng xử lý

```text
select_revision(site, identity, snapshot)
    -> snapshot_revision_refs
    -> fetch_revisions / source_revisions / raw cache
    -> WikidataSource index và in-memory items
    -> closure(qid): network depth + cached completion
    -> classify(title, qid, items, rules, snapshot)
    -> classification stage / quality / process checkpoint
    -> eligible articles -> pairs -> selection
```

### 4.1. Historical revision selection và raw cache

Trong [revisions.py](backend/src/vimfc/source/revisions.py):

- Dòng **30–36**: ưu tiên reference đã lưu theo `(site, identity, snapshot)`.
- Dòng **38–48**: nếu chưa có, kiểm tra failure cache rồi lookup revision; không có revision phù hợp dẫn tới `history_unavailable`.
- Dòng **52–56**: lưu selected reference cùng snapshot.
- Dòng **59–98**: lấy raw content theo selected revision, ưu tiên registered cache hoặc orphan có thể xác minh; chỉ fetch phần thiếu.

Cache chứa raw blob có timestamp trước snapshot chưa tự động chứng minh đó là latest selected revision cho snapshot. Việc đòi selected reference tương thích là cần thiết để giữ Fixed Snapshot.

### 4.2. Index và cached completion

Trong [wikidata.py](backend/src/vimfc/source/wikidata.py):

- Dòng **44–59**: `_read_cached()` kiểm tra source identity, snapshot, timestamp, content model, content hash và một số JSON shape.
- Dòng **61–76**: `_index_snapshot()` đọc các selected references cùng snapshot có registered raw object; tạo `_cached_refs` và title/QID index. Index chỉ được tạo khi `_titles is None`.
- Dòng **78–87**: `_load_cached()` materialize những QID nằm trong index, không gọi source.
- Dòng **114–123**: `closure()` thử nạp cache cho frontier, nhưng chỉ gọi `_load()` cho node có depth `<= max_depth`.
- Dòng **125–134**: node không nạp được bị đưa vào `skipped`; nếu cached completion bật, các node đã nạp được tiếp tục sinh frontier ngay cả sau depth 12.
- Dòng **141–143**: ghi `visited_count` và `uncached_frontier`.

Vì vậy, ở depth 13 trở đi:

```text
có item trong memory/index hợp lệ -> tiếp tục kiểm tra nhánh
chưa có item trong memory/index   -> dừng nhánh với missing dependency
```

Đây là nơi cache chuyển từ tối ưu truy xuất sang điều kiện quyết định lượng bằng chứng được phép xét.

### 4.3. Pure classification

Trong [classify.py](backend/src/vimfc/pipeline/classify.py):

- Dòng **18–24**: configured root kết thúc ancestor branch của chính nó; root article dùng P31/P106, các node kế tiếp dùng P279.
- Dòng **39–44**: kiểm tra node budget và missing/invalid dependency.
- Dòng **51–55**: khi cached completion bật, classifier tiếp tục kiểm tra `items` vượt `max_depth`.
- Dòng **56–57**: bất kỳ required dependency còn thiếu/invalid đều giữ outcome unresolved theo rules hiện tại.
- Dòng **63–68**: chỉ sau khi closure khép kín mới quyết định `no_supported_rule`, group precedence và subtype.

Do đó, thay đổi graph mà `closure()` materialize sẽ thay đổi input thực tế của classifier. Không có bằng chứng cho thấy pure classifier tự ngẫu nhiên đổi kết quả khi input `items` giống hệt nhau.

### 4.4. Fingerprint và stage reuse

Tại [provider.py:64](backend/src/vimfc/pipeline/provider.py):

```python
classification_dep = {
    'snapshot': self.run['snapshot'],
    'article_ref': asdict(raw.ref),
    'rules': self.config['classification'],
}
```

Fingerprint thiếu:

- historical Wikidata item closure;
- dependency QID/revision IDs/timestamps/hashes;
- frontier và lý do thiếu;
- dependency inventory/policy manifest được dùng để cho phép cached completion.

Các dependency và `closure_summary` có được lưu trong **result**, nhưng chưa trở thành **dependency identity** của stage.

Trong [runner.py](backend/src/vimfc/pipeline/runner.py):

- Dòng **25–28**: nếu current run đã có result cùng dependency hash, trả lại ngay.
- Dòng **29–36**: guard tránh reuse unresolved chỉ áp dụng khi lấy result từ `reuse_run` khác.
- Dòng **71–76**: kết quả `unresolved` không được nhận diện là transient pending, nên stage được lưu `completed`.
- Dòng **183–186**: outer process stage cũng dùng key dựa trên snapshot/config/candidate, rồi có thể reuse cả process result.

Trong [pipeline_store.py:92–103](backend/src/vimfc/storage/pipeline_store.py), `result_hash` kiểm tra JSON đã lưu không bị đổi. Nó không kiểm tra dependency inventory hiện tại có còn giống lúc tính result hay không.

**Nuance:** `stage.status=completed` nghĩa là hoàn tất xử lý, không có nghĩa classification supported. Đây không tự nó là lỗi. Rủi ro là checkpoint không gắn với manifest đủ để biết bộ bằng chứng đầu vào đã thay đổi.

## 5. Findings theo mức độ

### F1 — P1: Cache-dependent semantic closure dưới cùng fingerprint

**Đã xác minh.** Dependency sau network depth được xem xét hay không phụ thuộc vào cache sẵn có. Fingerprint chỉ giữ snapshot/article reference/rules, nên chưa phân biệt hai lần classification dùng closure khác nhau.

Ảnh hưởng trực tiếp: outcome, subtype availability và eligibility có thể khác nhau; không chỉ số requests hay elapsed time.

### F2 — P1: Current-run checkpoint giữ unresolved sau khi dependency đã đủ

**Đã xác minh qua provider thực tế.** Sau warm-up, fresh direct classification trả completed, nhưng provider trong cùng run sau restart vẫn lấy unresolved đã checkpoint. Tạo run mới với cùng rules thì được tính lại và completed.

Giữ checkpoint cũ có thể phù hợp nếu logical input của run đã được đóng băng. Hiện chưa có manifest thể hiện ranh giới đó, trong khi closure vẫn có thể hưởng cache mới từ các articles khác. Đây là hệ quả của F1 và thiếu cơ chế nhận diện input thay đổi, không phải khuyến nghị ghi đè kết quả run cũ.

### F3 — P2: Instance cache index bị stale sau warm-up bên ngoài

**Đã xác minh.** `_cached_refs` không refresh sau khi index được xây dựng. Nếu dependency được ghi vào shared cache qua một source/instance khác, instance cũ có thể tiếp tục unresolved, trong khi instance mới completed.

Warm-up qua chính `WikidataSource._load()` lại thêm item vào `self.items`, nên instance hiện tại có thể nhìn thấy dependency theo một đường khác. Tuổi và đường warm-up của instance trở thành hidden state bổ sung.

### F4 — P2: Invalid cache entry không liên quan có thể ảnh hưởng article khác

**Đã xác minh bằng fixture.** Sau khi graph A đã có đủ dependency và completed, thêm một cached QID không liên quan có timestamp tương lai làm index scan thất bại. Provider của run mới trả unresolved với `invalid_historical_cache`, dù graph A không tham chiếu QID đó.

Từ chối metadata invalid là đúng. Vấn đề là phạm vi validation/index toàn snapshot tạo ảnh hưởng lan rộng đến articles không phụ thuộc entry bị lỗi.

### F5 — Hạn chế diagnostics: Cache miss chưa được phân biệt đầy đủ với unavailable history

`missing_or_invalid_historical_dependency` có thể xuất hiện khi dependency đơn giản chưa cached và chưa được lookup vì ngoài network depth. Đây không phải bằng chứng historical revision không tồn tại hoặc không thể lấy được.

`closure_summary.uncached_frontier` hỗ trợ điều tra, nhưng classifier reason và terminal processing state chưa diễn đạt đủ khác biệt giữa thiếu input do policy, unavailable history, invalid cache và hết budget.

## 6. Đối chứng bằng fixtures

### 6.1. Source revisions cố định, cache lạnh và cache có sẵn

Graph supported fixture:

```text
A --P31--> Q5 (Human root)
A --P106--> Q1001 --P279--> ... --P279--> Q1012 (depth 12)
                                         |
                                         +--P279--> Q901 (Scientist, depth 13)

B --P31--> Q901
```

Rules không đổi: `qid-3`, `max_depth=12`, `max_cached_nodes=2048`. Fixture source có sẵn cùng revision/content của Q901 trong cả hai điều kiện. Không gọi mạng.

| Điều kiện | Nodes được materialize | Outcome thực tế | Chi tiết |
|---|---:|---|---|
| Cache lạnh | 14 | `unresolved` | Frontier Q901; không lookup Q901 ở depth 13 |
| Cache đã có đủ dependency | 15 | `completed` | Person, Scientist subtype |

Cold execution có 27 lời gọi **fixture client**, warm execution có 0. Đây không phải số API requests thực tế. Q901 có thể được fixture source cung cấp; thiếu cache không đồng nghĩa source không khả dụng.

Đối chứng khác dùng graph không có supported root:

| Điều kiện | Nodes | Outcome thực tế |
|---|---:|---|
| Dependency cuối ở depth 13 chưa cached | 13 | `unresolved` |
| Cùng revisions, dependency cuối đã cached và graph khép kín | 14 | `unclassified/no_supported_rule` |

Hai bảng chứng minh hai kiểu transition trên các graph đối chứng khác nhau. Chúng không chứng minh một article trên cùng complete valid graph chuyển trực tiếp giữa supported và unsupported.

### 6.2. Thứ tự A/B và warm-up

Trong cùng instance:

1. Classify A trước: unresolved.
2. Xử lý closure của B: Q901 nằm ở depth 1 nên được nạp.
3. Tính trực tiếp A lại: completed, Scientist.

Với warm-up bên ngoài instance:

1. Tính A: unresolved.
2. Ghi dependency Q901 hợp lệ vào shared fixture cache bằng một đường khác.
3. Instance cũ vẫn unresolved do index cũ.
4. Instance mới completed trên cache hiện tại.

### 6.3. Provider, fingerprint, checkpoint và eligibility

Đối chứng dùng `HistoricalProvider`, `StageExecutor`, `RevisionSource`, `WikidataSource`, extraction/quality thực tế, cùng một Wikipedia `RawRevision`. Lead lấy từ fixture có sẵn; dependency cache/SQLite nằm trong môi trường tách biệt.

| Bước | Classification | Eligible |
|---|---|---|
| Trước khi Q901 được warm | `unresolved` | `false`, chỉ có quality reason `unsupported_classification` |
| Sau warm-up, tính trực tiếp bằng fresh closure | `completed` | — |
| Provider mới nhưng resume cùng run | Vẫn `unresolved` | `false` |
| Tạo run fixture mới với cùng config và cache đã warm | `completed` | `true` |

Đối chứng khác xác nhận current-run classification stage vẫn có `status=completed`, dependency hash và result hash không đổi sau warm-up/reuse.

**Expected theo hợp đồng đầu vào:** Nếu logical dependency input giống nhau thì kết quả phải giống nhau. Nếu input đã đổi, manifest/fingerprint phải nhận diện được, hoặc run phải giữ inventory đã đóng băng một cách rõ ràng. Không nên để cùng declared input/fingerprint đại diện cho hai closure khác nhau.

### 6.4. Invalid cache không thuộc graph của article

Fixture thêm `Q999999`, không nằm trong dependency graph của A, với metadata không hợp lệ đối với snapshot. Kết quả quan sát:

```text
Trước entry invalid: completed
Sau entry invalid, provider/run mới: unresolved
error_code: invalid_historical_cache
offending_qid: Q999999
```

Điều này không có nghĩa nên dùng invalid metadata. Nó chứng minh cache index toàn snapshot có thể ảnh hưởng article ngoài phạm vi dependency của entry lỗi.

## 7. Đối chứng trực tiếp 43 đồ thị lịch sử của Pilot 2

### 7.1. Phương pháp

- Đọc 43 article/QID và raw revisions từ database/checkpoint/audit hiện có.
- Xây catalog 1.014 selected historical QIDs tại cùng snapshot; đọc và xác minh raw object/content hash.
- Dùng catalog này làm fixture source cố định cho code production `RevisionSource -> closure -> classify`.
- Cold condition: mỗi article bắt đầu với cache riêng trống.
- Warm condition: historical catalog hiện có đã được đưa vào cache fixture trước khi classify.
- Không fetch thêm dữ liệu thật; không gọi Builder/CLI pilot; không ghi database/raw cache thật.

### 7.2. Kết quả cold/warm riêng từng article

| Điều kiện | Completed | Unclassified | Unresolved |
|---|---:|---:|---:|
| Cache lạnh riêng cho từng article | 0 | 0 | 43 |
| Cache có sẵn historical catalog | 20 | 17 | 6 |

Transitions:

- 20: unresolved → completed.
- 17: unresolved → unclassified.
- 6: unresolved → unresolved.

**Cả 37/43 ca được giải quyết hồi cứu đều có outcome khác trong đối chứng cold-cache này.** Warm condition không cần lời gọi fixture source.

Article IDs của 37 ca thay đổi:

```text
19792958, 4130, 507484, 19742847, 1250504, 3830,
20018307, 19621998, 19970897, 214384, 128119, 19972470,
14705604, 14865641, 10963, 1580376, 281610, 1316900,
3259503, 3691131, 19878751, 47518, 29220, 14703159,
35465, 19945606, 3711942, 329413, 357533, 1316901,
84074, 3264708, 19961315, 2435064, 516152, 60752, 3147926
```

Đây là đối chứng trên 43 graph bằng các revisions đã lưu, không phải kết quả live của Pilot 100 lạnh. Không được ngoại suy trực tiếp thành số eligible hoặc số pairs của một Pilot 100 mới.

### 7.3. Cache lạnh dùng chung và processing order

Giữ cùng tập 43 article, revisions và rules, nhưng dùng cache chung và xử lý xuôi/ngược:

| Thứ tự | Completed | Unclassified | Unresolved |
|---|---:|---:|---:|
| Xuôi | 19 | 17 | 7 |
| Ngược | 18 | 17 | 8 |

Ba articles đổi outcome:

- `19742847` — L'Abidjanaise.
- `60752` — Danh sách thành phố theo dân số.
- `3147926` — Ngày Quốc tế vì Dân chủ.

Đảo thứ tự là đối chứng về coupling giữa articles, không phải hai replay có cùng discovery order. Không dùng thí nghiệm này riêng lẻ để kết luận vi phạm same-input replay. Đối chứng cold/warm riêng từng article ở mục 7.2 mới trực tiếp thể hiện hidden cache input khi revisions/rules được giữ nguyên.

### 7.4. Sáu ca còn unresolved

| Page ID | Article |
|---|---|
| 20020318 | 1ucid |
| 19482761 | Khánh (nghệ sĩ) |
| 19583257 | 12 Gauge (rapper) |
| 19966548 | 2pillz |
| 3700502 | K'Choi |
| 19575174 | 2Mex |

Cả sáu còn cần `Q141438977` và `Q141445732`.

Đối chiếu database thật tại snapshot đang xét:

| QID | Snapshot reference count | Recorded source failure count |
|---|---:|---:|
| Q141438977 | 0 | 0 |
| Q141445732 | 0 | 0 |

Chỉ đủ bằng chứng kết luận **chưa có selected dependency trong cache/index**. Chưa đủ bằng chứng kết luận historical revision của chúng thực sự không khả dụng trên Wikidata.

## 8. Phân biệt cache/source states

| Trạng thái | Hành vi/code hiện tại | Kết luận có thể đưa ra |
|---|---|---|
| Source lookup không trả revision phù hợp | `history_unavailable` tại `revisions.py:45–48`, lưu failure cache | Không có revision phù hợp trong kết quả lookup tại thời điểm đó; RCA này không gọi API để kiểm chứng sự khả dụng ngoài thực tế |
| Dependency chưa cached và ngoài network depth | Không lookup; đưa vào `uncached_frontier`; classifier trả `missing_or_invalid_historical_dependency` | Thiếu input do acquisition policy/cache state, không chứng minh source unavailable |
| Dependency cached từ run khác, cùng selected snapshot, raw hợp lệ | Được duyệt sâu hơn network depth | Revision được dùng phải có provenance hợp lệ; cache đã ảnh hưởng coverage ngữ nghĩa |
| Có raw blob nhưng thiếu selected reference/registered index phù hợp | Không mặc nhiên tham gia cached completion | Blob tồn tại chưa đủ chứng minh latest selected revision cho snapshot |
| Raw/hash/metadata/cutoff invalid | `invalid_historical_cache` | Không được dùng làm bằng chứng; hiện index toàn snapshot có thể ảnh hưởng article không liên quan |
| Hết `max_cached_nodes` | `hierarchy_node_budget_exhausted` | Resource/policy-limited unresolved, không phải `no_supported_rule` |

Negative source failures còn có cơ chế cache riêng trong `revisions.py:18–28`. Không được diễn giải một missing frontier chưa từng lookup như một recorded permanent source failure.

## 9. Stopping conditions, sibling branches, subtype, precedence và budget

Những phần hoạt động đúng trong các kiểm tra đã thực hiện:

- Configured root chỉ kết thúc ancestor branch của chính nó.
- Finding một root không tự động đóng các required sibling branches.
- Subtype và primary-group precedence chờ xử lý graph cần thiết.
- Cycle detection dùng visited set; không tự tạo root match.
- Với cùng tập `items` hợp lệ và rules, traversal/tie ordering là deterministic.
- Khi hết budget, classifier không tự cho bài thành supported.

Rủi ro cache-dependence xuất hiện **trước và quanh các điều kiện này**, vì chúng nhận các tập `items` khác nhau tùy cache.

`max_cached_nodes=2048` là giới hạn an toàn. Các graph lịch sử đang xét nhỏ hơn giới hạn đó; budget không phải nguyên nhân của kết quả cold/warm đã chứng minh. Tuy nhiên cần bổ sung kiểm thử biên, vì graph được materialize khác nhau theo cache có thể làm đường đi tới budget/reason khác nhau. Không có bằng chứng hiện tại rằng budget tạo false completed.

Đánh giá này xác minh hành vi theo taxonomy/rules hiện tại, không xác nhận toàn bộ ontology Wikidata hoặc mọi mapping đều đúng về ngữ nghĩa nghiên cứu.

## 10. Tác động

### 10.1. Reproducibility

Snapshot/rules/article reference hiện chưa mô tả đủ logical input. Một run có thể hưởng dependency do run trước hoặc article trước warm lên. Current-run checkpoint có thể tiếp tục giữ mức bằng chứng cũ mà fingerprint không phản ánh.

Điều này ảnh hưởng việc rebuild/replay từ nguồn và cache. Không có bằng chứng rằng một frozen artifact đã chứa đủ embedded metadata mất khả năng replay nội bộ.

### 10.2. Fixed Snapshot và provenance

Các đối chứng dùng cùng selected historical revisions và không phát hiện future-revision substitution. Temporal validity không tự bảo đảm cache-independent reproducibility.

Result có lưu visited dependency items, raw hashes/revisions và closure summary. Đây là điểm tốt cho kiểm chứng kết quả cụ thể, nhưng chưa thay thế manifest đầu vào/fingerprint của stage.

### 10.3. Eligibility

Đã xác minh thay đổi từ `eligible=false` sang `eligible=true` trong fixture sau warm-up/new run, dù lead/rules/snapshot giữ nguyên. `quality.py:11–12` loại classification chưa completed.

Đây chủ yếu là thay đổi coverage/false-negative theo mức bằng chứng đang có, không phải bằng chứng code tự cho bài sai type vào corpus.

### 10.4. Pair generation và selection

`pairs.py:17–25` chỉ xét eligible endpoints. Cache-dependent eligibility và subtype availability có thể làm đổi Bridge endpoints, Comparison blocks và đầu vào selection.

Chưa đo được mức thay đổi số pairs hoặc selected IDs trên corpus production. Không được kết luận 0 pairs của Pilot 2 là lỗi pair algorithm từ finding này.

### 10.5. Mục tiêu 20.000 articles

Cùng candidate cohort/rules có thể cho nguồn eligible khác nhau tùy cache baseline, nên yield estimates và chi phí mở rộng có thể bị lệch. Chưa đủ dữ liệu để định lượng ảnh hưởng tới việc đạt 20.000 articles. Không được dùng finding này để hạ eligibility hoặc đổi target.

## 11. Đối chiếu SPEC/PLAN

| Yêu cầu | Đánh giá |
|---|---|
| [FR-007](docs/specs.md), dòng 165: chọn historical revisions không sau snapshot | Các cache guards đang cố bảo đảm điều này; không có temporal violation được tái hiện trong đối chứng hợp lệ |
| [FR-008](docs/specs.md), dòng 166: giữ raw/provenance và cơ sở classification | Result có dependency metadata; cần bổ sung input identity/manifest để stage reuse có thể kiểm chứng |
| [FR-009](docs/specs.md), dòng 167: thiếu metadata phải unresolved | Conservative unresolved là đúng; phải phân biệt chưa acquire với source thực sự unavailable |
| [FR-019](docs/specs.md), dòng 192: same-input replay phải tái lập selection | Hidden cache input và cache-dependent eligibility là rủi ro trực tiếp |
| [FR-025/026](docs/specs.md), dòng 201–202: compatible resume, reprocess dependencies bị ảnh hưởng, giữ prior runs | Chưa nhận diện được closure thay đổi dưới cùng classification key; giải pháp không nên ghi đè lịch sử run cũ |
| [PLAN](docs/plans.md), dòng 196: snapshot traversal, cycles, shortest supported paths, depth 12 | Các guard tồn tại; cached extension cần một policy/input contract đầy đủ |
| [PLAN](docs/plans.md), dòng 267: classification fingerprint dựa trên historical item closure/rules | **Implementation chưa đáp ứng đầy đủ**: fingerprint hiện chưa chứa closure |
| [PLAN](docs/plans.md), dòng 267: operational timings có thể khác, selected content/scores/order không được khác | Cache phải giảm chi phí hoặc trở thành input được khai báo; không được ngầm thay đổi graph được phép xét |

## 12. Test coverage hiện có và khoảng trống

Trong lượt RCA, chạy riêng các test files liên quan, hoàn toàn offline, với pytest cache provider tắt và basetemp ngoài repository:

- `tests/unit/test_cached_hierarchy_completion.py`
- `tests/integration/test_cached_hierarchy_source.py`
- `tests/integration/test_classification_terminal_states.py`
- `tests/integration/test_archived_pilot2_hierarchy.py`

**Kết quả: 23 passed, exit code 0.**

Những tests này xác minh từng hành vi riêng: warm cache có thể hoàn tất sâu, uncached frontier ngoài bound không bị fetch, invalid cache bị từ chối, legacy depth behavior và cross-run unresolved được tính lại.

Chúng chưa đòi invariance giữa cold/warm conditions. `test_classification_terminal_states.py:24` còn xác nhận current run trả lại unresolved cũ, nên suite xanh không phủ nhận finding checkpoint.

Các đối chứng bổ sung của RCA được thực hiện trong bộ nhớ/thư mục tạm; không được thêm vào tracked tests vì người dùng chỉ yêu cầu điều tra, chưa cho phép sửa.

## 13. Đã chứng minh và chưa thể chứng minh

### Đã chứng minh

1. Cache availability ảnh hưởng semantic closure và outcome sau network depth.
2. Cả 37 ca được giải quyết hồi cứu đổi outcome trong đối chứng cold-cache riêng từng article.
3. Processing order/cache warm-up ảnh hưởng ba article trong đối chứng shared-cache trên tập 43 bài.
4. Instance cũ và mới có thể khác outcome sau external warm-up.
5. Provider/current-run stage reuse giữ unresolved dù direct classification sau warm-up đã completed.
6. New run có thể cho eligibility khác dưới cùng article/snapshot/rules.
7. Invalid cache entry không liên quan có thể làm article khác unresolved qua index toàn snapshot.
8. Các điều kiện trên không cần real network request để tái hiện.

### Chưa chứng minh

1. Kết quả live của một Pilot 100 `qid-3` khởi đầu với cold cache hoàn toàn.
2. Hai QID thiếu thực sự không có historical revision khả dụng.
3. Cùng complete valid graph đổi trực tiếp giữa `completed` và `unclassified` chỉ do cache.
4. Supported group/subtype của một closure đầy đủ bị sai do cache hợp lệ.
5. Số eligible/pairs/selected IDs sẽ thay đổi bao nhiêu ở 80.000 candidates hoặc corpus 20.000.
6. Toàn bộ taxonomy/mapping có đúng ngữ nghĩa nghiên cứu hay không.

## 14. Các phương án sửa — chưa thực hiện

### A. Áp giới hạn ngữ nghĩa thống nhất cho cached và uncached nodes

Áp cùng semantic depth/node budget bất kể dữ liệu đến từ cache hay source. Cache chỉ giảm việc lấy dữ liệu. Frontier ngoài semantic bound có cùng trạng thái trong cả hai điều kiện; không được gọi là `no_supported_rule` nếu graph còn mở.

**Ưu điểm:** ít thay đổi, không cần tăng API requests, dễ kiểm chứng tính tái lập theo policy bounded.

**Trade-off:** có thể mất coverage mà cached extension đang cung cấp; nhiều ca quay về unresolved. Nếu cần coverage cao hơn phải review taxonomy/acquisition policy hoặc mở rộng candidate pool, không hạ eligibility.

### B. Acquisition deterministic → dependency manifest đóng băng → pure classification

Chọn dependency bằng một policy deterministic được version hóa. Manifest ghi QID, revision ID, timestamp, raw hash, budget/policy và frontier reason. Classification chạy trên manifest đã materialize; dependency fingerprint bao gồm manifest identity. Physical cache chỉ phục vụ lấy đúng artifacts trong manifest.

**Ưu điểm:** mạnh nhất cho rebuild/resume, source provenance và kiểm tra input compatibility; phù hợp PLAN về closure fingerprint. Warm cache giảm requests mà không tự mở rộng semantic graph.

**Trade-off:** cần thêm bước planning/materialization và lifecycle của manifest. Cold acquisition có thể tốn thêm requests nếu policy được duyệt yêu cầu closure sâu hơn. Giới hạn depth/API hiện tại không được tự thay đổi: nếu giữ depth 12 thì phải áp dụng thống nhất; nếu chọn policy khác phải review/version hóa trước, vẫn dùng historical cutoff và API etiquette.

Đây không phải đề xuất đơn thuần tăng traversal limit. Phần cốt lõi là khai báo và đóng băng input graph, không thưởng thêm semantic coverage chỉ vì cache hit.

### C. Giữ cached completion nhưng đóng băng logical dependency inventory của run

Inventory hợp lệ trở thành input được khai báo, có hash, thay vì ambient mutable cache. Cache warm-up trong lúc xử lý không tự thêm node vào semantic universe của run. Inventory khác phải được coi là input khác. Replay với physical cache lạnh phải materialize đúng pinned artifacts hoặc báo thiếu input; không âm thầm tạo một kết quả khác dưới cùng identity.

**Ưu điểm:** có thể giữ cách tận dụng historical cache hiện tại với ít thay đổi acquisition hơn B.

**Trade-off:** reproducibility phụ thuộc việc cung cấp cùng inventory/artifacts; không bảo đảm cùng coverage nếu thay logical inventory. Cần phân biệt rõ physical cache và logical input dataset, cũng như tránh ảnh hưởng của entries ngoài dependency scope.

**Chỉ thêm hash của toàn bộ mutable cache là chưa đủ:** hash có thể đổi sau mỗi warm-up, chứa entries không liên quan và chưa mô tả graph nào thực sự cần thiết cho article.

## 15. Khuyến nghị

**Ưu tiên B** cho hướng lâu dài vì hợp với classification closure fingerprint, provenance và reproducible rebuild/resume đã chốt trong PLAN.

- Chốt/version hóa dependency acquisition policy trước.
- Nếu bắt buộc giữ semantic depth 12, áp dụng nhất quán như A; không mở sâu hơn chỉ vì cache có sẵn.
- Nếu cần giữ coverage cached hiện tại trong giai đoạn chuyển tiếp, dùng C với inventory đóng băng, không chỉ một cache hash mutable.
- Bảo toàn kết quả run cũ; khi logical input/rules thay đổi, dùng identity mới và invalidate/recompute đúng các stage phụ thuộc.
- Không tự động chuyển unresolved thành supported để tăng yield.

Quyết định sửa và trade-off API/coverage cần người dùng review trước. Chưa triển khai phương án nào.

## 16. Regression tests cần bổ sung

1. Cùng declared revisions/manifest/rules: cold và warm cache cho cùng outcome/group/subtypes/eligibility khi input đã materialize.
2. Node ở depth 12/13: cached và uncached tuân cùng semantic policy; reason cho frontier bị policy chặn phải rõ.
3. Đảo thứ tự A/B và shuffled article order không làm đổi classification theo article sau khi đáp ứng cùng dependency policy.
4. Cache warm qua instance khác: policy về inventory refresh/freeze được kiểm chứng, không phụ thuộc tuổi instance ngoài input identity.
5. Current-run resume sau thay đổi dependency: nhận diện manifest incompatibility hoặc giữ input đã đóng băng rõ ràng; không silently reuse theo key thiếu dependency.
6. Cross-run reuse: chỉ reuse khi closure/manifest identity tương thích; recompute đúng classification/quality/process/pairs/selection khi input thay đổi.
7. Phân biệt `not_fetched_due_to_policy`, `history_unavailable`, transient failure, invalid cache và budget exhaustion.
8. Invalid cache ngoài dependency scope không đổi classification của article không liên quan, trừ khi entry đó thuộc input chung đã khai báo.
9. Known root không che required missing sibling; subtype/precedence chỉ hoàn tất khi đủ bằng chứng.
10. Cycles, duplicate paths và budget boundaries, gồm sát 2.048 nodes; không tạo false completed hoặc false `no_supported_rule`.
11. Replay từ archived fixtures của các ca supported, unsupported và missing frontier trong Pilot 2.
12. End-to-end chứng minh eligibility/pair/selection invariance trên cùng logical input dataset, không chỉ kiểm tra pure classifier.

## 17. Tiêu chí phải PASS trước Pilot 100 tiếp theo

- [ ] Policy xác định logical dependency graph được chốt và version hóa.
- [ ] Manifest/fingerprint thể hiện đủ dependency input, completeness/frontier và policy.
- [ ] Cold/warm/restart/reordered fixture scenarios đáp ứng hợp đồng deterministic đã chọn.
- [ ] Current-run và cross-run reuse không giấu dependency changes dưới cùng identity.
- [ ] Missing cache được phân biệt với historical-unavailable; các trường hợp unresolved có reason/action chính xác.
- [ ] Sibling/subtype/precedence/cycle/budget guards vẫn giữ tiêu chuẩn correctness.
- [ ] Các regression tests cần thiết và unit/integration offline suite PASS.
- [ ] Giữ nguyên Fixed Snapshot, eligibility, Lead-only Graph và target 20.000.
- [ ] User review phương án sửa và tự quyết định chạy lại Pilot 100 trên run/input identity phù hợp.

Không có tiêu chí nào ở đây tự cấp quyền chạy Pilot 1.000 hoặc Production.

## 18. Điểm dừng

RCA đã hoàn tất và được ghi lại trong file này. Chưa sửa implementation/config, chưa thay đổi SPEC/PLAN, chưa commit/push và chưa chạy pilot mới. Chờ người dùng review findings và quyết định phương án sửa.
