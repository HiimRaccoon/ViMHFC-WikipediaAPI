# Stratified + Hyperlink-aware Discovery — design và implementation scope

**Cập nhật implementation:** người dùng đã yêu cầu triển khai controlled frozen-inventory experiment `stratified-lead-1` / pipeline-7, source lifecycle và manual79 pack. [Protocol/boundaries hiện tại](../discovery-experiments.md). Phần dưới giữ rationale của proposal ban đầu; online adaptive multi-round expansion và live discovery yield vẫn chưa triển khai/chạy. Không đổi default configs hoặc historical run. Baseline: run `666068c8-3e97-4a7b-9a9b-f1e451c32a23`, snapshot `2026-10-25T03:00:00Z`, `qid-5 / lead-4 / quality-2.1 / rank-1`.

Mục tiêu là tăng **quota-usable eligible yield**, tỷ lệ articles có partner và độ đa dạng evidence chains. Giữ corpus20.000 articles,2.000 human-authored claims, label/NEI/hop/human-review requirements, eligibility thresholds, Fixed Snapshot và Lead-only Graph. Không dùng LLM/Cross-Encoder trong pipeline.

## 1. Bằng chứng thúc đẩy thiết kế

[Audit baseline](../../reports/verification/pilot1000-next-stage-audit-20261027.json) ghi:

- 131/1.000 candidates có primary discovery origin là8 category streams;869 đến từ namespace/prefix streams. Đây là origin sau dedup, không phải số API hints hoặc lịch sử mọi origin trùng lặp.
- Category seeds tạo37 eligible/131; namespace/prefix tạo113/869. Chênh lệch quan sát không chứng minh ưu thế nhân quả hoặc đại diện cho toàn Wikipedia. People25/43, Science0/2, Locations0/14; không ước lượng production yield từ strata rất nhỏ.
- Eligible composition: Person47, Organization17, Creative Work53, Award/Competition27, Landmark/Facility4, Event2, Location0, Science/Technology0. Seed category là hint, không đồng nhất với actual primary group.
- Lead-only graph:150 vertices,29 directed non-self edges,32 edge endpoints,118 isolates,126 weak components; largest component16. Comparison associations không phải graph edges.
- Eligible lead links gồm1.087 occurrences tới804 distinct resolved targets ngoài cohort,35 tới in-cohort ineligible,31 eligible-internal occurrences/29 edges,2 self links và368 unresolved identities.804 targets chưa được chứng minh eligible, primary type hoặc đủ namespace/article-kind constraints cho cohort mới.
- Comparison có120/159 candidates trong một subtype/attribute block;15/29 Bridge đi tới cùng một league-series hub. Counts cao không tương đương reasoning diversity.

Hiện `CandidateCollector.discover()` round-robin theo stream definitions và buffer50 hints được seed-sort; nó không phân bổ exposure theo8 entity-group quotas hay theo lead connectivity. `cmtype=page` không mở rộng cây subcategory. [collect.py](../../backend/src/vimfc/pipeline/collect.py), [config đang dùng](../../data/replays/quality21-20261026/pipeline.json).

## 2. Các phương án và lựa chọn đề xuất

| Phương án | Lợi ích | Hạn chế |
|---|---|---|
| Chỉ đổi category/seed weights | Nhỏ, dễ tái lập, cải thiện type exposure | Không chủ động nối eligible graph; category trực tiếp có thể rất hẹp |
| Chỉ mở rộng hyperlink neighbors | Tận dụng evidence link đã có | Dễ phình quanh hubs/series, làm yếu coverage nhóm thiếu và thiên lệch seed |
| **Stratified seeds + bounded lead-neighbor rounds** | Kiểm soát coverage và connectivity trong cùng budget | Cần discovery manifest, deterministic rounds, origin/propensity accounting và đánh giá riêng |

Đề xuất phương án ba, nhưng không tự áp dụng tỉ lệ tối ưu từ Pilot1.000. Điểm bắt đầu để thử nghiệm là **70% seed exploration /30% lead-neighbor slots**, seed fixed, quotas và hub/series caps khai báo trước.70/30 chỉ là treatment được đề xuất cho pilot mới; cần so với seed-only và legacy trên cùng budget. Không phải yêu cầu nghiên cứu mới hoặc guarantee yield.

## 3. Strata và eligibility tách biệt

Giữ corpus quotas hiện tại: Person5.000, Organization3.000, Location3.000, Event2.500, Creative Work2.500, Award/Competition1.500, Landmark/Facility1.500, Science/Technology1.000.

Mỗi seed hint có `expected_stratum`, origin và lý do ưu tiên; **primary group chỉ được xác định bởi historical classification**. Không nhận category/title hint làm primary proof hoặc subtype proof. Một hint dự kiến Organization có thể được classify Location/Creative Work hoặc unresolved; lưu kết quả vào đúng nhóm đã chứng minh.

Thiết kế seed catalog được review, version hóa, có exploration floor cho cả8 strata và fallback namespace budget giới hạn. Nếu cần subcategories, expansion phải được giới hạn depth/list budget, pin inventory và dedup cycle; category membership vẫn là acquisition hint, không phải historical classification evidence. Không biến một stream Science có2 observations thành kết luận Science không có nguồn cung.

Candidate budget có thể tính ở **ranh giới round** từ quota deficits và yield estimates đã đóng băng trước round: `ceil(deficit_g / conservative_yield_g)`, có smoothing/exploration floor, cap tổng và cách xử lý zero/unknown yield. Không học weights từ completion order, cache hits hoặc request latency. Mọi cập nhật weights phải là declared deterministic round input, không một adaptive mutable state ngầm.

Global yield cần ít nhất25% để20.000 eligible nằm trong80.000 unique candidates; đây là điều kiện số học, không phải forecast. Ngay cả25% global yield cũng chưa đủ nếu nhóm dư không thể dùng cho quotas thiếu. Report quota-usable yield và per-stratum outcomes, không chỉ tổng eligible.

## 4. Hyperlink frontier

Chỉ eligible seeds có extraction hoàn chỉnh, historical identity/provenance hợp lệ và không bị quarantine mới đóng góp frontier. Mỗi frontier entry giữ source page/revision/raw hash/sentence/link span, original target, canonical historical target proof, snapshot, origin set và mọi duplicate-parent occurrences.

Trình tự đề xuất:

1. Đọc **retained Full Lead prose links**, không body/infobox/hatnote/navigation links.
2. Resolve canonical target ở đúng snapshot; chưa đủ namespace/history/identity proof thì pending rõ ràng.804 targets trong audit là inventory cơ hội, chưa phải một approved candidate manifest.
3. Dedup canonical page IDs với seed pool và các rounds trước; giữ toàn bộ origins thay vì chỉ origin thắng đầu tiên. Một page chỉ chiếm một unique-candidate slot.
4. Áp declared per-seed/per-hub/per-series exposure caps và exploration floor. Không để15 links vào cùng league hub tạo15 cách đếm độc lập nguồn cung.
5. Ưu tiên target có tiềm năng giúp nhóm thiếu hoặc nối components bằng **hints được khai báo**, vẫn phải vượt đủ acquisition/classification/extraction/quality. Unknown type không được gán chắc chắn vào group thiếu.
6. Chỉ sau khi target eligible mới tạo operational edge từ link witness thật. Không sinh edge từ title match, comparison block hoặc predicted relation. Inbound connectivity chỉ được tính từ reverse index của lead edges đã materialize; không dùng full-article backlinks làm evidence graph.

Không tự đưa35 in-cohort ineligible targets vào graph bằng cách nới thresholds. Cần báo vì sao chúng bị loại; cải thiện extraction/classification nếu có evidence là một versioned task riêng.

## 5. Deterministic rounds và logical input

Tên version dự kiến: `discovery-stratified-link-1`; tên/schema là đề xuất. Current run vẫn giữ nguyên config/candidate order/manifests.

```text
pin seed/hint inventories + policy + seed
    -> deterministic round candidate manifest
    -> finish/report every candidate outcome in round
    -> freeze eligible seed set + validated lead frontier
    -> deterministic next-round manifest
```

Chưa xử lý đủ source outcomes thì pause hoặc seal round với các deferred/quarantined states theo explicit policy; không lấy tập "workers hoàn tất nhanh nhất" để mở rộng. Một sealed incomplete round không được tự thêm seeds khi cache/source được warm sau đó dưới cùng manifest ID. Recovery giữ pending round, hoặc tạo input/round identity mới theo policy; không sửa lịch sử.

Default được đề xuất là **pause khi còn operational deferred hoặc integrity chưa review**. Sealing một incomplete round chỉ là design option cần quyết định riêng; không tự cấp quyền discovery/cohort tiếp theo hoặc bỏ current PilotControls/data-completeness gates. Known quarantine vẫn giữ non-evidence và acceptance false theo contract hiện tại.

Sort/tie keys được định nghĩa bằng seed, stable origin/round ID và canonical page ID; không dùng filesystem order, wall-clock, API response order hoặc in-memory set order. Lưu API continuation/buffer/cursor response identities; live category/allpages listings chỉ là hints. Muốn replay discovery universe phải cung cấp archived hint/round manifests, không chỉ same snapshot timestamp.

Physical cache chỉ giảm acquisition cost. Cùng discovery logical inputs phải cho cùng page IDs/order/origin provenance và checkpoints sau restart; QID5 classification vẫn chạy trên exact immutable manifests. Không cấp thêm semantic depth vì một QID đã cached.

## 6. New-run/version boundaries

- Discovery weights, seed catalog, frontier expansion, dedup/origin semantics hoặc stopping rules thay đổi: version discovery/config, **new run** và new cohort manifest. Không append lại vào cohort1.000 hiện tại để gọi đó là same-input replay.
- Curated ontology roots hoặc primary decision certificates thay đổi: classification/rules/policy version mới, **new run/manifests**. Chỉ discovery đổi thì giữ qid-5 nếu semantics và inputs tương thích.
- Factual subject/date-role hoặc pair ranking/blocking thay đổi: version quality/ranking tương ứng và run mới; không diễn giải lại159 comparisons hiện có.
- Reuse selected historical raw revisions/hash-compatible artifacts và compatible stages. Không reuse derived results nếu rules/manifest/input identity khác.
- Quarantine không phải eligible evidence và vẫn block completeness theo contract hiện hành; không dùng discovery budget hoặc candidate replacement để che source failure.
- Operational approval hiện tại pin run/config; new run không tự kế thừa quyền quarantine. Review scope mới phải được cấp tường minh, không sửa old approval/marker.

## 7. Evaluation trước Pilot10.000

Thiết kế ablation riêng cho legacy/seed-only/70–30 treatment với cùng snapshot, semantic rules, unique-candidate và request budgets. Pin archived source inventories, machine/cache conditions và contact/API policy. Discovery treatment tạo cohort khác; không gọi chênh lệch yield giữa hai cohorts là same-input replay. Classification changes cần matched archived-cohort retrospective và held-out ontology controls riêng.

| Nhóm | Metrics bắt buộc báo | Giới hạn diễn giải |
|---|---|---|
| Source/yield | processed/N, deferred/quarantine/source_failed/N; eligible/N và eligible/processed; quota-usable eligible/N; per-origin/stratum counts | Không loại deferred khỏi denominator chính; không suy population yield từ một alphabet-prefix cohort |
| Type coverage | distribution theo proven primary/subtype; quota deficits; TV distance so với target composition; unknown/ambiguous riêng | Expected stratum không phải actual entity type |
| Lead graph | unique directed non-self edges; non-isolated fraction; isolates; WCC sizes/largest share; in/out-degree; resolved outside frontier và unresolved identities riêng | Comparison edges không được trộn vào lead graph |
| Pair coverage/diversity | unique pair endpoints/eligible; counts theo subtype/attribute/domain/scope; hub top-share; HHI/effective categories; duplicate chains | Raw pair permutations không đo số claims độc lập |
| Human2-hop | pair conversion: pairs tạo được ≥1 accepted necessary2-hop claim / pairs đã thử authoring; claim validation: accepted2-hop claims / authored2-hop claims reviewed | Hai denominators khác nhau; hiện chưa có human review nên N/A, không phải0% |
|3-hop potential | simple A→B→C với3 distinct docs; shortcuts A→C; subject-compatible paths; human-confirmed3-hop claims riêng | Hai graph edges hoặc ba documents không tự chứng minh3 necessary factual steps |
| Cost/reproducibility | physical/logical requests, errors/retries/backoff, cache rates, new/restored work, sampled peak RSS gồm exports, exact manifest/order replay | Warm/cold và workload khác nhau phải tách scope; không ngoại suy RAM tuyến tính |

Human review dùng đúng existing external two-annotator policy; không thêm app-managed review workflow hoặc thay quotas. Đề xuất review all29 Bridge và50 stratified Comparison:22 event_end,8 Actor birth,6 Footballer birth,6 University founded,4 event_start,3 generic event,1 film release. Tổng79 pairs là plan, **chưa được human review**. Freeze sample IDs/seed/inclusion probabilities trước đánh giá; báo stratum-weighted estimates và uncertainty có xét shared endpoints/hubs.

Reviewer cần kiểm tra factual-step necessity, date/entity scope và whether claim đã được khẳng định trực tiếp bằng một fact. Document ablation chỉ là diagnostic về cross-document necessity: project vẫn cho phép genuine single-document multi-hop. Title-only shortcuts hoặc một endpoint đã chứa đủ facts cần được ghi nhận; không tự đổi gold hops/labels bằng heuristic. Không dùng unresolved sources để tạo NEI sai prescribed construction.

Trước khi xin Pilot10.000: giải quyết source/integrity gate hiện tại; design được review, version/run isolation và offline regression suite PASS; completed staged pilots của semantics mới có đủ reports; đo yield/type/connectivity/diversity và external human yield. Không tự đặt một tỷ lệ human yield tối thiểu chưa được nghiên cứu phê duyệt, và không tự chạy cohort tiếp theo.

## 8. Regression plan và risks

Chi tiết dependency order/files/tests tại [implementation plan](../superpowers/plans/2026-10-27-stratified-hyperlink-discovery.md). Tất cả steps implementation còn unchecked.

Rủi ro chính: sampling bias từ seed/topic/hub, zero-history strata, frontier feedback làm lệch type coverage, source lag làm round không seal được, payload duplication/RAM và provenance growth. Giới hạn frontier bằng declared caps; lưu immutable references/hashes thay full duplicated entities; sampler bao gồm report exports; không bỏ validation để tiết kiệm bộ nhớ. Không tăng host concurrency hoặc nới maxlag5.
