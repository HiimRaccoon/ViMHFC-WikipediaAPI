# Resume Pilot 1.000 sau sửa cooldown — thao tác của người dùng

**Run lead-4 mới,27/10/2026:** `666068c8-3e97-4a7b-9a9b-f1e451c32a23` đã có đúng một reviewed-quarantine invocation được cho phép:991 processed,150 eligible,8 deferred hints,1 quarantine; native exit5 và data/pilot acceptance false. [Kết quả, comparison và audit](../reports/verification/pilot1000-reviewed-quarantine-20261027.md). Các lệnh lead-3 bên dưới áp dụng cho run cũ đã ghi rõ, không phải run lead-4. Run mới yêu cầu driver `scripts/run-reviewed-pilot.py` với matching `--reviewed-quarantine configs/reviews/integrity-23640705-20261027.json`; không tự lặp invocation hoặc đổi approval để vượt gate. Task hiện tại đã DỪNG; chờ review/ủy quyền tiếp theo.

**Health gate mới:** Lệnh resume dưới đây tự preflight Wikidata nếu có work phụ thuộc nguồn đến hạn. Trong cooldown/circuit/probe lease, không gửi thăm dò. Khi được phép, chỉ một request probe maxlag5; probe pass là advisory, request dữ liệu sau vẫn có thể mở circuit. Nếu gate chặn, exit5 kèm `paused=true`, `completed=false`, `health_report_path`; **chưa có worker nào chạy**, không cập nhật metrics dữ liệu cũ. Xem `host-health.json` hoặc `health-checks/<attempt>/health-report.json` bên cạnh log. Không lặp lại command liên tục để lách thời gian hold120–1800s/Retry-After. `pipeline report --pilot-size 1000` vẫn xuất data attempt gần nhất, không phải health receipt mới.

**Follow-up offline 26/10/2026:** Run hiện có 878 processed,121 deferred,1 terminal SHA-1 failure. Đã kiểm tra cache-only toàn bộ878 completed inputs. Dùng **`-DeferredOnly`** ở bước2 để chỉ vào worker cho deferred đến hạn;878 completed và1 failed được giữ nguyên. Tại kiểm tra11:09 giờ Việt Nam ngày26/10,113 đã đến hạn,8 hint phải chờ đến27/10 khoảng02:06–02:32. Qua deadline không chứng minh Wikidata đã hết lag. [Kết quả kiểm tra và giới hạn RCA](../reports/verification/pilot1000-followup-20261026.md).

Run đã kiểm tra chỉ đọc: `aa5c21ba-0e1d-4e5b-b0cd-8c56e6ed9b4b`, 1.000 candidates, `qid-5`/`lead-3`, snapshot `2026-10-25T03:00:00Z`, config hash `94e05c4fd75e7db926a0dc3838ca96daa4158889c1a01ad9d8b0592da73292d7`. Sau attempt thật trước đó, có878 manifests đã publish, không có transient source-error frontier. Checkpoint tương thích với thay đổi scheduler mang tính vận hành; không tạo run mới hoặc thay đổi config. Follow-up hiện tại chỉ kiểm tra/sửa offline, không chạy thêm API hoặc resume.

Python embedded trên máy này ưu tiên bản `vimfc` cũ trong site-packages. Các lệnh bên dưới dùng `scripts/vimfc-local.py` để bảo đảm chạy code checkout với đúng môi trường dependency, không cần cài lại hoặc sửa Python environment.

## 1. Chuẩn bị và kiểm tra trạng thái

Chỉ chạy khi không còn tiến trình pilot khác dùng cùng run. Context và mọi đường dẫn được đọc từ file đã có, không điền lại hoặc đoán đường dẫn config.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ContextFile = (Get-Content -LiteralPath .\reports\pilots\pilot1000-context.txt -Raw -Encoding UTF8).Trim()
$Context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Context.run_id -ne 'aa5c21ba-0e1d-4e5b-b0cd-8c56e6ed9b4b') { throw 'Context is for a different run.' }
$LocalCli = (Resolve-Path -LiteralPath .\scripts\vimfc-local.py).Path
$ReportDir = Join-Path $Context.report_root '1000'
$env:VIMFC_OFFLINE = '1'
& $Context.python -B $LocalCli pipeline status --database $Context.database --run $Context.run_id
$StatusExit = $global:LASTEXITCODE
if ($StatusExit -ne 0) { throw "status failed: $StatusExit" }
```

`candidate_work_counts` có thể rỗng trước resume đầu tiên vì đây là bảng bổ sung. Các failed stages cũ có retry deadline vẫn được nhận diện; không xóa checkpoint để chuyển trạng thái. `host_cooldowns` là deadlines đã lưu, có thể đã qua hạn. Status không gọi API; lần đầu mở store sẽ bổ sung bảng/cột mới, giữ nguyên dữ liệu cũ.

## 2. Resume đúng 1.000 candidates và lưu log/exit code

Block này chỉ được chạy khi chính bạn sẵn sàng cho phép truy cập nguồn. Script kiểm tra read-only context/run/config/cohort trước, lấy khóa, chạy pilot cùng run, dùng `2>&1 | Tee-Object`, và lưu exit code thật trong `finally`. Mỗi lần gọi có tên log mới. Không gọi `pipeline create`, không mở rộng cohort, không vượt gate.

```powershell
Remove-Item Env:\VIMFC_OFFLINE -ErrorAction SilentlyContinue
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Resume-Pilot1000.ps1 -ContextFile $ContextFile -MaxWaitSeconds 60 -RetryRounds 2 -DeferredOnly
$ResumeExit = $global:LASTEXITCODE
Write-Host "Resume exit code: $ResumeExit"
Get-ChildItem -LiteralPath $ReportDir -Filter 'pilot1000-resume-*' |
    Sort-Object LastWriteTime |
    Select-Object Name,Length,LastWriteTime
```

- Exit **0**: toàn cohort có terminal processing và không có pinned transient frontier; vẫn **chưa phải pilot accepted**, không đồng nghĩa mọi bài eligible.
- Exit **5**: có deferred/source failure hoặc pinned transient frontier; đã lưu báo cáo, chưa đủ dữ liệu để nghiệm thu. Xem `work-audit.json`, không chạy vòng lặp resume liên tục.
- Exit **1**: lỗi vận hành/ngắt chạy; stages đã hoàn thành được bảo toàn. Exit **2/3**: tham số/config/checkpoint không tương thích, dừng để kiểm tra.

`MaxWaitSeconds=60` chỉ giới hạn tổng thời gian scheduler chủ động ngủ; không phải timeout toàn pilot. Mỗi logical HTTP query vẫn có tối đa 5 attempts, tối đa 60 giây backoff tổng cộng và HTTP timeout hiện tại. Retry-After dài hơn được lưu rồi defer, không bị rút ngắn. Scheduler có tối đa 2 lượt thử lại sau lượt đầu, chỉ vào các bài đến hạn. Có thể dùng `-MaxWaitSeconds 0 -RetryRounds 0` để chạy một lượt rồi trả quyền điều khiển, không tự chờ.

`-DeferredOnly` không giới hạn báo cáo xuống121 bài: cohort vẫn1.000, terminal records được khôi phục và kiểm tra offline để tổng hợp audits/pairs/selection. Nó không discovery, không vào worker cho878 bài hoàn tất hoặc1 source_failed. Nếu cached terminal artifact thiếu/hỏng, dừng với incompatibility thay vì tự gọi nguồn để sửa. `scheduler.checkpoint_restored` phân biệt records đọc lại với số worker attempts mới; tốc độ không tính878 records này như công việc mới.

RAM report mới có `includes_report_exports=true`, bao gồm tạo/serialize/ghi các báo cáo chính và SQLite publication. `processing_seconds` và `report_export_seconds` tách scope; final telemetry flush được khai báo loại trừ. Backoff thuộc stage thực sự phát sinh request, materialization có timer riêng; không áp dụng lại attribution mới lên báo cáo cũ.

Trong terminal thứ hai, có thể dùng block status ở bước 1. Progress gồm `candidate_deferred`, source reason, retry deadline và visited/attempted/processed/deferred/source_failed. `source_wait` xuất hiện trước khi chờ; Ctrl+C giữ checkpoint. RAM, requests, errors, HTTP retries, cache và scheduler candidate attempts được ghi trong performance report; không đồng nhất các đơn vị này.

## 3. Xuất và xem báo cáo sau resume

```powershell
$env:VIMFC_OFFLINE = '1'
& $Context.python -B $LocalCli pipeline status --database $Context.database --run $Context.run_id
if ($global:LASTEXITCODE -ne 0) { throw 'status failed' }
$Export = Join-Path $ReportDir ('pilot1000-export-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '.json')
& $Context.python -B $LocalCli pipeline report --database $Context.database --run $Context.run_id --pilot-size 1000 --output $Export
if ($global:LASTEXITCODE -ne 0) { throw 'report export failed' }
$Metrics = Get-Content -LiteralPath (Join-Path $ReportDir 'metrics.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Metrics | Select-Object run_id,attempt_id,completed,data_complete,pilot_accepted,acceptance_status,deferred_count,source_failure_count,pinned_transient_manifest_count
$Metrics.work_counts | Format-List
$Metrics.performance.request_rates | Format-List
$Metrics.performance.resources | Format-List
$Metrics.performance.scheduler | Format-List
$Metrics.work_audit.articles |
    Where-Object { $_.status -in @('deferred','source_failed') } |
    Group-Object error_code | Select-Object Name,Count
Get-ChildItem -LiteralPath $ReportDir | Select-Object Name,Length,LastWriteTime
Write-Host "Export: $Export"
```

Gửi lại `metrics.json`, `performance.json`, `classification-audit.json`, `template-audit.json`, `pair-audit.json`, `dependency-manifest-audit.json`, `fingerprint-audit.json`, `subtype-audit.json`, **`work-audit.json`**, cohort, run context ở thư mục cha, log/exit-code mới và `attempts/<attempt_id>/`. Report aliases thể hiện lần gần nhất; bản trước được giữ trong `previous-latest/<hash>/`, attempts và logs cũ không bị ghi đè. Bridge/Comparison vẫn chỉ dùng articles đủ eligibility và các rules hiện tại.

## 4. Nếu vẫn còn deferred hoặc bị ngắt

Không cần gọi `pipeline retry` trước mỗi resume: pilot tự nhận diện due stages. Chờ `retry_at` trong work audit hoặc host cooldown rồi chạy lại **đúng block bước 2**, giữ run/config/context. Không dùng vòng lặp tự chạy vô hạn. Nếu muốn xem thao tác requeue offline, lệnh sau chỉ requeue transient stages đã đến hạn; nó không truy cập nguồn hoặc bỏ TTL/cooldown:

```powershell
$env:VIMFC_OFFLINE = '1'
& $Context.python -B $LocalCli pipeline retry --database $Context.database --run $Context.run_id
if ($global:LASTEXITCODE -ne 0) { throw 'retry preparation failed' }
```

Tám `item_hint={qid:null}` cũ được refresh tối đa một lần khi chuyển sang stage vận hành mới, nếu cần và chưa có hint/manifest được pin. Nếu nguồn vẫn không có hint, bài được deferred với TTL **24 giờ**, không retry liên tục hoặc đoán QID. Sau TTL, resume cùng run cho phép refresh tiếp. Positive hint vẫn phải khớp historical sitelink; không dùng current pageprops làm bằng chứng classification.

Tám hint đã được refresh trong attempt `5a638c8f-f206-4d8a-9b8d-929b0369aec7`; flag mới **không** cho phép refresh chúng thêm trước deadline. Case page421635/Q10761835/revision23640705 vẫn source_failed vì SHA-1; không xóa stage hoặc dùng retry để bypass. Chưa có original response/hashes để xác định nguyên nhân offline. Vì vậy dù deferred được giải quyết hết, run có thể vẫn exit5; xử lý121 deferred an toàn không đồng nghĩa toàn1.000 được nghiệm thu.

`unresolved` do depth 12/primary ambiguity không tự biến thành retryable. Manifest đã publish giữ nguyên dù cache mới đầy hơn. Nếu một report tương lai có `pinned_transient_manifest_count>0`, same-run resume không thể sửa optional inventory đã đóng băng: cần review và tạo run mới với compatible raw reuse để cải thiện input; không xóa/sửa binding. Hiện run được bàn giao không có trường hợp này.

Dừng sau resume và gửi báo cáo đánh giá. Không có lệnh Pilot 10.000 hoặc Production trong hướng dẫn này.
