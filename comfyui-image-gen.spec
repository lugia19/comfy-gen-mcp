# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['comfyui-image-gen\\server\\main.py'],
    pathex=['comfyui-image-gen'],
    binaries=[],
    datas=[('comfyui-image-gen\\model_packs', 'model_packs')],
    hiddenimports=['httpx', 'httpx._transports', 'httpx._transports.default', 'httpcore', 'mcp', 'mcp.server', 'mcp.server.fastmcp', 'mcp.server.fastmcp.server', 'mcp.server.lowlevel', 'mcp.server.stdio', 'mcp.types', 'PIL', 'PIL.Image', 'anyio', 'anyio._backends', 'anyio._backends._asyncio', 'pydantic', 'server', 'server.comfyui', 'server.config', 'server.downloader', 'server.model_pack', 'server.workflow', 'server.tunnel', 'server.setup_ui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mcp.cli'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='comfyui-image-gen',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
