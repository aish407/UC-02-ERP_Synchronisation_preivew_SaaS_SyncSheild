param(
  [switch]$SkipHealthCheck
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  Write-Host '== Python syntax =='
  python -m py_compile server.py
  if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
  Write-Host '== Frontend syntax =='
  node --check public/app.js
  if ($LASTEXITCODE -ne 0) { throw 'JavaScript syntax check failed.' }
  Write-Host '== Acceptance tests =='
  python -m unittest discover -s tests -v
  if ($LASTEXITCODE -ne 0) { throw 'Acceptance tests failed.' }
  if (-not $SkipHealthCheck) {
    Write-Host '== Local health endpoint =='
    $response = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 5
    if ($response.status -ne 'ok') { throw 'Health endpoint returned an unexpected response.' }
    $response | ConvertTo-Json -Compress
  }
  Write-Host 'All local checks passed.'
} finally { Pop-Location }
