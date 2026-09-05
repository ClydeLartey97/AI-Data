@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0measure_dell.ps1"
exit /b %ERRORLEVEL%
