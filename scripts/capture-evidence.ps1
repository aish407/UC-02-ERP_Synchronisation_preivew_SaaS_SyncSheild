param([switch]$SkipHealthCheck)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $root "evidence/local-verification-$stamp.txt"
Start-Transcript -Path $log -Force | Out-Null
try {
  if ($SkipHealthCheck) {
    & (Join-Path $PSScriptRoot 'verify-local.ps1') -SkipHealthCheck
  } else {
    & (Join-Path $PSScriptRoot 'verify-local.ps1')
  }
  Write-Host "Evidence saved to $log"
} finally { Stop-Transcript | Out-Null }
