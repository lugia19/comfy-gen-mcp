# Comfy-Gen-MCP

This is just a vibecoded garbo thing I wanted to play around with. It's an MCP server / installable MCPB that wires up ComfyUI with Claude for image generation.

I didn't want to build a generic "do everything" ComfyUI controller — there are already projects for that. This one just generates images. Ships with built-in workflows for **Anima** (anime/illustration) and **Flux 2 Klein** (realistic/general-purpose), plus support for one custom workflow.

Meant to be pretty dumb and plug and play.

## Prerequisites

- [ComfyUI Desktop](https://www.comfy.org/download) installed and run at least once (so it creates its config)
- For Flux 2 Klein: the [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) custom node (you'll be guided through installing it, but if you want to pre-install it, you can do so via the Extensions button in ComfyUI)

## Installation

### Option 1: Claude Desktop Extension (MCPB) — Windows only

Best for local use with Claude Desktop.

> **Note:** Claude Desktop doesn't display tool-generated images well by default. You can fix this by installing Claude Desktop via my [modified launcher](https://github.com/lugia19/Claude-WebExtension-Launcher), which patches in the Claude QoL web extension.

1. Download the `.mcpb` file from the [releases page](https://github.com/lugia19/comfy-dxt/releases)
2. In Claude Desktop: Settings > Extensions > Advanced Settings > Install Extension
3. A setup window should appear — if it doesn't, restart Claude Desktop
4. Pick which models to download, configure artist styles, etc.
5. If you skip a model pack, it'll download automatically when you first try to use it

**Updating to a new version**: Go to your settings, extensions, and uninstall it. Then reinstall it.
If this doesn't work, go to advanced settings, click on "Open extensions folder", close the Claude Desktop client, and manually delete it. (May need a restart).

### Option 2: Standalone MCP Server (exe / .app) — Windows & macOS

Best for remote access via claude.ai, or for macOS users (since the MCPB can't display images properly on macOS without the QoL extension, which isn't available on Mac).

**Windows:**
1. Download the `.exe` from the [releases page](https://github.com/lugia19/comfy-dxt/releases)
2. Run it — a setup wizard will guide you through first-time configuration
3. Choose between a Cloudflare tunnel (easiest) or your own reverse proxy
4. The server window can be minimized to the system tray

**macOS:**
1. Download the `.zip` from the [releases page](https://github.com/lugia19/comfy-dxt/releases)
2. Extract and move `Comfy-Gen-MCP.app` to `/Applications`
3. Run `xattr -cr "/Applications/Comfy-Gen-MCP.app"` in Terminal (one-time, to bypass Gatekeeper)
4. Double-click the app — same setup wizard as Windows
5. If something doesn't work, post about it. I've done my best to get it working, but MacOS can be very uncooperative. So consider this a work in progress.

### Adding as a connector on claude.ai

When using the standalone server with a Cloudflare tunnel, a window will show your MCP URL. To connect it:

1. Go to [claude.ai](https://claude.ai), click on **Customize**
2. Click on **Connectors**
3. Click the **+** sign next to the search icon
4. Click **Add custom connector**
5. Give it a name and paste the URL
6. Optional: Remove any old versions of the connector

> **Note:** The tunnel URL changes every time you restart the server.

## Mobile / Remote Access

The standalone server is what you want for remote access. However, the **Claude mobile app does not display tool-generated images**. Use a browser instead — ideally Firefox (including Android) with [Claude QoL](https://github.com/nicekid1/Claude-Enhancement-Suite) installed.

## Configuration

**MCPB (Claude Desktop):** Settings > Extensions > Configure for comfyui-image-gen

**Standalone exe:** Settings are stored in `local_config.json` next to the executable. On first run the file is auto-created with all user-editable keys set to their defaults, so you can open it and edit in place. The first-run wizard handles model downloads.

Available settings (JSON keys in `local_config.json`):
- `anima_artists` — comma-separated list of @artist tags (browse styles at the [Anima Style Explorer](https://thetacursed.github.io/Anima-Style-Explorer/index.html))
- `comfyui_url` — default `http://127.0.0.1:8000`. **Change this if you use portable/standalone ComfyUI** or run on a non-default port — include the full URL with scheme and port (e.g. `http://127.0.0.1:8188`).
- `comfyui_exe` — only needed for auto-launch of ComfyUI Desktop in a non-default install path. Leave empty for portable installs you start yourself.
- `custom_workflow` — path to a ComfyUI workflow exported in API format (.json)
- `custom_workflow_prompt_node` — title of the node in your custom workflow that receives the prompt text (auto-detected if empty)

## Troubleshooting

- **If anything fails after installing GGUF:** restart Claude Desktop, ComfyUI, or your computer. ComfyUI sometimes leaves ghost processes behind.
- **"Cannot find ComfyUI's models directory":** open ComfyUI Desktop and complete its initial setup first. It needs to run at least once to create its config file.
- **Slow generation / timeouts:** If generation takes longer than ~55 seconds, the server will return a request token. Claude will automatically retry to fetch the result. On slower hardware (especially macOS with MPS), this is normal.
- **macOS "damaged app" error:** run `xattr -cr "/Applications/Comfy-Gen-MCP.app"` in Terminal.