[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ContextFile)

$ErrorActionPreference = 'Stop'
$context = Get-Content -LiteralPath $ContextFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($context.candidates -ne 100) { throw 'This script accepts exactly 100 candidates.' }
$repo = Split-Path -Parent $PSScriptRoot
$folder = [System.IO.Path]::GetFullPath($context.report_dir)
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$logPath = Join-Path $folder ('pilot100-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '.log')
$exitPath = [System.IO.Path]::ChangeExtension($logPath, '.exit-code.txt')
$lockPath = Join-Path $folder 'pilot100.lock'
$lease = $null
$nativeCode = 1
Push-Location -LiteralPath $repo
try {
    $lease = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $savedPreference = $ErrorActionPreference
    # Windows PowerShell 5 turns native stderr into ErrorRecord objects. Continue
    # keeps those records in Tee-Object; a native nonzero exit is captured below.
    $ErrorActionPreference = 'Continue'
    if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
        $PSNativeCommandUseErrorActionPreference = $false
    }
    "Run: $($context.run_id); candidates: 100; log: $logPath" | Tee-Object -FilePath $logPath -ErrorAction Stop
    $LASTEXITCODE = $null
    & $context.python -u -m vimfc pipeline pilot --database $context.database --run $context.run_id --candidates 100 --progress 2>&1 |
        Tee-Object -FilePath $logPath -Append -ErrorAction Stop
    $nativeCode = $LASTEXITCODE
    if ($null -eq $nativeCode) { $nativeCode = 1 }
    $ErrorActionPreference = $savedPreference
}
finally {
    # This also records an interruption/launch failure as exit 1 if execution
    # never reached the native exit-code assignment.
    [System.IO.File]::WriteAllText($exitPath, [string]$nativeCode + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
    if ($null -ne $lease) { $lease.Dispose() }
    Pop-Location
}
Write-Host "Exit code: $nativeCode; saved: $exitPath"
exit $nativeCode
