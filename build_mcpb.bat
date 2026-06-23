@echo off
cd /d "%~dp0"

REM Double-clickable entry point. All build logic lives in build_mcpb.ps1, which drives
REM pygo-bootstrap's build-all.ps1, stages the launcher into comfyui-image-gen\bootstrap\,
REM and packs the mcpb. Pass -WindowsOnly to skip the macOS .app while iterating.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_mcpb.ps1" %*
if errorlevel 1 ( echo build_mcpb.ps1 failed & pause & exit /b 1 )
pause
