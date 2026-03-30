#!/bin/bash
cd "$(dirname "$0")"

# Create/reuse a build venv
if [ ! -d "build_venv" ]; then
    python3 -m venv build_venv
fi
source build_venv/bin/activate

pip install pyinstaller httpx mcp Pillow certifi websocket-client PyQt6
pyinstaller --onefile --name comfyui-image-gen-mcp \
    --add-data "comfyui-image-gen/model_packs:model_packs" \
    --add-data "comfyui-image-gen/server/tray_icon.png:server" \
    --icon "comfyui-image-gen/icon.ico" \
    --paths "comfyui-image-gen" \
    --hidden-import httpx \
    --hidden-import httpx._transports \
    --hidden-import httpx._transports.default \
    --hidden-import httpcore \
    --hidden-import mcp \
    --hidden-import mcp.server \
    --hidden-import mcp.server.fastmcp \
    --hidden-import mcp.server.fastmcp.server \
    --hidden-import mcp.server.lowlevel \
    --hidden-import mcp.server.stdio \
    --hidden-import mcp.types \
    --hidden-import PIL \
    --hidden-import PIL.Image \
    --hidden-import anyio \
    --hidden-import anyio._backends \
    --hidden-import anyio._backends._asyncio \
    --hidden-import pydantic \
    --hidden-import certifi \
    --hidden-import server \
    --hidden-import server.comfyui \
    --hidden-import server.config \
    --hidden-import server.downloader \
    --hidden-import server.model_pack \
    --hidden-import server.workflow \
    --hidden-import server.tunnel \
    --hidden-import server.setup_ui \
    --hidden-import server.comfy_job \
    --hidden-import server.ui \
    --hidden-import websocket \
    --hidden-import PyQt6 \
    --hidden-import PyQt6.QtCore \
    --hidden-import PyQt6.QtGui \
    --hidden-import PyQt6.QtWidgets \
    --exclude-module mcp.cli \
    comfyui-image-gen/server/main.py

# ── Package as .app bundle ──────────────────────────────────────
APP_NAME="Comfy-Gen-MCP"
APP_BUNDLE="dist/${APP_NAME}.app"
BINARY="dist/comfyui-image-gen-mcp"

echo ""
echo "Packaging as .app bundle..."

# Clean previous bundle
rm -rf "$APP_BUNDLE"

# Create structure
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# Move binary
cp "$BINARY" "$APP_BUNDLE/Contents/MacOS/comfyui-image-gen-mcp"
chmod +x "$APP_BUNDLE/Contents/MacOS/comfyui-image-gen-mcp"

# Generate Info.plist
cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>comfyui-image-gen-mcp</string>
    <key>CFBundleIdentifier</key>
    <string>com.lugia19.comfyui-image-gen</string>
    <key>CFBundleName</key>
    <string>Comfy-Gen-MCP</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
</dict>
</plist>
PLIST

# Ad-hoc sign
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null || echo "Warning: codesign failed (may need to sign manually)"

# Remove quarantine
xattr -cr "$APP_BUNDLE" 2>/dev/null

# Zip for distribution (preserves executable bits)
cd dist
zip -r "ComfyUI-Image-Gen-macOS.zip" "${APP_NAME}.app"
cd ..

echo ""
echo "Output:"
echo "  Binary: $BINARY"
echo "  App:    $APP_BUNDLE"
echo "  Zip:    dist/ComfyUI-Image-Gen-macOS.zip"
