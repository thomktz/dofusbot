@echo off
setlocal
cd /d "%~dp0"
echo Starting Dofusbot Windows setup...
echo.
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0setup-windows.ps1"
echo.
echo PowerShell exited. Press any key to close this window.
pause >nul
