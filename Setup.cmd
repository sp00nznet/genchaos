@echo off
rem General Chaos recomp quick start: checks prerequisites, generates from your ROM, builds.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1" %*
if errorlevel 1 (pause & exit /b 1)
pause
