@echo off
cd /d "%~dp0comfyui-image-gen"
uv run --directory . server/main.py --http
pause
