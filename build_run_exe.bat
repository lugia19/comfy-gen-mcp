@echo off
cd /d "%~dp0"
comfyui-image-gen\venv\Scripts\pip install pyinstaller httpx mcp Pillow websocket-client PyQt6
comfyui-image-gen\venv\Scripts\pyinstaller --onefile --name comfyui-image-gen-mcp ^
    --add-data "comfyui-image-gen\model_packs;model_packs" ^
    --add-data "comfyui-image-gen\server\tray_icon.png;server" ^
    --icon "comfyui-image-gen\icon.ico" ^
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
    --hidden-import server.comfy_job ^
    --hidden-import server.ui ^
    --hidden-import websocket ^
    --hidden-import PyQt6 ^
    --hidden-import PyQt6.QtCore ^
    --hidden-import PyQt6.QtGui ^
    --hidden-import PyQt6.QtWidgets ^
    --exclude-module mcp.cli ^
    comfyui-image-gen\server\main.py
echo.
echo.
echo Output: dist\comfyui-image-gen-mcp.exe
echo.
echo Launching...
start "" "dist\comfyui-image-gen-mcp.exe"
