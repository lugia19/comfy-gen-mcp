# Comfy-Gen-MCP

This is just a vibecoded garbo thing I wanted to play around with. It's an MCP server / installable MCPB that wires up ComfyUI with Claude for image generation.

I didn't want to build a generic "do everything" ComfyUI controller — there are already projects for that. This one just generates images. Ships with built-in workflows for **Anima** (anime/illustration), **Flux 2 Klein** and **Z-Image Turbo** (realistic/general-purpose), plus image editing and support for one custom workflow.

Meant to be pretty dumb and plug and play — **ComfyUI itself is installed for you** (via [comfy-cli](https://github.com/Comfy-Org/comfy-cli)) on first run, along with any custom nodes the models need. You don't need an existing ComfyUI install.

## Prerequisites

Basically nothing to set up by hand:

- **ComfyUI is auto-installed** on first run into `~/.comfy-gen-mcp/comfyui/`. Your GPU is auto-detected (NVIDIA / AMD / Apple Silicon / CPU fallback) and the matching build is fetched.
- **Required custom nodes** (e.g. [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) for the Flux/Z-Image packs) are installed automatically the first time you use a model that needs them.

What you *do* want:
- A reasonably capable GPU is strongly recommended (CPU works but is slow). Rough VRAM: Anima ~4 GB, Flux 2 Klein ~6 GB, Z-Image Turbo 8 GB+.
- Disk space — roughly **6–10 GB per model pack** you choose to download.

## Installation

### Option 1: Claude Desktop Extension (MCPB) — Windows only

Best for local use with Claude Desktop.

> **Note:** Claude Desktop doesn't display tool-generated images well by default. You can fix this by installing Claude Desktop via my [modified launcher](https://github.com/lugia19/Claude-WebExtension-Launcher), which patches in the Claude QoL web extension.

1. Download the `.mcpb` file from the [releases page](https://github.com/lugia19/comfy-dxt/releases)
2. In Claude Desktop: Settings > Extensions > Advanced Settings > Install Extension
3. A setup window should appear — if it doesn't (or nothing shows up after a couple minutes), restart Claude Desktop
4. Pick which models to download, configure artist styles, etc.
5. If you skip a model pack, it'll download automatically when you first try to use it

**Updating:** server-side fixes update themselves — the extension pulls the latest server code on launch, so most bug fixes reach you without reinstalling. For larger updates (new tools/models or a new shim), grab the latest `.mcpb`: go to Settings > Extensions, uninstall it, then reinstall.
If uninstall misbehaves, go to advanced settings, click "Open extensions folder", close Claude Desktop, and manually delete it (may need a restart).

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

## Models & tools

| Tool | Model pack(s) | Notes |
|------|---------------|-------|
| `generate_illustrated_image` | **Anima** | Anime/illustration; booru tags + natural language. ~6 GB, ~4 GB VRAM. Can't render text — use `edit_image` afterward to add it. |
| `generate_realistic_image` | **Flux 2 Klein** or **Z-Image Turbo** | Realistic/general-purpose. Pick one in Settings — **Z-Image Turbo is the default** (~9 GB, 8 GB+ VRAM); Flux 2 Klein is ~8 GB, ~6 GB VRAM. |
| `edit_image` | **Flux 2 Klein (Edit)** | img2img editing from a text prompt. Takes a local file path or public URL; optional second reference image. |
| `generate_custom_image` | *(your workflow)* | Only appears if you set a custom workflow path in Settings. |
| `fetch_result` | — | Retrieves a generation that's still running (when a tool returns a `request_token`). |

Packs you don't download up front are fetched automatically the first time you use the corresponding tool.

## Configuration

Everything is managed in the **Settings panel** — click **Settings** in the Comfy-Gen-MCP server window (look for the tray icon), or in Claude Desktop go to Settings > Extensions > Configure for comfyui-image-gen.

Settings include:
- **Model selection** — which pack backs `generate_realistic_image` (Flux 2 Klein vs Z-Image Turbo)
- **Anima artist styles** — @artist tags blended into Anima prompts (browse them at the [Anima Style Explorer](https://thetacursed.github.io/Anima-Style-Explorer/index.html))
- **Anima LoRAs** — add LoRAs with a strength and an optional *trigger word* (the LoRA only applies when that word appears in the prompt; leave it blank to always apply). Anima-only for now.
- **Anima sampling steps** — default 30; higher is slower but can improve quality
- **ComfyUI URL** — default `http://127.0.0.1:8188`. If that port is taken, the managed ComfyUI automatically falls back to a free port. Only change this if you want to point at your *own* already-running ComfyUI instead of the managed one.
- **Custom workflow** — path to a ComfyUI workflow exported in **API format** (.json). Setting it adds the `generate_custom_image` tool.
- **Custom workflow prompt node** — title of the node that receives the prompt text (auto-detected from the first KSampler's positive input if left blank).
- **Expose via Cloudflare tunnel** *(advanced, standalone only)* — serve the MCP endpoint over a public tunnel instead of localhost.

Under the hood these are stored in `~/.comfy-gen-mcp/local_config.json`, which is auto-created with defaults on first run. You can edit it by hand if you like, but the Settings panel is the easy path.

## Troubleshooting

- **Nothing shows up after a couple minutes:** restart Claude Desktop. (First-run ComfyUI install + model downloads can take a while; check progress via **Settings > Open Logs Folder**.)
- **Something fails after a custom node install:** restart Claude Desktop, ComfyUI, or your computer. ComfyUI sometimes leaves ghost processes behind.
- **Slow generation / timeouts:** if a generation takes longer than ~55 seconds, the tool returns a request token and Claude automatically retries to fetch the result. On slower hardware (especially macOS with MPS), this is normal.
- **Diagnostics:** use **Settings > Open Logs Folder** — everything (server + ComfyUI) is consolidated there.
- **macOS "damaged app" error:** run `xattr -cr "/Applications/Comfy-Gen-MCP.app"` in Terminal.
