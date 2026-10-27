param(
    [Parameter(Mandatory=$true)][string]$ContextFile,
    [Parameter(Mandatory=$true)][ValidateSet(100,1000)][int]$Size,
    [ValidateRange(1,10000)][int]$MaxRequests=3000,
    [ValidateRange(0,3600)][int]$MaxWaitSeconds=60,
    [ValidateRange(0,5)][int]$RetryRounds=0,
    [string]$ReviewedQuarantine
)
$ErrorActionPreference='Stop'
$ContextPath=(Resolve-Path -LiteralPath $ContextFile).Path
$Context=Get-Content -LiteralPath $ContextPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($Name in @('python','reports','run_id','variant')) {
    if (-not $Context.$Name) { throw "Missing context field: $Name" }
}
if (-not (Test-Path -LiteralPath $Context.python -PathType Leaf)) { throw 'Context Python environment is unavailable' }
$Root=Split-Path -Parent $PSScriptRoot
$RunFolder=Join-Path $Context.reports $Context.run_id
$Folder=Join-Path $RunFolder ([string]$Size)
New-Item -ItemType Directory -Path $Folder -Force | Out-Null
# A single lock covers both stage sizes; no concurrent invocations of this wrapper.
$Lock=[System.IO.File]::Open((Join-Path $RunFolder 'discovery-pilot.lock'),[System.IO.FileMode]::OpenOrCreate,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None)
try {
    $Stamp=[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff')
    $Log=Join-Path $Folder ("discovery-$($Context.variant)-$Size-utc-$Stamp.log")
    $ExitFile=[System.IO.Path]::ChangeExtension($Log,'exit-code.txt')
    if ((Test-Path -LiteralPath $Log) -or (Test-Path -LiteralPath $ExitFile)) { throw 'Log or exit file already exists' }
    $NativeArgs=@('-B',(Join-Path $Root 'scripts\discovery-experiment.py'),'run','--context',$ContextPath,'--size',[string]$Size,
        '--max-requests',[string]$MaxRequests,'--max-wait-seconds',[string]$MaxWaitSeconds,'--retry-rounds',[string]$RetryRounds)
    if ($ReviewedQuarantine) { $NativeArgs+=@('--reviewed-quarantine',(Resolve-Path -LiteralPath $ReviewedQuarantine).Path) }
    $ErrorActionPreference='Continue'
    $PSNativeCommandUseErrorActionPreference=$false
    & $Context.python @NativeArgs 2>&1 | Tee-Object -FilePath $Log
    $NativeExit=$LASTEXITCODE
    [System.IO.File]::WriteAllText($ExitFile,[string]$NativeExit)
    Write-Host "Native exit: $NativeExit; log: $Log; exit file: $ExitFile"
} finally {
    $Lock.Dispose()
}
exit $NativeExit
