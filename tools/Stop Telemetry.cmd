@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_telemetry.ps1"
pause

