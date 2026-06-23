@echo off
cd /d "%~dp0"

REM 1. Build the self-updating bootstrapper dist (Go exe + uv.exe + install.py + repo.json
REM    + icon) into dist\http_dist. This is the same dist the standalone HTTP build ships.
echo === Building bootstrapper dist (build_http_dist.ps1) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_http_dist.ps1"
if errorlevel 1 ( echo build_http_dist.ps1 failed & pause & exit /b 1 )

REM 2. Stage it inside the extension so the packed mcpb ships it at <bundle>\bootstrap\.
REM    The stdio shim copies these into ~\.comfy-gen-mcp\runtime and runs the exe there,
REM    which git-pulls + launches the real server (server.shim._spawn_server).
echo === Staging bootstrap into comfyui-image-gen\bootstrap ===
set "BOOT=comfyui-image-gen\bootstrap"
if exist "%BOOT%" rmdir /s /q "%BOOT%"
mkdir "%BOOT%"
for %%F in (comfyui-image-gen-mcp.exe uv.exe install.py repo.json icon.ico) do (
    copy /y "dist\http_dist\%%F" "%BOOT%\" >nul
    if errorlevel 1 ( echo Missing dist\http_dist\%%F & pause & exit /b 1 )
)

REM 3. Pack the mcpb.
echo === Packing mcpb ===
if not exist dist mkdir dist
npx @anthropic-ai/mcpb pack comfyui-image-gen dist\comfyui-image-gen.mcpb
pause
