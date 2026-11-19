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
    $WingetOutput = @(& winget install --id $Id -e --source winget --accept-package-agreements --accept-source-agreements 2>&1)
    $WingetOutput | ForEach-Object { Write-Host $_ }
    $WingetText = $WingetOutput -join "`n"
    if ($LASTEXITCODE -ne 0 -and $WingetText -notmatch 'No newer package versions are available') {
        throw "winget could not install $Id (exit $LASTEXITCODE)."
    }
}

function Find-CommandPath([string[]]$Names) {
    foreach ($Name in $Names) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($null -ne $Command) { return $Command.Source }
    }
    return $null
}

try {
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
    Write-Setup "Dofusbot Windows setup: $Root" Cyan

    $PyLauncher = Find-CommandPath @('py.exe', 'py')
    $PythonFallback = Find-CommandPath @('python.exe', 'python')
    if ($null -eq $PyLauncher -and $null -eq $PythonFallback) {
        Install-WingetPackage 'Python.Python.3.12'
    } else {
        Write-Setup 'Python already installed; keeping it.' Green
    }
    $PyLauncher = Find-CommandPath @('py.exe', 'py')
    $PythonFallback = Find-CommandPath @('python.exe', 'python')
    if ($null -eq $PyLauncher -and $null -eq $PythonFallback) {
        throw 'Python is installed according to winget, but is not available yet. Restart Windows once, then run START-SETUP.cmd again.'
    }

    $Npm = Find-CommandPath @('npm.cmd', 'npm')
    if ($null -eq $Npm) {
        Install-WingetPackage 'OpenJS.NodeJS.LTS'
    } else {
        Write-Setup 'Node.js already installed; keeping it.' Green
    }
    $Npm = Find-CommandPath @('npm.cmd', 'npm')
    if ($null -eq $Npm) {
        throw 'npm is installed according to winget, but is not available yet. Restart Windows once, then run START-SETUP.cmd again.'
    }

    Write-Setup 'Creating Python environment...' Cyan
    if ($null -ne $PyLauncher) {
        & $PyLauncher -3.12 -m venv .venv
    } else {
        & $PythonFallback -m venv .venv
    }
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
