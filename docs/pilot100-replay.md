# Chạy lại Pilot 100 sau sửa lỗi — thao tác của người dùng

Chỉ chạy các lệnh dưới đây khi muốn bắt đầu Pilot 100 mới sau lần 2. Agent chưa chạy pilot này. Không có lệnh Pilot 1.000 hoặc Production trong hướng dẫn này.

Nguồn đã kiểm tra trên máy này:

- Repository: `C:\ViFC_MultiHop_Data`.
- Python đã có và đã kiểm tra cài backend offline: `.tools\python312\python.exe`.
- Database: `data\pipeline.sqlite`; raw cache tương ứng: `data\raw`.
- Báo cáo nguồn thực tế: `reports\pilots\9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e\100\metrics.json`.
- Run nguồn: `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e`; snapshot lưu trong run/report: `2026-10-25T03:00:00Z`, tức 10:00 ngày 25/10/2026 tại Việt Nam.

Lệnh `prepare-replay` đối chiếu report với config/hash/snapshot trong database, sao chép contact/user-agent/seed/streams/quotas/ranking từ run cũ, dùng extraction/classification rules mới và tạo bundle config riêng. Nó không tạo run và không gọi API. Lệnh `create` sau đó tạo UUID mới và sao chép cohort/cursor tương thích; raw revisions vẫn dùng cache chung theo revision/cutoff. Không sửa kết quả run nguồn.

## 1. Chuẩn bị config và tạo run mới — chạy một lần

Copy cả block vào PowerShell Windows. Timestamp dưới đây chỉ đặt tên bundle/log, **không chọn snapshot**. Snapshot và source run được đọc, xác thực từ dữ liệu đã lưu.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ErrorActionPreference = 'Stop'
$Repo = (Get-Location).Path
$Python = Join-Path $Repo '.tools\python312\python.exe'
$Database = Join-Path $Repo 'data\pipeline.sqlite'
$SourceReport = Join-Path $Repo 'reports\pilots\9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e\100\metrics.json'
$ReplayRoot = Join-Path $Repo 'configs\replays'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'
$Bundle = Join-Path $ReplayRoot ('pilot100-' + $Stamp)
if (!(Test-Path -LiteralPath $Python)) { throw "Missing Python: $Python" }
New-Item -ItemType Directory -Path $ReplayRoot -Force | Out-Null
$SetupLog = Join-Path $ReplayRoot ('setup-' + $Stamp + '.log')
$env:VIMFC_OFFLINE = '1'

# Cài đúng source hiện tại; không tải package qua mạng.
# Windows PowerShell 5 cần Continue khi gộp native stderr qua Tee-Object.
$ErrorActionPreference = 'Continue'
$LASTEXITCODE = $null
& $Python -m pip install --no-index --no-build-isolation --no-deps .\backend 2>&1 |
    Tee-Object -FilePath $SetupLog
$InstallExit = $LASTEXITCODE
$InstallExit | Set-Content -LiteralPath ($SetupLog + '.exit-code.txt') -Encoding UTF8
$ErrorActionPreference = 'Stop'
if ($null -eq $InstallExit -or $InstallExit -ne 0) { throw "Backend install failed: $InstallExit; see $SetupLog" }

$PreparedText = & $Python -m vimfc pipeline prepare-replay --database $Database --source-report $SourceReport --config .\configs\pipeline.json --output-dir $Bundle
$PrepareExit = $LASTEXITCODE
if ($PrepareExit -ne 0) { throw "prepare-replay failed: $PrepareExit" }
$Replay = ($PreparedText -join "`n") | ConvertFrom-Json

$CreatedText = & $Python -m vimfc pipeline create --database $Replay.database --snapshot $Replay.snapshot --config $Replay.config --reuse-run $Replay.source_run
$CreateExit = $LASTEXITCODE
if ($CreateExit -ne 0) { throw "create failed: $CreateExit" }
$RunId = (($CreatedText -join "`n") | ConvertFrom-Json).run_id
if (!$RunId -or $RunId -eq $Replay.source_run) { throw 'Expected a new run ID' }
$ReportDir = Join-Path $Repo ('reports\pilots\' + $RunId + '\100')
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$ContextFile = Join-Path $ReportDir 'run-context.json'
[ordered]@{
    python = $Python
    database = $Replay.database
    run_id = $RunId
    source_run = $Replay.source_run
    snapshot = $Replay.snapshot
    config = $Replay.config
    config_hash = $Replay.config_hash
    candidates = 100
    report_dir = $ReportDir
} | ConvertTo-Json | Set-Content -LiteralPath $ContextFile -Encoding UTF8
$ContextFile | Set-Content -LiteralPath (Join-Path $ReplayRoot 'latest-context.txt') -Encoding UTF8
Write-Host "NEW RUN: $RunId"
Write-Host "SNAPSHOT: $($Replay.snapshot)"
Write-Host "CONTEXT: $ContextFile"
Write-Host "REPORTS: $ReportDir"
```

Bundle mới dùng `qid-3`, `lead-3`, `pipeline-3`; snapshot, ranking, taxonomy và eligibility giữ nguyên. Giới hạn truy xuất API vẫn 12; chỉ dependency đã có trong cache lịch sử mới được kiểm tra sâu hơn đến khi closure khép kín, có node budget riêng.

Bundle config chứa contact đã lưu của bạn; `configs/replays/` được gitignore. Không thay đổi rules trong bundle hoặc file config của run đang chạy; nếu thay đổi rules lần nữa phải tạo run khác.

## 2. Người dùng tự chạy đúng 100 candidates, lưu stdout/stderr và exit code

Block này cũng đọc được context nếu bạn đã đóng/mở lại terminal. Không chạy lại bước 1 khi chỉ muốn resume cùng run.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ContextFile = (Get-Content -LiteralPath .\configs\replays\latest-context.txt -Raw -Encoding UTF8).Trim()
$Context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json

# Chỉ bỏ offline guard khi chính bạn sẵn sàng chạy pilot có truy cập nguồn.
Remove-Item Env:\VIMFC_OFFLINE -ErrorAction SilentlyContinue
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-Pilot100.ps1 -ContextFile $ContextFile
$PilotExitCode = $LASTEXITCODE
Write-Host "Pilot exit code: $PilotExitCode"
Get-ChildItem -LiteralPath $Context.report_dir -Filter 'pilot100-*' |
    Sort-Object LastWriteTime | Select-Object FullName,Length,LastWriteTime
```

Script chỉ truyền `--candidates 100`, khóa độc quyền theo run, chạy Python `-u`, gộp `2>&1 | Tee-Object` ngay lúc chạy. `$LASTEXITCODE` được lấy ngay sau pipeline và ghi file riêng; không dùng `$?` hoặc exit code của `Tee-Object`. Mỗi lần gọi có log mới, không ghi đè log trước. Không chạy đồng thời một CLI pilot khác bằng tay cho cùng run.

Windows PowerShell 5.1 ghi log Tee ở UTF-16LE; xem bằng `Get-Content` tự nhận BOM. File exit code và JSON là UTF-8. Một số dòng stderr được PowerShell 5.1 bọc thành `NativeCommandError` trong log; chúng vẫn được ghi, exit code lấy từ Python.

## 3. Kiểm tra trạng thái và xuất báo cáo — không truy cập API

Chạy sau khi pilot kết thúc. Muốn xem tiến độ trong terminal thứ hai thì chỉ chạy lệnh `status` trong block này.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ContextFile = (Get-Content -LiteralPath .\configs\replays\latest-context.txt -Raw -Encoding UTF8).Trim()
$Context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json
$env:VIMFC_OFFLINE = '1'
& $Context.python -m vimfc pipeline status --database $Context.database --run $Context.run_id
$StatusExit = $LASTEXITCODE
if ($StatusExit -ne 0) { throw "status failed: $StatusExit" }

$Export = Join-Path $Context.report_dir 'pilot100-export.json'
& $Context.python -m vimfc pipeline report --database $Context.database --run $Context.run_id --pilot-size 100 --output $Export
$ReportExit = $LASTEXITCODE
if ($ReportExit -ne 0) { throw "report failed: $ReportExit" }
Write-Host "RUN: $($Context.run_id); SNAPSHOT: $($Context.snapshot)"
Write-Host "EXPORTED REPORT: $Export"
Get-Content -LiteralPath (Join-Path $Context.report_dir 'summary.md') -Encoding UTF8
```

Nếu bị ngắt trước khi hoàn tất cohort, `status` vẫn đọc được checkpoint; báo cáo `--pilot-size 100` chưa tồn tại sẽ trả lỗi rõ ràng. Có thể xuất audit checkpoint hiện có bằng cách bỏ `--pilot-size 100` và chọn output khác, ví dụ `checkpoint-audit.json`. Đây là báo cáo trạng thái, không phải Pilot 100 hoàn chỉnh.

## 4. Retry/resume cùng run, chỉ khi cần

Xem `classification-audit.json` để phân biệt `retryable` và `retry_at`. Chờ đến hạn Retry-After rồi chạy block sau. `pipeline retry` chỉ requeue stage transient đã đến hạn; không gọi API, không bỏ cooldown, không retry vô hạn lỗi cấu trúc. Sau đó dùng lại bước 2, **không tạo run khác**. Các stage hoàn chỉnh/raw đã lưu được tái sử dụng; pending classification được cập nhật thay vì chèn trùng.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ContextFile = (Get-Content -LiteralPath .\configs\replays\latest-context.txt -Raw -Encoding UTF8).Trim()
$Context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json
$env:VIMFC_OFFLINE = '1'
& $Context.python -m vimfc pipeline retry --database $Context.database --run $Context.run_id
$RetryExit = $LASTEXITCODE
if ($RetryExit -ne 0) { throw "retry failed: $RetryExit" }
# Sau khi kiểm tra số requeue và đến hạn retry, chạy lại bước 2.
```

`classification.outcome=unresolved` với `missing_or_invalid_historical_dependency`, `hierarchy_node_budget_exhausted` hoặc lỗi cache là trạng thái kết thúc chưa đủ căn cứ, không retry transient. Xem `diagnostics` và `closure_summary` để biết QID thiếu, frontier, depth và budget. Cần bổ sung bằng chứng/rules trong run mới; không được biến chúng thành complete chỉ để tăng số eligible. `unclassified/no_supported_rule` chỉ dùng khi closure đã khép kín mà không có root hỗ trợ. Template chưa hỗ trợ vẫn giữ unresolved extraction. Một lần chạy xong đủ 100 attempts có thể vẫn còn unresolved/pending; `completed` của báo cáo cohort không có nghĩa 100 bài đủ điều kiện.

## Vị trí cần gửi lại

Tất cả nằm dưới `reports\pilots\<run_id_mới>\100\`, đường dẫn thật được lưu trong context và in ở bước 1:

| File | Nội dung |
|---|---|
| `run-context.json` | Run ID mới, run nguồn, snapshot, config, database |
| `pilot100-<timestamp>.log` | stdout/stderr của đúng lần chạy |
| `pilot100-<timestamp>.exit-code.txt` | Exit code Python/script, `0` là tiến trình kết thúc bình thường |
| `metrics.json` | Performance, API/backoff/cache, counts, audit và corpus của lần gọi gần nhất |
| `performance.json` | Performance riêng: wall time, request/cache/retry/resource counters |
| `pair-audit.json` | Cạnh lead giữa các bài khác nhau, self-links, blocks subtype/attribute, generated/positive counts và lý do zero pairs |
| `template-audit.json` | Template hỗ trợ/loại non-prose/unknown, affected articles |
| `classification-audit.json` | Outcome, roots, provenance, blocked branches, retryability/deadline và request context |
| `cohort.json` | 100 candidates được replay |
| `summary.md`, `summary.json` | Tóm tắt |
| `pilot100-export.json` | Bản xuất báo cáo ở bước 3 |
| `attempts\<attempt_id>\metrics.json` cùng performance/template/classification/pair audits | Lịch sử bất biến của từng lần hoàn tất/resume, tránh mất số liệu lần trước |

Nếu có resume, gửi tất cả log/exit-code cùng `attempts/` và báo cáo cuối. Metrics của mỗi invocation đo riêng lần đó; replay có raw cache cũ không phải cold benchmark để ngoại suy Production. Không suy luận chất lượng pair selection từ cohort còn thiếu extraction/classification. Sau Pilot 100, dừng để đánh giá log/báo cáo; chưa chạy Pilot 1.000.

Bộ sửa lần 2 được kiểm tra offline; [per-article audit](../reports/verification/pilot2-remediation-audit.json). Không suy ra kết quả live của lần replay mới từ phân tích cache. Sau khi chạy, gửi `run-context.json`, tất cả log/exit-code, `metrics.json`, `performance.json`, `template-audit.json`, `classification-audit.json`, `pair-audit.json` và `pilot100-export.json`.
