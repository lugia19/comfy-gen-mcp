"""
ComfyUI Image Gen — MCP server entry point.

Supports two modes:
  - DXT/stdio (default): run by Claude Desktop via the .mcpb extension
  - HTTP connector: run standalone with --http flag for remote access
"""

import argparse
import logging
import os
import secrets
import subprocess
import sys
import threading
import time

# Ensure the extension root is on sys.path so `from server.xxx` imports work
# whether this file is run as a script (uv), as a module, or as a frozen exe.
if getattr(sys, "frozen", False):
    _ext_dir = sys._MEIPASS
else:
    _ext_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ext_dir not in sys.path:
    sys.path.insert(0, _ext_dir)

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from server.comfyui import (
    check_required_nodes,
    find_comfyui_install,
    find_comfyui_url,
    find_models_dir,
    launch_comfyui,
)
from server.config import COMFYUI_DEFAULT_URL, MODEL_PACKS_DIR, _EXT_DIR
from server.comfy_job import ComfyJob, wait_for_job
from server.model_pack import check_models_present, load_all_packs
from server.workflow import load_custom_workflow

log = logging.getLogger("comfy-mcp")
# CRITICAL: logs must go to stderr — stdout is the MCP stdio transport channel.
_log_fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_log_fmt)

# Log file goes next to the exe (writable), not in _MEIPASS (temp/read-only).
_log_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _ext_dir
_log_file = os.path.join(_log_dir, "server.log")
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setFormatter(_log_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_stderr_handler, _file_handler])


def _env(key: str) -> str | None:
    """Read env var, treating unsubstituted ${user_config.*} as unset."""
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("${"):
        return None
    return val


# ── Global state ──────────────────────────────────────────────────────
comfyui_url: str = COMFYUI_DEFAULT_URL
comfyui_exe: str | None = None
comfyui_process: subprocess.Popen | None = None
models_dir: str | None = None

# Per-pack download state: pack_name → True if download in progress
_downloading: dict[str, bool] = {}
setup_running: bool = False  # True while first-run or ComfyUI setup is in progress

# ── Job queue ─────────────────────────────────────────────────────────
_jobs: dict[str, ComfyJob] = {}



def _get_python_and_cwd() -> tuple[str, str]:
    """Get the Python executable and cwd for launching setup_ui subprocesses.

    When frozen (PyInstaller exe), sys.executable is the exe itself — can't use it
    with -m. Instead use the system Python and point cwd at the bundled data.
    """
    if getattr(sys, "frozen", False):
        # Use system Python; cwd is _MEIPASS which has the server/ package
        return "python", sys._MEIPASS
    else:
        return sys.executable, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_SETUP_LOCKFILE = os.path.join(_EXT_DIR, ".setup_running.lock")


def _is_setup_locked() -> bool:
    """Check if another instance is already running the setup UI."""
    if os.path.isfile(_SETUP_LOCKFILE):
        # Stale lockfile check: if older than 30 minutes, ignore it
        try:
            age = time.time() - os.path.getmtime(_SETUP_LOCKFILE)
            if age > 300:
                log.warning("Stale setup lockfile (%.0fs old), removing", age)
                os.remove(_SETUP_LOCKFILE)
                return False
        except OSError:
            pass
        log.info("Setup lockfile exists — another instance is handling setup")
        return True
    return False


def _launch_setup_background(*setup_args: str):
    """Launch a setup_ui subprocess in a background thread (non-blocking)."""
    global setup_running

    if _is_setup_locked():
        return

    setup_running = True
    # Create lockfile
    try:
        with open(_SETUP_LOCKFILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    def _run():
        global setup_running, comfyui_exe, models_dir
        try:
            python, cwd = _get_python_and_cwd()
            args = [python, "-m", "server.setup_ui"] + list(setup_args)
            log.info("Setup UI command: %s (cwd=%s)", args, cwd)
            # First-run setup: runs before mcp.run() takes over stdin/stdout,
            # so plain subprocess.run() works fine and allows tkinter UI to show.
            # (The on-demand download path must use DEVNULL — see _launch_download_ui.)
            result = subprocess.run(args, cwd=cwd)
            log.info("Setup UI exited with code %d", result.returncode)
            # Re-detect after setup
            comfyui_exe = find_comfyui_install()
            models_dir = find_models_dir()
            log.info("Post-setup: exe=%s, models_dir=%s", comfyui_exe or "NOT FOUND", models_dir or "NOT FOUND")
        except Exception as e:
            log.error("Setup UI failed: %s", e)
        finally:
            setup_running = False
            try:
                os.remove(_SETUP_LOCKFILE)
            except OSError:
                pass
            log.info("Background setup finished.")

    t = threading.Thread(target=_run, daemon=True)
    t.start()


_http_mode_flag = False  # set in main() before tools are called


def _launch_download_ui(pack: dict):
    """Launch the download UI for a model pack (blocking)."""
    title = f"Downloading {pack.get('display_name', 'model')} files..."
    pack_models = pack.get("models", [])

    if _http_mode_flag:
        # HTTP mode: run in-process (no pipe issues, works on macOS too)
        from server.setup_ui import run_download_ui
        log.info("Running download UI in-process for %s", pack["name"])
        run_download_ui(models_dir or "", pack_models, title)
    else:
        # DXT/stdio mode: must subprocess to avoid MCP pipe issues
        pack_path = pack.get("_source_path")
        if not pack_path:
            log.error("No source path for pack %s, cannot launch download UI", pack["name"])
            return
        python, cwd = _get_python_and_cwd()
        args = [python, "-m", "server.setup_ui", "--download", models_dir or "", pack_path]
        try:
            log.info("Download UI command: %s (cwd=%s)", args, cwd)
            subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except Exception as e:
            log.error("Failed to launch download UI: %s", e)


def _download_in_background(pack: dict):
    """Run download UI in a background thread for a model pack."""
    pack_name = pack["name"]
    if _downloading.get(pack_name):
        log.info("Download already in progress for %s", pack_name)
        return
    _downloading[pack_name] = True
    log.info("Starting background download for pack: %s", pack_name)

    def _run():
        try:
            _launch_download_ui(pack)
        finally:
            _downloading[pack_name] = False
            log.info("Background download finished for pack: %s", pack_name)

    t = threading.Thread(target=_run, daemon=True)
    t.start()



def startup(http_mode: bool = False) -> tuple[list[dict], dict | None]:
    """Load model packs, detect ComfyUI, return list of packs to register as tools."""
    global comfyui_url, comfyui_exe, models_dir

    comfyui_url = _env("COMFYUI_URL") or COMFYUI_DEFAULT_URL
    custom_workflow = _env("CUSTOM_WORKFLOW")
    anima_artists = _env("ANIMA_ARTISTS")

    log.info("=== ComfyUI Image Gen DXT startup ===")
    log.info("COMFYUI_URL=%s", comfyui_url)
    log.info("CUSTOM_WORKFLOW=%s", custom_workflow or "(none)")
    log.info("ANIMA_ARTISTS=%s", anima_artists or "(default)")

    # Detect ComfyUI
    comfyui_exe = find_comfyui_install()
    log.info("ComfyUI exe: %s", comfyui_exe or "NOT FOUND")
    models_dir = find_models_dir()
    log.info("Models dir: %s", models_dir or "NOT FOUND")

    # Load model packs
    packs = load_all_packs(MODEL_PACKS_DIR)
    if not packs:
        log.error("No model packs found in %s", MODEL_PACKS_DIR)

    # First-run or ComfyUI-missing setup
    # HTTP mode: run blocking (no pipe issues). DXT/stdio mode: run in background thread.
    from server.config import load_local_config
    local_cfg = load_local_config()
    if not local_cfg.get("setup_complete"):
        if http_mode:
            # HTTP mode: run setup UI directly in-process (no pipe issues, no subprocess needed)
            from server.setup_ui import run_first_time_setup
            need_comfyui = comfyui_exe is None
            log.info("First run detected — launching setup wizard (blocking, in-process)...")
            run_first_time_setup(models_dir or "", packs, need_comfyui, in_process=True)
            comfyui_exe = find_comfyui_install()
            models_dir = find_models_dir()
            if comfyui_exe is None:
                log.error("ComfyUI still not found after setup. Exiting.")
                print("ComfyUI is required. Please install it and try again.")
                sys.exit(1)
        else:
            log.info("First run detected — launching setup wizard in background...")
            _launch_setup_background("--first-run", models_dir or "", MODEL_PACKS_DIR)
    elif comfyui_exe is None:
        if http_mode:
            from server.setup_ui import run_comfyui_setup
            log.info("ComfyUI not found — launching detection UI (blocking, in-process)...")
            run_comfyui_setup(in_process=True)
            comfyui_exe = find_comfyui_install()
            models_dir = find_models_dir()
            if comfyui_exe is None:
                log.error("ComfyUI still not found after setup. Exiting.")
                print("ComfyUI is required. Please install it and try again.")
                sys.exit(1)
        else:
            log.info("ComfyUI not found — launching detection UI in background...")
            _launch_setup_background("--comfyui")

    # Apply per-pack customizations
    for pack in packs:
        # Anima artist list: env var > local_config > pack default
        if pack.get("default_artist_list"):
            local_cfg = load_local_config()
            artists_str = (
                anima_artists
                or local_cfg.get("pack_settings", {}).get(pack["name"], {}).get("artist_list")
                or pack["default_artist_list"]
            )
            # Split into preferred default (first) and others
            parts = [a.strip() for a in artists_str.split(",") if a.strip()]
            if parts:
                preferred = parts[0]
                others = ", ".join(parts[1:]) if len(parts) > 1 else "none"
                artist_display = f"preferred default: {preferred}, others available: {others}"
            else:
                artist_display = artists_str
            log.info("Pack '%s' artist_list: %s", pack["name"], artists_str)
            pack["tool_description"] = pack["tool_description"].replace("{artist_list}", artist_display)

    # Load custom workflow as a separate tool (not overriding packs)
    custom_pack = None
    if custom_workflow and os.path.isfile(custom_workflow):
        log.info("Loading custom workflow: %s", custom_workflow)
        wf, pnid, snids = load_custom_workflow(custom_workflow)
        custom_pack = {
            "name": "custom",
            "display_name": "Custom Workflow",
            "tool_name": "generate_custom_image",
            "tool_description": (
                "Generate an image using a user-provided custom ComfyUI workflow. "
                "Use natural language to describe the image. "
                "The aspect_ratio parameter controls image shape: "
                "square (1:1), portrait (3:4), landscape (4:3), tall (9:16), wide (16:9). Default is square."
            ),
            "workflow": wf,
            "prompt_node_id": pnid,
            "seed_nodes": [{"node_id": sid, "field": "seed"} for sid in snids],
            "models": [],
        }
        log.info("Custom workflow loaded: prompt_node=%s, samplers=%s", pnid, snids)
    elif custom_workflow:
        log.warning("Custom workflow path not found: %s", custom_workflow)

    # Log model status per pack
    for pack in packs:
        if models_dir:
            present = check_models_present(models_dir, pack)
            log.info("Pack '%s': models %s", pack["name"], "OK" if present else "MISSING (will download on use)")
        else:
            log.warning("Pack '%s': no models_dir, cannot check models", pack["name"])

    return packs, custom_pack


def main():
    # macOS .app relaunch: if no terminal, reopen in Terminal.app
    import platform
    if platform.system() == "Darwin" and not os.environ.get("TERM"):
        executable = os.path.abspath(sys.argv[0]) if getattr(sys, "frozen", False) else os.path.abspath(sys.executable)
        exe_dir = os.path.dirname(executable)
        # Escape single quotes for AppleScript
        exe_dir_esc = exe_dir.replace("'", "'\\''")
        executable_esc = executable.replace("'", "'\\''")
        script = (
            f'tell application "Terminal"\n'
            f"    set newTab to do script \"cd '{exe_dir_esc}' && '{executable_esc}' && exit\"\n"
            f'    activate\n'
            f'end tell'
        )
        subprocess.Popen(["osascript", "-e", script])
        sys.exit(0)

    # Frozen exe is always HTTP mode; script mode uses argparse.
    if getattr(sys, "frozen", False):
        class _Args:
            http = True
            port = 9247
            tunnel = None
        args = _Args()
    else:
        parser = argparse.ArgumentParser(description="ComfyUI Image Gen MCP Server")
        parser.add_argument("--http", action="store_true", help="Run as HTTP connector instead of stdio (DXT)")
        parser.add_argument("-p", "--port", type=int, default=9247, help="HTTP server port (default: 9247)")
        parser.add_argument("-t", "--tunnel", nargs="?", const="temp", help="Start a cloudflare tunnel (HTTP mode only)")
        args = parser.parse_args()

    global _http_mode_flag
    http_mode = args.http
    _http_mode_flag = http_mode

    # In HTTP mode, inject settings from local_config.json into env vars
    # so startup()'s _env() reads them identically to DXT mode.
    if http_mode:
        from server.config import load_local_config, save_local_config
        local_cfg = load_local_config()
        env_map = {
            "COMFYUI_URL": "comfyui_url",
            "COMFYUI_EXE": "comfyui_exe",
            "CUSTOM_WORKFLOW": "custom_workflow",
            "ANIMA_ARTISTS": "anima_artists",
        }
        for env_key, cfg_key in env_map.items():
            val = local_cfg.get(cfg_key)
            if val and env_key not in os.environ:
                os.environ[env_key] = val

    packs, custom_pack = startup(http_mode=http_mode)

    # Create FastMCP with mode-appropriate config
    if http_mode:
        from server.config import load_local_config, save_local_config
        local_cfg = load_local_config()

        # Generate/load MCP secret path
        mcp_path = local_cfg.get("mcp_path")
        if not mcp_path:
            mcp_path = f"/mcp/{secrets.token_urlsafe(32)}"
            local_cfg["mcp_path"] = mcp_path
            save_local_config(local_cfg)
            log.info("Generated new MCP path (saved to local_config.json)")

        mcp = FastMCP(
            "ComfyUI Image Gen",
            stateless_http=True,
            json_response=True,
            host="0.0.0.0",
            port=args.port,
            streamable_http_path=mcp_path,
        )
        log.info("HTTP mode: port=%d, path=%s", args.port, mcp_path)
    else:
        mcp = FastMCP("ComfyUI Image Gen")

    # Register the custom workflow tool (always present, errors if not configured)
    @mcp.tool(
        name="generate_custom_image",
        description=(
            "Generate an image using a user-provided custom ComfyUI workflow. "
            "This tool only works if the user has configured a custom workflow path in the extension settings. "
            "Use natural language to describe the image. "
            "The aspect_ratio parameter controls image shape: "
            "square (1:1), portrait (3:4), landscape (4:3), tall (9:16), wide (16:9). Default is square."
        ),
    )
    async def generate_custom_image(prompt: str, aspect_ratio: str = "square") -> CallToolResult:
        global comfyui_process, comfyui_url

        if custom_pack is None:
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "No custom workflow is configured. "
                    "To use this tool, set a workflow path in Settings > Extensions > Configure > Custom Workflow Path."
                ))]
            )

        log.info("generate_custom_image called, aspect=%s, prompt=%r", aspect_ratio, prompt[:100])

        # Ensure ComfyUI is running
        found_url = find_comfyui_url(comfyui_url)
        if found_url:
            comfyui_url = found_url
        else:
            if comfyui_exe:
                try:
                    comfyui_process, comfyui_url = launch_comfyui(comfyui_exe, comfyui_url)
                except TimeoutError as e:
                    return CallToolResult(content=[TextContent(type="text", text=f"Error: {e}")])
            else:
                return CallToolResult(
                    content=[TextContent(type="text", text="ComfyUI is not running. Please start ComfyUI Desktop.")]
                )

        # Start generation as a background job and wait with timeout
        ComfyJob.cleanup_old(_jobs)
        job = ComfyJob(prompt, custom_pack, aspect_ratio, comfyui_url)
        job.start()
        _jobs[job.token] = job
        return await wait_for_job(job)

    # Register fetch_result tool for retrieving async generation results
    @mcp.tool(
        name="fetch_result",
        description=(
            "Fetch the result of an image generation that is still in progress. "
            "Use this when a generation tool returns a request_token instead of an image."
        ),
    )
    async def fetch_result(request_token: str) -> CallToolResult:
        log.info("fetch_result called, token=%s", request_token)
        if request_token not in _jobs:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown or expired request token: {request_token}")]
            )
        return await wait_for_job(_jobs[request_token])

    total_tools = len(packs) + 2  # packs + custom + fetch_result
    log.info("Starting MCP server, %d tool(s)...", total_tools)

    # Register one tool per model pack
    def _make_handler(pack: dict):
        """Factory that returns a tool handler with `pack` captured in its closure."""
        async def handler(prompt: str, aspect_ratio: str = "square") -> CallToolResult:
            global comfyui_process, comfyui_url

            log.info("%s called, aspect=%s, prompt=%r", pack["tool_name"], aspect_ratio, prompt[:100])

            # Check if first-run setup is still in progress
            if setup_running:
                log.warning("Setup still running, rejecting request")
                return CallToolResult(
                    content=[TextContent(type="text", text=(
                        "First-time setup is still in progress. "
                        "A setup window should be open — check your taskbar. "
                        "If you don't see it, restart Claude Desktop to re-trigger the setup. "
                        "Please try again once setup is complete."
                    ))]
                )

            # Check if we can find the models directory
            if not models_dir:
                log.warning("No models directory found — ComfyUI may not have been run yet")
                return CallToolResult(
                    content=[TextContent(type="text", text=(
                        "Cannot find ComfyUI's models directory. "
                        "Please open ComfyUI Desktop and complete its initial setup first, then try again. "
                        "ComfyUI needs to run at least once to create its configuration."
                    ))]
                )

            # Check if this pack's models are downloaded
            if not check_models_present(models_dir, pack):
                pack_name = pack["display_name"]
                if _downloading.get(pack["name"]):
                    log.info("Download already in progress for %s", pack_name)
                else:
                    log.info("Models missing for %s, launching download...", pack_name)
                    _download_in_background(pack)
                return CallToolResult(
                    content=[TextContent(type="text", text=(
                        f"Models for {pack_name} are being downloaded. "
                        "A download progress window should be open — check your taskbar. "
                        "If you don't see it, restart Claude Desktop to re-trigger the setup. "
                        "Please try again once the download completes."
                    ))]
                )

            # Ensure ComfyUI is running
            log.info("Scanning for ComfyUI (comfyui_url=%s)...", comfyui_url)
            found_url = find_comfyui_url(comfyui_url)
            log.info("find_comfyui_url returned: %s", found_url)
            if found_url:
                comfyui_url = found_url
            else:
                log.info("ComfyUI not found, attempting launch... (comfyui_exe=%s)", comfyui_exe)
                if comfyui_exe:
                    try:
                        comfyui_process, comfyui_url = launch_comfyui(comfyui_exe, comfyui_url)
                    except TimeoutError as e:
                        log.error("ComfyUI launch timed out: %s", e)
                        return CallToolResult(
                            content=[TextContent(type="text", text=(
                        f"Error: {e}\n\n"
                        "If this error persists, try closing ComfyUI from the system tray and retrying. "
                        "If that doesn't help, restarting your PC can clear ghost ComfyUI instances."
                    ))]
                        )
                else:
                    log.error("No ComfyUI exe path available, cannot auto-launch")
                    return CallToolResult(
                        content=[TextContent(type="text", text="ComfyUI is not running. Please start ComfyUI Desktop.")]
                    )

            # Check for required custom nodes
            required = pack.get("required_nodes")
            if required:
                missing = check_required_nodes(comfyui_url, required)
                if missing:
                    names = ", ".join(missing)
                    log.error("Missing custom nodes: %s", names)
                    return CallToolResult(
                        content=[TextContent(type="text", text=(
                            f"This model requires the following ComfyUI custom node(s): {names}. "
                            f"Please open ComfyUI in your browser at {comfyui_url} , "
                            "click 'Extensions' in the top right, search for the node name, and click Install. "
                            "Then click apply, wait for it to work and try again."
                            "If it does not work, please close ComfyUI from the system tray."
                        ))]
                    )

            # Start generation as a background job and wait with timeout
            ComfyJob.cleanup_old(_jobs)
            job = ComfyJob(prompt, pack, aspect_ratio, comfyui_url)
            job.start()
            _jobs[job.token] = job
            return await wait_for_job(job)
        return handler

    for pack in packs:
        mcp.tool(name=pack["tool_name"], description=pack["tool_description"])(_make_handler(pack))

    # Start tunnel and run server
    tunnel_proc = None
    if http_mode:
        from server.tunnel import start_cloudflare_tunnel, copy_to_clipboard
        from server.config import load_local_config, save_local_config
        local_cfg = load_local_config()
        mcp_path = local_cfg.get("mcp_path", "/mcp")

        # Determine tunnel mode: CLI flag, saved preference, or interactive prompt
        use_tunnel = args.tunnel is not None
        if not use_tunnel and "use_tunnel" in local_cfg:
            use_tunnel = local_cfg["use_tunnel"]
        elif not use_tunnel and args.tunnel is None:
            from server.tunnel import show_tunnel_choice
            use_tunnel = show_tunnel_choice(local_cfg, save_local_config)

        if use_tunnel:
            try:
                tunnel_proc, tunnel_url = start_cloudflare_tunnel(args.port)
                full_url = f"{tunnel_url}{mcp_path}"
                log.info("Tunnel URL: %s", full_url)
            except RuntimeError as e:
                log.error("Tunnel failed: %s", e)
                print(f"Tunnel failed: {e}")
                full_url = None
        else:
            full_url = None
            print(f"\n  Reverse proxy target: http://localhost:{args.port}{mcp_path}\n")
            log.info("Reverse proxy target: http://localhost:%d%s", args.port, mcp_path)

        # Show URL to user (tunnel mode only)
        if full_url:
            import platform
            if platform.system() == "Darwin":
                # macOS: NSWindow must be on main thread, so use console output instead
                from server.tunnel import copy_to_clipboard
                print(f"\n  MCP URL: {full_url}")
                if copy_to_clipboard(full_url):
                    print("  (Copied to clipboard)")
                print("\n  How to add as a connector in Claude.ai:")
                print("  1) Go to claude.ai, click on Customize")
                print("  2) Click on Connectors")
                print("  3) Click on the + sign next to the search icon")
                print("  4) Click 'Add custom connector'")
                print("  5) Give it a name and paste the URL above")
                print("  6) Optional: Remove any old versions of the connector\n")
            else:
                from server.tunnel import show_url_window
                url_thread = threading.Thread(target=show_url_window, args=(full_url,), daemon=True)
                url_thread.start()

        log.info("Starting MCP server (HTTP mode, port %d)...", args.port)
        try:
            mcp.run(transport="streamable-http")
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            if tunnel_proc:
                tunnel_proc.kill()
            if comfyui_process:
                log.info("Stopping ComfyUI...")
                comfyui_process.terminate()
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
