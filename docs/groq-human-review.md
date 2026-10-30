# Human review cho Groq Classification — offline

Tool: `scripts/groq_human_review.py`. Không cần API key/GPU; không gọi Groq, không resume inference, không ghi pipeline/ledger/corpus. CLI chặn DNS và socket connections. Source chuẩn là frozen dataset-v2, 179 responses và full-review export đã có.

## Bắt đầu gán nhãn

Chỉ đưa thư mục **blind/** cho annotator. Mở `blind/labels.csv` bằng Excel (UTF8-BOM) hoặc sửa `blind/labels.jsonl`; chọn một định dạng làm bản chính. `reference.jsonl` là bản input bất biến, không sửa. Giữ coordinator/manifest/comparison tách khỏi annotator cho đến khi đã chốt nhãn.

Giữ nguyên review_id, page_id, revision_id, snapshot, fingerprint, title, lead_text và sentences. CSV chứa JSON trong cột sentences: mỗi câu có `id` và `text`; không đổi IDs hoặc nội dung. Cột notes là nhận xét của người review.

Mỗi hàng đã review cần người review và ngày ISO thật; không tự điền tên/ngày thay người khác. Điền `review_status=reviewed` và chọn đúng một trong ba cách:

| Quyết định của người review | human_primary | human_abstain | out_of_taxonomy |
|---|---|---|---|
| Đủ căn cứ cho một nhóm | Tên đúng của một trong8nhóm | false | false |
| Không đủ căn cứ/chưa chắc | Để trống | true | false |
| Rõ chủ thể nhưng ngoài taxonomy | Để trống | false | true |

`pending` hoặc ô gold trống không thành ground truth. Nếu chưa chắc, abstain thay vì sao chép nhãn qid-5/Groq. Dùng đúng definitions và precedence archived trong `blind/INSTRUCTIONS.md`; không tự nới Science/Technology thành mọi khái niệm chuyên ngành. JSONL giữ nguyên text; CSV có formula escaping. Không tự tra kiến thức ngoài supplied historical Lead để bù evidence.

## Sampling và giới hạn suy luận

Policy `human-review-diagnostic8-random3-1`, seed20261030. Packet hiện tại11bài: hai ca bắt buộc, tối đa5bất đồng verified-reference, một Science/Technology probe bổ sung, và3mẫu ngẫu nhiên có seed. Random draws: một mỗi baseline outcome; chọn đều strata reason/group rồi chọn đều bài trong stratum. Unresolved random slot được phân bổ trước cho `primary_dependency_missing`, bổ sung cho ambiguity diagnostic.

Manifest ghi actual frame IDs/counts và conditional inclusion probabilities. Đó là xác suất **trong frame có điều kiện**, không phải inclusion probability cho mọi179/300bài. Chẩn đoán được chọn chủ đích; evaluation random chỉ có3bài, đã loại diagnostic và giới hạn reason. Không có pooled headline accuracy, inverse-probability population estimate hoặc ngoại suy ra corpus. Không bảo đảm11bài đại diện đủ8nhóm.

## Prepare (khi thật sự cần một packet mới)

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_human_review.py prepare `
  --dataset '<EXISTING_DATASET_V2>' `
  --results '<EXISTING_FINAL_RESULTS_JSON>' `
  --review-export '<EXISTING_FULL_REVIEW_JSONL>' `
  --dataset-manifest 'ece7a99e23bbd95488abd226e3cc83ff703e33fff23a01d5eaa8f1181ae2e161' `
  --output '<NEW_GROQ_REVIEW_DIRECTORY>' --seed 20261030
```

Không regenerate dataset. Tool kiểm tra historical provenance bằng source loader cache-only và đối chiếu existing export; missing/corrupt source dừng, không fetch lại. Cùng inputs/seed cho cùng selection/order/manifest identity. Thư mục output phải mới, ngoài source/dataset/cache. `coordinator/manifest.json` pin checksums của reference/guide/comparison; editable labels không bị hash-lock vì đó là nơi bạn gán nhãn. Ghi lại manifest ID từ handoff; không re-sign manifest bị sửa.

## Import human labels và chấm

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_human_review.py import `
  --bundle '<REVIEW_DIRECTORY>' `
  --labels '<REVIEW_DIRECTORY>\blind\labels.csv' `
  --manifest-id '<PINNED_REVIEW_MANIFEST_ID>' `
  --output '<NEW_REVIEW_RESULTS_DIRECTORY>'
```

Có thể import một phần: các hàng pending vẫn pending. Không dùng comparison file làm labels. Import từ chối duplicate/unknown IDs, chỉnh source text/typed sentence IDs, sai enum, flags mâu thuẫn, thiếu reviewer/ngày và manifest/reference tamper. Tạo output mới cho mỗi lần import để giữ lịch sử review; không ghi accepted pipeline hoặc API ledger.

Output: `reviewed-labels.jsonl`, `metrics.json`, `report.md`. Khi chưa có gold thật, `AWAITING_HUMAN_LABELS` và metrics=null; không có accuracy giả bằng0.

## Metrics contract

- Báo riêng `diagnostic_purposive` và `stratified_random`; chỉ chấm các hàng người review đã xác nhận.
- Accuracy/precision/recall/Macro-F1 chính là closed8-group metrics trên human in-taxonomy gold. Model abstention và không có baseline label được tính sai/FN trên gold có nhóm, không loại khỏi denominator để tăng score.
- Macro precision/recall/F1 dùng **cùng label set** cho3approaches trong một partition: union của nhóm human gold và nhóm được các approaches dự đoán trên chính các gold rows đó. Zero division=0. Không đưa mọi nhóm chưa xuất hiện vào macro; per-group support và macro_labels được xuất rõ.
- Human uncertainty không phải gold nên bị loại khỏi accuracy/F1, nhưng có count riêng. Human out-of-taxonomy được báo riêng bằng assigned/declined/unresolved counts; không tự ánh xạ model abstain thành nhận diện đúng out-of-taxonomy vì schema model không phân biệt hai loại đó.
- Baseline qid-5, Groq-only và Wikidata-first là predictions/references được so với **nhãn người độc lập**; không hệ thống nào được dùng để sinh gold. Information sets khác nhau; coverage thấp không đồng nghĩa ontology sai, và score của11ca không phải score trên300.

Mọi human labels chỉ là input cho evaluation/policy review tương lai. Không tự nâng thành Wikidata-verified primary, eligible article, accepted manifest hoặc graph evidence.
