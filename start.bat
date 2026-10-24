@echo off
rem Start (or restart) the console; args go to start.ps1, e.g. start.bat -Port 8701
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
