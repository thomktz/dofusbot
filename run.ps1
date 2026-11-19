<#
Starts the Windows dashboard backend and Vite development server.

  .\run.ps1 -Mock
  .\run.ps1 -BrainPort 8770

The Windows VM currently uses this for the dashboard / protocol environment.
It intentionally does not launch Dofus until the Windows live bridge has been
configured with the installed client path.
#>
[CmdletBinding()]
param(
    [switch]$Mock,
    [int]$BrainPort = 8765,
    [int]$WebPort = 5173,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw "Missing .venv. Follow the Windows setup commands in README.md first."
}
if (-not (Test-Path (Join-Path $Root 'webapp\node_modules'))) {
    throw "Missing webapp dependencies. Run: cd webapp; npm ci"
}

function Stop-Listener([int]$Port) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Stop-Listener $BrainPort
Stop-Listener $WebPort

$env:VITE_API_PORT = "$BrainPort"
$backendArgs = @('-m', 'dofus_brain.server', '--port', "$BrainPort")
if ($Mock) { $backendArgs += '--mock' }
if ($ServerArgs) { $backendArgs += $ServerArgs }

$backend = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $Root -PassThru -NoNewWindow
$web = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev', '--', '--port', "$WebPort") -WorkingDirectory (Join-Path $Root 'webapp') -PassThru -NoNewWindow

Write-Host "backend  ws://127.0.0.1:$BrainPort/ws"
Write-Host "webapp   http://localhost:$WebPort"
Write-Host 'Press Ctrl+C to stop both.'

try {
    Wait-Process -Id $backend.Id
}
finally {
    foreach ($process in @($backend, $web)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
