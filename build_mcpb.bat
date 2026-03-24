@echo off
cd /d "%~dp0"
if not exist dist mkdir dist
npx @anthropic-ai/mcpb pack comfyui-image-gen dist\comfyui-image-gen.mcpb
pause
