# Pilot 100 qid-4 — đánh giá sau lượt chạy của người dùng

**Run:** `0b8c25da-3bf5-4386-856d-d6b3b5dc46bf`. **Ngày:** 2026-10-25.

**Verdict:** PASS kiểm tra execution, historical inputs, manifest/fingerprint và replay trên artifacts của run này. Coverage vẫn hạn chế; chưa khuyến nghị Pilot 1.000/Production trước khi review coverage và tài nguyên. Người dùng chạy pilot; lượt đánh giá này không gọi API, không chạy lại pilot, không sửa code/config hay database/raw cache.

## Kết quả thực tế

| Chỉ số | Pilot 2 qid-2/lead-2 | Pilot mới qid-4/lead-3 |
|---|---:|---:|
| Candidates | 100 | 100 |
| Extraction hoàn chỉnh / unresolved | 92 / 8 | 92 / 8 |
| Classification supported | 27 | 27 |
| Classification unclassified | 30 | 30 |
| Classification pending | 43 | 0 |
| Classification terminal unresolved | 0 | 43 |
| Eligible | 16 | 16 |
| API requests | 531 | 0 |
| Errors / retries | 0 / 0 | 0 / 0 |
| End-to-end giây | 226,45 | 33,83 |
| Bridge / Comparison pairs | 0 / 0 | 0 / 0 |

Exit code đã lưu là `0`; log kết thúc ở processed=100 và thông báo STOP. Thời gian pipeline đo là 33,8303 giây, không bao gồm toàn bộ chi phí khởi động/ghi báo cáo của wrapper. Đây là warm-cache replay với hit rate 100%, không phải cold benchmark và không đủ để ngoại suy chi phí production.

## Các kiểm tra trực tiếp đã PASS

- Context, archived run config, config bundle trên đĩa và metrics khớp config hash `6320532acedaa40689021bb96399a647c0a9119d1a1603cae3963d0576e7a8f7`.
- Cùng Fixed Snapshot `2026-10-25T03:00:00Z`, cùng 100 candidates và cùng discovery order với Pilot 2. Cohort hash `96bb4880e587ecc1de2528db25bf80cb446c94e930280f56638205bb40c8a173`.
- 100 manifest files khớp database/binding/content hash; classification và outer process dependency fingerprints đều khớp manifest đã pin. Không có stage failed/pending trong run mới; `stage.status=completed` bao gồm cả classification unresolved có reason kết thúc rõ ràng.
- Materialize trực tiếp từ raw cache với `allow_fetch=False`: 5.378 node occurrences, 969 QIDs khác nhau. Không có artifact thiếu/hỏng trong các manifest được kiểm tra. Kiểm tra identity, cutoff, raw hashes và graph/frontier bằng production validators.
- Pure classification replay khớp 100/100 kết quả đã lưu; quality replay khớp 100/100; 16 eligible articles qua validator dùng cho freeze. Đây không phải freeze corpus 20.000 bài.
- Manifest IDs và outcomes của 100 bài khớp [offline historical audit qid-4](../../verification/qid4-historical-audit.json).
- Pair manifest và selection replay khớp; exported report, các audit riêng và immutable attempt report khớp `metrics.json`.
- Hai run cũ giữ nguyên fingerprints của 20 bảng theo run và 20 report files. Năm shared source tables cũng không đổi. Actual DB có 3 runs; hai run đầu vẫn qid-1/qid-2, run mới qid-4. Chưa có actual qid-3 pilot.
- Database đọc bằng `mode=ro&immutable=1` sau khi xác nhận WAL rỗng; size/mtime DB/WAL/SHM không đổi trong kiểm tra. Không mở actual DB qua writable PipelineStore hoặc CLI status/report trong lượt đánh giá.

## 43 unresolved là policy limit, không phải retry failure

57 manifests complete; 43 incomplete do `dependency_policy_limit`, tổng 147 frontier entries đều ở depth 13. Trong 43 bài đó, 26 đã chứa configured root nhưng vẫn còn nhánh bắt buộc chưa giải quyết. Không tự completed khi chỉ thấy một root.

Kết quả này đúng với policy bounded-bfs-1: semantic depth 12, node budget 2.048 áp dụng thống nhất cho cache và source. Cached extension sâu hơn của qid-3 không được dùng ngầm. Không có input failure, node-budget exhaustion hoặc negative historical-unavailable evidence trong run mới. Không được diễn giải các dependencies ngoài bound là revision thực sự không tồn tại.

Run này xác nhận warm-cache execution và replay từ manifest; không phải phép đối chứng live cold/warm độc lập. Đối chứng physical cold-cache/source restoration trước đó sử dụng fixtures/MockTransport. Không dùng một warm run để tuyên bố đã kiểm chứng mọi hành vi API ngoài thực tế.

## Extraction và zero pairs

8 unresolved extraction vẫn được quarantine; không đưa provisional output vào eligible selection. Các diagnostics còn gồm `sclass`, `cvt`, `grt`, `malformed_template`, `usa` và một số factual templates tiếng Việt. Không phát hiện hồi quy về số bài hoàn chỉnh trong lượt này; không thực hiện audit thủ công lại từng factual clause của toàn bộ 92 bài.

16 eligible articles không có internal non-self lead edge và không có shared subtype/attribute block đủ hai bài. Vì vậy 0 Bridge/Comparison pairs phù hợp input hiện tại; không phải bằng chứng pair algorithm sai. `corpus.graph.internal_edge_count=1` có tính self-link, còn pair audit dùng non-self edges nên bằng 0; không mâu thuẫn.

## Rủi ro tài nguyên cần xử lý trước khi mở rộng

- Peak RSS **601.960.448 bytes ≈ 574,07 MiB**. Samples tăng từ khoảng 37,26 MiB lúc đầu lên 574,07 MiB cuối invocation.
- Sampler ghi disk growth **299.761.968 bytes ≈ 285,88 MiB**, I/O read 519.095.978 bytes, I/O write 622.411.510 bytes. Disk-growth scope có thể bao gồm SQLite/WAL; không coi toàn bộ số này là raw corpus mới. Shared raw/source tables không đổi.
- Payload `classify` checkpoints tổng **94.776.131 bytes**; `process` checkpoints **95.494.052 bytes**. Classification còn được lưu trong bảng `classification_results`, tạo thêm một bản payload. Đây là duplication thực tế, dù dependency identities đúng.
- Code giữ prepared items theo article trong `HistoricalProvider._prepared` (`provider.py:93`) và giữ tất cả processed records (`runner.py:195`); builder gọi preparation trước process, còn process gọi preparation lần nữa. Đây là các vị trí cần profile/khử lặp payload/materialization trước khi scale. Chưa đo heap allocations để khẳng định toàn bộ RSS growth đến từ một vị trí, chưa kết luận memory leak, và không ngoại suy tuyến tính lên 20.000 bài.
- Warm-cache network performance đã tốt; local serialization/materialization/storage footprint là vấn đề khác cần đánh giá riêng. Chưa sửa implementation trong lượt review này.

## Quyết định sau review

Đã hoàn thành mục tiêu kiểm chứng run qid-4 vừa chạy: inventory/fingerprints rõ ràng, 100/100 kết quả replay được từ historical artifacts, không có source failure hoặc stale dependency mismatch được phát hiện. Coverage không tăng: 43 bài vẫn ngoài policy depth; 16 eligible và 0 pairs giữ nguyên.

Khuyến nghị giữ điểm dừng trước Pilot 1.000. Bước tiếp theo cần review riêng trade-off coverage và footprint RAM/SQLite; không tự tăng depth, hạ eligibility hay chạy cohort lớn hơn. Log và artifacts gốc được giữ nguyên.

Nguồn: [metrics](100/metrics.json), [performance](100/performance.json), [manifest audit](100/dependency-manifest-audit.json), [fingerprint audit](100/fingerprint-audit.json), [classification audit](100/classification-audit.json), [template audit](100/template-audit.json), [pair audit](100/pair-audit.json), [run context](100/run-context.json), [export](100/pilot100-export.json), [exit code](100/pilot100-20261025-184847-7538536.exit-code.txt).
