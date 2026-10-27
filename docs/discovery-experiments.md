# Discovery experiments: offline package và Windows handoff

Implementation: `stratified-lead-1` / new-run `pipeline-7`, giữ nguyên qid-5/lead-4/quality-2.1/rank-1, depth12 và snapshot2026-10-25T03:00:00Z. Không thay default collector của historical runs. Chỉ `run` gọi source; `offline`, `prepare`, `status`, `report`, `compare` không gọi API.

## Ba khái niệm khác nhau

| Sampling stratum / primary group | Quota gốc | Reporting group |
|---|---:|---|
|Person|5.000|Person|
|Organization|3.000|Organization|
|Location|3.000|Location|
|Event|2.500|Event|
|Creative Work|2.500|Creative Work|
|Award / Competition|1.500|Award / Competition|
|Landmark / Facility|1.500|Location — chỉ aggregation báo cáo|
|Science / Technology|1.000|**UNMAPPED, giữ riêng**|

Sáu reporting groups đầu sau aggregation có tổng quota tham chiếu19.000; Science/Technology1.000 vẫn được báo riêng, tổng toàn bộ20.000. Không gộp Science vào Creative Work. Sampling origin chỉ là hint; qid-5 quyết định actual primary bằng historical evidence. Comparison luôn dùng supported subtype, không dùng reporting group.

## Phạm vi implementation

- Legacy giữ thứ tự archived seeds. Seed-only cân8 sampling strata bằng weighted deficit theo quota hiện tại, rồi dùng exploration pool khi category inventory cạn. Hybrid chọn700 seeds +300 eligible-seed lead neighbors khi đủ dữ liệu/caps; sắp7 seed/3 neighbor trong mỗi10 slots để prefix100 cũng kiểm tra treatment.
- Canonical dedup giữ mọi origin; source page phải eligible/extraction completed trong **prior-run frozen inventory**. Chỉ genuine retained-lead links có exact sentence span, target revision/hash/snapshot proof và registered valid raw mới vào frontier. Không dùng body links, title-equality fake links hoặc Comparison edges.
- Frontier dùng conservative namespace subset: canonical historical titles không có dấu `:` thuộc main namespace. Mọi frontier colon-title được giữ ở omission audit, kể cả title hợp lệ namespace0, cho đến khi có explicit namespace proof. Archived seeds không được certify namespace chỉ từ sampling origin: khi chưa đủ proof, giữ `namespace=null` / `discovery_namespace_status=unverified`. Không gán type/subtype cho neighbor.
- New discovery provider bắt buộc kiểm tra source scope trước classification. Canonical title không separator phải đến từ selected historical identity; colon-title cần checkpoint namespace qua exact selected revision, page/revision/timestamp và current canonical title không đổi. Non-main hoặc moved/ambiguous title dừng candidate với reason rõ, không thành eligible. Namespace metadata là intake scope trong hợp đồng historical-title/move resolver hiện tại, không phải bằng chứng Wikidata và không chứng minh mọi lịch sử thay đổi namespace configuration. Scope receipt có ID trong process dependency hash/result và được restore cache-only; checkpoint thiếu không được bypass. Việc kiểm tra qua API này **chỉ xảy ra khi bạn chạy pilot sau**, không trong offline task.
- Mỗi seed cấp tối đa10 neighbors; candidates có nhiều origins không tiêu thụ nhiều slots. Neighbor thiếu được fallback bằng seeds hợp lệ và báo rõ actual mix/shortfall; thiếu unique candidates không được pad/replace source failures.
- Đây là **controlled frozen-inventory experiment**, không phải online adaptive collector. Inventory lấy từ prior-run checked evidence; worker speed/cache warm-up không thể thêm neighbors trong lúc pilot chạy. Muốn mở round tiếp hoặc thay listing universe cần inventory/round identity mới và run/cohort mới. Không tự tiếp tục80.000/Production.
- Cohort manifest pins policy, input inventory, semantic rule hashes, exact candidates/order/origins và quota/reporting mapping. Prepare tạo DB/run riêng; discovery mismatch không clone old cohort. Compatible raw/inner stages có thể reuse; không có derived reuse trái dependency identity.
- Private cache copies được khuyến nghị cho ba arms để API cost không hưởng warm-up từ arm chạy trước. Prepare cả ba trước bất kỳ live work nào. Baseline compressed-file hashes phải khớp; source database/context/raw/report cũ không bị sửa.

## Offline evidence và human evaluation

Package đã tạo: [release offline output](../reports/verification/discovery-release-20261027/). `offline-comparison.json` chỉ đo known archived-input subset trên cùng budget1.000; missing input/namespace proof giữ unknown, eligible yield đầy đủ là null. Seed-only và legacy trên inventory đúng1.000 seeds có cùng membership; không thể dùng chúng để kết luận stratification tăng population yield. Hybrid có novel targets nên cần pilot mới để biết full outcomes.

Manual79 pack: [index](../reports/verification/discovery-release-20261027/manual-review79/index.md).29 Bridge census +50 Comparison:22 season-end,8 Actor birth,6 Footballer birth,6 University founded,4 event-start,3 generic-event,1 film-release. Sample pins IDs/seed/frame hash/inclusion probabilities;79 pair cards, full leads/sentences, exact link/value witnesses, revision/raw/manifest provenance, heuristic risk features và hai blank external CSV sheets. Không có authored claim, gold label hoặc valid-hop verdict tự động. Đây là review pack của baseline run, không phải xác nhận namespace của những discovery experiments mới.

Hai reviewers độc lập kiểm tra subject/date/predecessor/series scope; evidence đủ và cần ít nhất hai factual steps; title/single-fact shortcuts; evidence sentence IDs. Genuine single-document multi-hop vẫn được phép. Tách pair-conversion yield khỏi authored-claim validation yield; denominator0=N/A. Population estimates cần sampling weights và uncertainty xét shared endpoints/hubs. Giữ nguyên external human/NEI policy; không có app-managed workflow.

## PowerShell — chuẩn bị, chưa gọi API

Đường dẫn source dưới đây lấy từ context thật hiện tại. Các run IDs mới được đọc từ output của prepare, không điền UUID giả. Chạy từ terminal mới nếu execution policy hiện tại không cho phép scripts.

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$Python = Join-Path $PWD '.tools\python312\python.exe'
$Driver = Join-Path $PWD 'scripts\discovery-experiment.py'
$SourceContext = Join-Path $PWD 'data\replays\quality21-20261026\run-context.json'
$Source = Get-Content -LiteralPath $SourceContext -Raw -Encoding UTF8 | ConvertFrom-Json
$Offline = Join-Path $PWD 'reports\verification\discovery-release-20261027'
$SourceReview = Join-Path $PWD 'configs\reviews\integrity-23640705-20261027.json'
$Stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff')
$ExperimentRoot = Join-Path $PWD ("data\experiments\discovery-$Stamp")
if (Test-Path -LiteralPath $ExperimentRoot) { throw 'Use a new experiment directory' }
foreach ($Path in @($Python,$Driver,$SourceContext,(Join-Path $Offline 'inventory.json'),$SourceReview)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing input: $Path" }
}
# Reuse cache with private copies, not a cold network rebuild. Check disk first.
$RawBytes = (Get-ChildItem -LiteralPath $Source.raw_cache -Recurse -File | Measure-Object Length -Sum).Sum
$DbBytes = (Get-Item -LiteralPath $Source.database).Length
if (Test-Path -LiteralPath ($Source.database + '-wal')) { $DbBytes += (Get-Item -LiteralPath ($Source.database + '-wal')).Length }
$RequiredBytes = 3 * ($RawBytes + $DbBytes) + 2GB
$Volume = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($PWD.Path))
if ($Volume.AvailableFreeSpace -lt $RequiredBytes) { throw 'Insufficient disk for three isolated database/cache baselines' }
$Contexts = @{}
foreach ($Variant in @('legacy','seed-only','hybrid')) {
    Write-Host "Offline validation and preparation: $Variant (may take several minutes)"
    $Json = & $Python -B $Driver prepare --context $SourceContext `
        --inventory (Join-Path $Offline 'inventory.json') `
        --plan (Join-Path $Offline ("plans\$Variant.json")) `
        --output-dir (Join-Path $ExperimentRoot $Variant) `
        --source-review $SourceReview --private-cache
    $PrepareExit = $LASTEXITCODE
    if ($PrepareExit -ne 0) { throw "Preparation failed: $Variant, exit $PrepareExit; do not run this arm" }
    $Prepared = ($Json -join "`n") | ConvertFrom-Json
    $Contexts[$Variant] = $Prepared.context
    Write-Host "$Variant run: $($Prepared.run_id); context: $($Prepared.context)"
}
$MapPath = Join-Path $ExperimentRoot 'contexts.json'
[System.IO.File]::WriteAllText($MapPath,($Contexts | ConvertTo-Json),[System.Text.UTF8Encoding]::new($false))
Write-Host "Save this map path: $MapPath"
```

Prepare không activate quarantine ở run mới. Draft file có `reviewed_by` rỗng và bị validator từ chối. Chỉ sau khi **bạn review scope/evidence**, có thể tạo approval mới ở tầng vận hành. Đó là yêu cầu existing `ReviewedIntegrityQuarantine.from_file`: run_id/config_hash phải khớp run mới; không sửa approval cũ.

Preflight ngày 27/10/2026 xác định page421635 (Q10761835 / revision23640705) ở ordinal275 của Legacy, **ordinal100 của Seed-only** và ordinal142 của Hybrid trong frozen release inventory. Vì vậy Seed-only Pilot100 đã chạm known integrity blocker; không chạy treatment này bằng approval run cũ, không đổi thứ tự hoặc thay candidate để tránh lỗi. Khi yêu cầu chạy cả ba arms có stop-on-blocker, dừng trước source work và bàn giao từng draft theo run/config mới cho người dùng review. Không tự ký `reviewed_by`, kể cả approval cũ đã được người dùng cấp cho cùng revision ở run khác.

Approval refusal-only cũng **không** làm revision hợp lệ hoặc làm `data_complete=true`. Seed-only100 vẫn bị completeness gate chặn lên1.000 nếu candidate100 còn quarantined; missing hint trong prefix cũng có thể giữ gate incomplete. Không bypass `PilotControls` sau khi đã có approval. Cần source recovery xác minh được hoặc quyết định thiết kế/experiment khác được review riêng. Khi sau này chạy các arms tuần tự, source maxlag/circuit ở một arm phải làm dừng batch để xem deadline trước khi bắt đầu arm kế; DB/cache riêng không phải quyền bypass cooldown của cùng upstream host.

Namespace scope là input bắt buộc trong cả bước tạo fingerprint và `process_prepared` validation của qid-5 discovery worker. Missing/changed scope phải bị từ chối; exact scope phải được chấp nhận. Regression coverage phải chạy qua Builder → process_dependencies → process_prepared với **primary-first-1**, không chỉ direct provider.process hoặc fixture bounded-bfs-1.

```powershell
# Chỉ thực hiện sau khi đã kiểm tra từng draft, exact revision/hashes và run/config.
$ApprovedReviews = @{}
foreach ($Variant in @('legacy','seed-only','hybrid')) {
    $C = Get-Content -LiteralPath $Contexts[$Variant] -Raw -Encoding UTF8 | ConvertFrom-Json
    $Draft = Get-Content -LiteralPath $C.quarantine_review_draft -Raw -Encoding UTF8 | ConvertFrom-Json
    $Draft | ConvertTo-Json -Depth 100
    $Decision = Read-Host "Approve refusal-only review for $Variant / $($C.run_id)? Type APPROVE or stop"
    if ($Decision -cne 'APPROVE') { throw 'Stopped before activation or API work' }
    $Draft.review.reviewed_by = "manual-$env:USERNAME"
    $Draft.review.reason = 'Reviewed exact historical mismatch evidence; refuse this revision only; no trusted evidence or acceptance'
    $ApprovedPath = Join-Path (Split-Path -Parent $Contexts[$Variant]) 'quarantine-review-approved.json'
    if (Test-Path -LiteralPath $ApprovedPath) { throw 'Approval already exists; inspect it instead of overwriting' }
    [System.IO.File]::WriteAllText($ApprovedPath,($Draft | ConvertTo-Json -Depth 100),[System.Text.UTF8Encoding]::new($false))
    $ApprovedReviews[$Variant] = $ApprovedPath
}
```

## Pilot100 — chỉ khi bạn chủ động chạy sau review

Chạy tuần tự, không parallel arms. Các commands dưới đây bắt đầu real source work; task implementation không chạy chúng. Same100 unique-candidate prefix per treatment, max3.000 physical attempts, maxlag5, concurrency1/host, scheduler wait60s/retry0; HTTP attempts và Retry-After guards hiện tại giữ nguyên. Cap không phải cam kết sẽ gọi3.000 requests hoặc finish.

```powershell
$Variant = 'legacy' # Sau khi review kết quả, lặp riêng cho 'seed-only' rồi 'hybrid'.
& .\scripts\Invoke-DiscoveryPilot.ps1 -ContextFile $Contexts[$Variant] -Size 100 `
    -MaxRequests 3000 -MaxWaitSeconds 60 -RetryRounds 0 `
    -ReviewedQuarantine $ApprovedReviews[$Variant]
$PilotExit = $LASTEXITCODE
Write-Host "Pilot100 native exit: $PilotExit"
$C = Get-Content -LiteralPath $Contexts[$Variant] -Raw -Encoding UTF8 | ConvertFrom-Json
& $C.python -B $Driver status --context $Contexts[$Variant]
if ($LASTEXITCODE -ne 0) { throw 'Status failed' }
```

Wrapper lưu cả stdout/stderr bằng `Tee-Object`, unique `.log` + `.exit-code.txt` trong `$C.reports\$C.run_id\100\`; khóa cả run để hai wrapper không chạy đồng thời.0 không thay human acceptance;5 incomplete,3 integrity/budget/input stop cần điều tra; không retry loop. Console có thể hiển thị NativeCommandError formatting vì progress trên stderr: đọc native exit file/receipt, không suy từ shell formatting.

## Pilot1.000, status, resume và reports

Chỉ sau khi bạn review Pilot100 từng arm; CLI bắt buộc prior100 `data_complete=true`. Nếu incomplete/integrity stop, giữ nguyên checkpoint và điều tra; không bypass gate hoặc thay candidates.

```powershell
$Variant = 'legacy' # Chọn từng arm, không tự chuyển cohort hoặc Production.
& .\scripts\Invoke-DiscoveryPilot.ps1 -ContextFile $Contexts[$Variant] -Size 1000 `
    -MaxRequests 3000 -MaxWaitSeconds 60 -RetryRounds 0 `
    -ReviewedQuarantine $ApprovedReviews[$Variant]
$PilotExit = $LASTEXITCODE
Write-Host "Pilot1000 native exit: $PilotExit"
```

Resume dùng **đúng command của size bị gián đoạn**, context và review cũ, sau khi inspect status/deadlines; không prepare lại/run ID mới hoặc `pipeline retry` để reset TTL. Valid quarantined records restore không vào worker/tăng attempt; completed records được checksum/raw-validate. Hai negative pageprops observations thực tế trong run đưa sang `association_review_required`, không gọi vô hạn và không chứng minh QID vĩnh viễn vắng mặt. Không tự requeue integrity failure khác.

```powershell
$C = Get-Content -LiteralPath $Contexts[$Variant] -Raw -Encoding UTF8 | ConvertFrom-Json
& $C.python -B $Driver status --context $Contexts[$Variant]
if ($LASTEXITCODE -ne 0) { throw 'Status failed' }
$Size = 1000 # Hoặc100 nếu đang ở bước100.
$ReportDir = Join-Path $C.reports ($C.run_id + '\' + $Size)
$Stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff')
& $C.python -B $Driver report --context $Contexts[$Variant] --size $Size `
    --output (Join-Path $ReportDir ("export-$Stamp.json"))
if ($LASTEXITCODE -ne 0) { throw 'Report export failed' }
Get-ChildItem -LiteralPath $ReportDir | Select-Object Name,Length,LastWriteTime
# Trong terminal thứ hai: theo dõi log, Ctrl+C chỉ dừng tail.
$LatestLog = Get-ChildItem -LiteralPath $ReportDir -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $LatestLog.FullName -Tail 20 -Wait
```

Metrics/performance/template/classification/pair/dependency-manifest/fingerprint/subtype/work/quarantine audits và manifests nằm trong report dir; immutable `attempts/<attempt_id>/` giữ lịch sử. Run context chứa run ID/database/config/inventory/cohort paths. Performance ghi physical/logical requests, errors/retries/backoff và sampled RSS gồm primary exports; đo wall/RAM cùng scope, tách new/restored work.

```powershell
# Chạy riêng trong cửa sổ theo dõi nếu cần RAM hiện tại.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*discovery-experiment.py*' } |
    ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
    Select-Object Id,CPU,@{Name='RSS_MiB';Expression={[Math]::Round($_.WorkingSet64/1MB,2)}}

# Sau khi có reports cùng size cho cả3 arms; output tên mới.
$Comparison = Join-Path $ExperimentRoot ("comparison-1000-" + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.json')
& $Python -B $Driver compare --contexts $Contexts['legacy'] $Contexts['seed-only'] $Contexts['hybrid'] `
    --size 1000 --output $Comparison
if ($LASTEXITCODE -ne 0) { throw 'Comparison export failed' }
```

Nếu mở terminal mới, đọc lại contexts map đã lưu thay vì đoán run IDs:

```powershell
# Dán đúng contexts.json path đã được in ở bước prepare.
$MapPath = Read-Host 'Paste the saved contexts.json path'
$Map = Get-Content -LiteralPath $MapPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Contexts = @{}
foreach ($Property in $Map.PSObject.Properties) { $Contexts[$Property.Name] = [string]$Property.Value }
$ExperimentRoot = Split-Path -Parent $MapPath
$Driver = Join-Path 'C:\ViFC_MultiHop_Data' 'scripts\discovery-experiment.py'
$Python = (Get-Content -LiteralPath $Contexts['legacy'] -Raw -Encoding UTF8 | ConvertFrom-Json).python
$ApprovedReviews = @{}
foreach ($Variant in @('legacy','seed-only','hybrid')) {
    $ApprovedReviews[$Variant] = Join-Path (Split-Path -Parent $Contexts[$Variant]) 'quarantine-review-approved.json'
}
```

Không có command10.000/Production. Chỉ xem xét giai đoạn đó sau source/integrity disposition, reproducibility, yield/type/connectivity/diversity và external human necessary-hop evaluation.
