<#
Starts the Dofus executable installed by Ankama Launcher.

This is deliberately a plain launch test: no Frida, proxy, bot backend, or
account automation. It only answers whether Dofus can open under Windows ARM
in this VM.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$release = Join-Path $env:APPDATA 'zaap\repositories\production\dofus\dofus3\release.json'
if (-not (Test-Path -LiteralPath $release)) {
    throw "Dofus is not installed yet. The Launcher has not written: $release"
}

$data = Get-Content -Raw -LiteralPath $release | ConvertFrom-Json
$exe = Join-Path ([string]$data.location) 'Dofus.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Dofus.exe is missing at: $exe"
}

Write-Host "Starting: $exe" -ForegroundColor Cyan
Start-Process -FilePath $exe
