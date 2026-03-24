@echo off
cd /d "%~dp0"
comfyui-image-gen\venv\Scripts\pip install pyinstaller httpx mcp Pillow
comfyui-image-gen\venv\Scripts\pyinstaller --onefile --name comfyui-image-gen-mcp ^
    --add-data "comfyui-image-gen\model_packs;model_packs" ^
    --paths "comfyui-image-gen" ^
    --hidden-import httpx ^
    --hidden-import httpx._transports ^
    --hidden-import httpx._transports.default ^
    --hidden-import httpcore ^
    --hidden-import mcp ^
    --hidden-import mcp.server ^
    --hidden-import mcp.server.fastmcp ^
    --hidden-import mcp.server.fastmcp.server ^
    --hidden-import mcp.server.lowlevel ^
    --hidden-import mcp.server.stdio ^
    --hidden-import mcp.types ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import anyio ^
    --hidden-import anyio._backends ^
    --hidden-import anyio._backends._asyncio ^
    --hidden-import pydantic ^
    --hidden-import server ^
    --hidden-import server.comfyui ^
    --hidden-import server.config ^
    --hidden-import server.downloader ^
    --hidden-import server.model_pack ^
    --hidden-import server.workflow ^
    --hidden-import server.tunnel ^
    --hidden-import server.setup_ui ^
    --exclude-module mcp.cli ^
    comfyui-image-gen\server\main.py
echo.
echo Output: dist\comfyui-image-gen-mcp.exe
pause
