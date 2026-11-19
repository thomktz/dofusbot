<#
One-click Windows VM bootstrap for dofusbot.

Run from File Explorer with “Run with PowerShell”, after cloning the
repository. It installs Python and Node through winget, builds a local venv,
installs backend and web dependencies, then optionally starts the safe mock
dashboard.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Install-WingetPackage([string]$Id) {
    Write-Host "Ensuring $Id is installed..."
    & winget install --id $Id -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $Id (exit $LASTEXITCODE)."
    }
}

Install-WingetPackage 'Python.Python.3.12'
Install-WingetPackage 'OpenJS.NodeJS.LTS'

$PyLauncher = 'C:\Windows\py.exe'
if (-not (Test-Path $PyLauncher)) {
    throw 'Python launcher was not found after installation. Close this window, reopen PowerShell, and run setup-windows.ps1 again.'
}

& $PyLauncher -3.12 -m venv .venv
$Python = Join-Path $Root '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip
& $Python -m pip install -e '.\brain[dev,protocol]'

$Npm = 'C:\Program Files\nodejs\npm.cmd'
if (-not (Test-Path $Npm)) {
    throw 'npm was not found after installation. Close this window, reopen PowerShell, and run setup-windows.ps1 again.'
}
Push-Location (Join-Path $Root 'webapp')
try {
    & $Npm ci
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Windows VM setup complete.' -ForegroundColor Green
$start = Read-Host 'Start the safe mock dashboard now? [Y/n]'
if ($start -notmatch '^(n|no)$') {
    & (Join-Path $Root 'run.ps1') -Mock
}
