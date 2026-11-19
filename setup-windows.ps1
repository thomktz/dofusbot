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
$LogPath = Join-Path $Root 'setup-windows.log'

function Write-Setup([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray) {
    Write-Host $Message -ForegroundColor $Color
    Add-Content -LiteralPath $LogPath -Value ("[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

function Install-WingetPackage([string]$Id) {
    Write-Setup "Installing $Id through winget..." Cyan
    & winget install --id $Id -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $Id (exit $LASTEXITCODE)."
    }
}

try {
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
    Write-Setup "Dofusbot Windows setup: $Root" Cyan

    $PyLauncher = 'C:\Windows\py.exe'
    if (-not (Test-Path $PyLauncher)) {
        Install-WingetPackage 'Python.Python.3.12'
    } else {
        Write-Setup 'Python launcher already installed; keeping it.' Green
    }
    if (-not (Test-Path $PyLauncher)) {
        throw "Python launcher is still missing at $PyLauncher. Restart Windows once, then run this script again."
    }

    $Npm = 'C:\Program Files\nodejs\npm.cmd'
    if (-not (Test-Path $Npm)) {
        Install-WingetPackage 'OpenJS.NodeJS.LTS'
    } else {
        Write-Setup 'Node.js already installed; keeping it.' Green
    }
    if (-not (Test-Path $Npm)) {
        throw "npm is still missing at $Npm. Restart Windows once, then run this script again."
    }

    Write-Setup 'Creating Python environment...' Cyan
    & $PyLauncher -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip.' }
    & $Python -m pip install -e '.\brain[dev,protocol]'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }

    Write-Setup 'Installing web dependencies...' Cyan
    Push-Location (Join-Path $Root 'webapp')
    & $Npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Could not install web dependencies.' }
    Pop-Location

    Write-Setup 'Windows VM setup complete.' Green
    $start = Read-Host 'Start the safe mock dashboard now? [Y/n]'
    if ($start -notmatch '^(n|no)$') {
        & (Join-Path $Root 'run.ps1') -Mock
    }
}
catch {
    Write-Setup "SETUP FAILED: $($_.Exception.Message)" Red
    Write-Setup "The error was saved to: $LogPath" Yellow
}
finally {
    while ((Get-Location).Path -ne $Root -and (Get-Location).Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        Pop-Location
    }
    Write-Host ''
    Read-Host 'Press Enter to close this window'
}
