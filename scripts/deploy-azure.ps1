param(
  [Parameter(Mandatory=$true)][string]$ResourceGroupName,
  [Parameter(Mandatory=$true)][string]$AppServicePlanId,
  [Parameter(Mandatory=$true)][string]$Location,
  [Parameter(Mandatory=$true)][string]$WebAppName,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tf = Join-Path $root 'infra/terraform'
if ($WebAppName -notmatch '^[a-zA-Z0-9-]{2,60}$') { throw 'WebAppName must contain only letters, numbers and hyphens.' }
Push-Location $tf
try {
  terraform init -input=false
  if ($LASTEXITCODE -ne 0) { throw 'terraform init failed.' }
  terraform validate
  if ($LASTEXITCODE -ne 0) { throw 'terraform validate failed.' }
  terraform plan -input=false -out=syncshield.tfplan `
    -var="resource_group_name=$ResourceGroupName" `
    -var="app_service_plan_id=$AppServicePlanId" `
    -var="location=$Location" `
    -var="web_app_name=$WebAppName"
  if ($LASTEXITCODE -ne 0) { throw 'terraform plan failed.' }
  if (-not $Apply) { Write-Host 'Plan created only. Review the plan, then rerun with -Apply to create the web app.'; return }
  terraform apply -input=false syncshield.tfplan
  if ($LASTEXITCODE -ne 0) { throw 'terraform apply failed.' }
  Pop-Location
  Push-Location $root
  try {
    $package = Join-Path $env:TEMP 'syncshield-deploy.zip'
    if (Test-Path $package) { Remove-Item -LiteralPath $package -Force }
    Compress-Archive -Path server.py,public,schema.sql,migrations -DestinationPath $package -Force
    az webapp deploy --resource-group $ResourceGroupName --name $WebAppName --src-path $package --type zip
    if ($LASTEXITCODE -ne 0) { throw 'Zip deployment failed.' }
    Write-Host "App URL: https://$WebAppName.azurewebsites.net"
  } finally { Pop-Location }
} finally { if ((Get-Location).Path -eq $tf) { Pop-Location } }
