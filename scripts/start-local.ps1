$ErrorActionPreference = 'Stop'
$adminName = Read-Host 'Platform administrator name'
$adminEmail = Read-Host 'Platform administrator email'
$adminOrganization = Read-Host 'Organization name'
$jobTitles = @('Developer', 'Software Developer', 'Tester / QA', 'Designer', 'Code Reviewer', 'System Architect', 'Cloud/DevOps Engineer', 'Security Engineer', 'Data/Integration Engineer', 'Platform Administrator', 'Product Manager', 'Data Analyst', 'DevOps Engineer', 'Project Manager', 'Other')
Write-Host ('Professional roles: ' + ($jobTitles -join ', '))
$adminJobTitle = Read-Host 'Professional role (enter one listed above)'
$securePassword = Read-Host 'Platform administrator password (12+ characters)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
  $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  if ($adminEmail -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw 'Enter a valid email address.' }
  if ($adminPassword.Length -lt 12) { throw 'Admin password must be at least 12 characters.' }
  if ([string]::IsNullOrWhiteSpace($adminName) -or [string]::IsNullOrWhiteSpace($adminOrganization) -or [string]::IsNullOrWhiteSpace($adminJobTitle)) { throw 'Name, organization and professional role are required.' }
  if ($jobTitles -notcontains $adminJobTitle) { throw 'Choose a professional role exactly as shown in the list.' }
  $env:SYNCSHIELD_ADMIN_NAME = $adminName
  $env:SYNCSHIELD_ADMIN_EMAIL = $adminEmail
  $env:SYNCSHIELD_ADMIN_ORGANIZATION = $adminOrganization
  $env:SYNCSHIELD_ADMIN_JOB_TITLE = $adminJobTitle
  $env:SYNCSHIELD_ADMIN_PASSWORD = $adminPassword
  # Simulated checkout is restricted by the server to loopback clients.
  $env:SYNCSHIELD_DEMO_PAYMENTS = '1'
  python (Join-Path (Split-Path -Parent $PSScriptRoot) 'server.py')
} finally {
  Remove-Item Env:SYNCSHIELD_ADMIN_EMAIL -ErrorAction SilentlyContinue
  Remove-Item Env:SYNCSHIELD_ADMIN_NAME -ErrorAction SilentlyContinue
  Remove-Item Env:SYNCSHIELD_ADMIN_ORGANIZATION -ErrorAction SilentlyContinue
  Remove-Item Env:SYNCSHIELD_ADMIN_JOB_TITLE -ErrorAction SilentlyContinue
  Remove-Item Env:SYNCSHIELD_ADMIN_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:SYNCSHIELD_DEMO_PAYMENTS -ErrorAction SilentlyContinue
  if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  $adminPassword = $null
  $securePassword.Dispose()
}
