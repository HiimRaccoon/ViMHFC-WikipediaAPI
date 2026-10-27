[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ContextFile,
    [ValidateRange(0, 3600)][int]$MaxWaitSeconds = 60,
    [ValidateRange(0, 5)][int]$RetryRounds = 2,
    [switch]$DeferredOnly
)

$ErrorActionPreference = 'Stop'
$context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($name in @('python', 'database', 'run_id', 'snapshot', 'config', 'config_hash', 'report_root')) {
    if (!$context.$name) { throw "Missing context field: $name" }
}
if (!(Test-Path -LiteralPath $context.python -PathType Leaf)) { throw 'Context Python environment is unavailable.' }
if (!(Test-Path -LiteralPath $context.database -PathType Leaf)) { throw 'Context database is unavailable.' }
$repo = Split-Path -Parent $PSScriptRoot
$folder = Join-Path ([System.IO.Path]::GetFullPath($context.report_root)) '1000'
$lease = $null
$nativeCode = 1
$exitPath = $null
Push-Location -LiteralPath $repo
try {
    # Read-only compatibility probe. It never instantiates PipelineStore/client.
    $check = @'
import json, sqlite3, sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'backend'/'src'))
from vimfc.config import load_config
from vimfc.domain import digest
c=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
db=sqlite3.connect(Path(c['database']).resolve().as_uri()+'?mode=ro',uri=True)
row=db.execute('SELECT snapshot,config_hash,config_json,production_report FROM pipeline_runs WHERE pipeline_run_id=?',(c['run_id'],)).fetchone()
if row is None: raise SystemExit('Unknown run; refusing to create one implicitly')
cfg=json.loads(row[2])
if row[0]!=c['snapshot'] or row[1]!=c['config_hash'] or digest(cfg)!=row[1]: raise SystemExit('Context/run identity mismatch')
if digest(load_config(c['config']))!=row[1]: raise SystemExit('Configuration changed; preserve this run and review compatibility')
if cfg['classification']['version']!='qid-5' or cfg['extraction']['version']!='lead-3': raise SystemExit('This resume script requires qid-5/lead-3')
if row[3]: raise SystemExit('Production run is not a bounded pilot')
n=db.execute('SELECT count(*) FROM candidates WHERE run_id=?',(c['run_id'],)).fetchone()[0]
if n!=1000: raise SystemExit('Expected exactly 1000 archived candidates; no discovery is authorized by this script')
prior=db.execute('SELECT report_json FROM pilot_reports WHERE run_id=? AND size=100',(c['run_id'],)).fetchone()
prior=json.loads(prior[0]) if prior else {}
if not prior.get('completed') or not prior.get('data_complete',not prior.get('source_failure_count',0)): raise SystemExit('Compatible complete Pilot 100 prerequisite is missing')
summary={'run_id':c['run_id'],'snapshot':row[0],'config_hash':row[1],'candidates':n,'compatibility':'PASS'}
if sys.argv[2].lower()=='true':
    q=list(db.execute('SELECT status,next_retry_at FROM candidate_work WHERE run_id=?',(c['run_id'],)))
    if len(q)!=n or any(status not in ('processed','deferred','source_failed') for status,_ in q): raise SystemExit('Deferred-only requires a complete valid queue checkpoint')
    due=sum(status=='deferred' and deadline is not None and datetime.fromisoformat(deadline.replace('Z','+00:00'))<=datetime.now(timezone.utc) for status,deadline in q)
    summary.update(deferred=sum(status=='deferred' for status,_ in q),due_deferred=due,deferred_only=True)
print(json.dumps(summary))
db.close()
'@
    $global:LASTEXITCODE = $null
    $preflight = $check | & $context.python -B - $ContextFile ([string]$DeferredOnly.IsPresent)
    $checkCode = $global:LASTEXITCODE
    if ($null -eq $checkCode -or $checkCode -ne 0) { throw "Read-only resume compatibility check failed: $checkCode" }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $lease = [System.IO.File]::Open((Join-Path $folder 'pilot1000.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'
    $logPath = Join-Path $folder ("pilot1000-resume-$stamp.log")
    $exitPath = [System.IO.Path]::ChangeExtension($logPath, '.exit-code.txt')
    if (Test-Path -LiteralPath $logPath) { throw 'Log collision; refusing overwrite.' }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $preflight | Tee-Object -FilePath $logPath -ErrorAction Stop
    "Scheduler host-aware-1; retry rounds: $RetryRounds; scheduler wait budget: $MaxWaitSeconds seconds" |
        Tee-Object -FilePath $logPath -Append -ErrorAction Stop
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) { $PSNativeCommandUseErrorActionPreference = $false }
    $global:LASTEXITCODE = $null
    $scopeArgs = @()
    if ($DeferredOnly) { $scopeArgs += '--deferred-only' }
    & $context.python -u (Join-Path $PSScriptRoot 'vimfc-local.py') pipeline pilot --database $context.database --run $context.run_id --candidates 1000 --progress --max-wait-seconds $MaxWaitSeconds --retry-rounds $RetryRounds @scopeArgs 2>&1 |
        Tee-Object -FilePath $logPath -Append -ErrorAction Stop
    $nativeCode = $global:LASTEXITCODE
    if ($null -eq $nativeCode) { $nativeCode = 1 }
    $ErrorActionPreference = $savedPreference
}
finally {
    if ($null -ne $exitPath) {
        [System.IO.File]::WriteAllText($exitPath, [string]$nativeCode + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
    }
    if ($null -ne $lease) { $lease.Dispose() }
    Pop-Location
}
Write-Host "Exit code: $nativeCode; saved: $exitPath"
Write-Host 'Exit 5: data incomplete; resume later. Exit 0: processing complete, still requires report review. No next pilot starts automatically.'
exit $nativeCode
