"""
Comfy-Gen-MCP — server entry point.

Supports two modes:
  - DXT/stdio (default): run by Claude Desktop via the .mcpb extension
  - HTTP connector: run standalone with --http flag or as frozen exe
"""

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time

import psutil

_ext_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ext_dir not in sys.path:
    sys.path.insert(0, _ext_dir)

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from server.comfyui import (
    check_required_nodes,
    clear_object_info_cache,
    ensure_manager_installed,
    find_comfy_cli,
    find_comfyui_url,
    find_models_dir,
    install_custom_nodes,
    launch_comfyui,
    stop_comfyui,
    upload_image,
)
from server.config import (
    COMFYUI_DEFAULT_URL,
    EXTENSION_VERSION,
    MODEL_PACKS_DIR,
    ensure_logs_dir,
    ensure_user_settings,
    is_http_mode,
    load_local_config,
    save_local_config,
)
from server.comfy_job import ComfyJob, wait_for_job
from server.model_pack import check_models_present, group_packs_by_tool, load_all_packs, resolve_pack_selections
from server.tool_specs import CUSTOM_DESC, EDIT_DESC, FETCH_DESC, resolve_tool_description
from server.workflow import inject_loras, load_custom_workflow

log = logging.getLogger("comfy-mcp")
# CRITICAL: logs must go to stderr — stdout is the MCP stdio transport channel.
_log_fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_log_fmt)

_log_file = os.path.join(ensure_logs_dir(), "server.log")
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
comfy_cli_path: str | None = None
comfyui_process: subprocess.Popen | None = None
models_dir: str | None = None

# Per-pack download state: pack_name → True if download in progress
_downloading: dict[str, bool] = {}
_server_window = None  # set in HTTP mode for cross-thread download UI
_runtime_lock = None  # single-instance file lock handle; held for the process lifetime

# Managed-lifecycle state — "pings arm it". Only the stdio shim ever hits GET /alive, so being
# pinged IS the signal that this server is shim-managed: the first ping ARMS it, and once armed
# it self-shuts-down when the pings go stale (shim died/crashed/wedged). A server nobody pings —
# the standalone HTTP instance the user runs directly — is never armed and stays up. No flag
# crosses the bootstrapper; the ping we already send does the whole job.
_keepalive_ts: list[float] = [0.0]
_armed: list[bool] = [False]  # set True on the first /alive ping
# 3 minutes: long enough that a stalled shim (e.g. the user sitting on a tool-permission
# prompt, which pauses the keepalive pings) doesn't trip a premature self-shutdown.
MANAGED_GRACE_SECONDS = 180
# Once pings lapse past this AND Claude Desktop is gone, shut down without waiting out the full
# grace. This is below the shim's 15s keepalive interval, so the process check runs ~once per ping
# cycle in normal operation — that's fine: the check is what prevents a premature exit, not the
# threshold (an app-open server always sees claude.exe and stays up).
MANAGED_FAST_GRACE_SECONDS = 10


def _claude_desktop_running() -> bool:
    """True if a Claude Desktop process (claude.exe, path without 'claude-code') is running.

    Used only to accelerate managed shutdown — biased toward True so we never exit early on
    uncertainty (worst case: fall back to the full grace). Windows-only; elsewhere returns True so
    the fast path is disabled and the 180s grace governs unchanged."""
    if sys.platform != "win32":
        return True
    try:
        for p in psutil.process_iter(["name", "exe"]):
            try:
                if (p.info["name"] or "").lower() != "claude.exe":
                    continue
                exe = (p.info["exe"] or "").lower()
                if "claude-code" in exe:
                    continue  # Claude Code CLI, not the desktop app
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue  # can't inspect — skip; another match may still confirm
        return False
    except Exception as e:
        log.warning("Claude Desktop process check failed (%s); assuming running", e)
        return True

# Self-restart state (Settings → Save → Restart). _START_CWD preserves the cwd for the
# `-m server.main` launch form (run_http.py is cwd-independent).
_START_CWD = os.getcwd()

# Active pack per tool_name — looked up dynamically by handlers.
# Updated after background setup completes so selections take effect without restart.
_active_packs: dict[str, dict] = {}

# ── Job queue ─────────────────────────────────────────────────────────
_jobs: dict[str, ComfyJob] = {}


def _get_output_dir() -> str | None:
    """Derive ComfyUI's output directory from the models directory."""
    if models_dir:
        return os.path.join(os.path.dirname(models_dir), "output")
    return None


def _ensure_nodes(required: dict[str, str]) -> str | None:
    """Install missing nodes and restart ComfyUI if needed.

    Returns an error message string, or None if all nodes are available.
    """
    global comfyui_process, comfyui_url

    missing = check_required_nodes(comfyui_url, required)
    if not missing:
        return None

    if not comfy_cli_path:
        names = ", ".join(missing)
        return f"This model requires custom node(s): {names}. comfy-cli is not available to install them."

    log.info("Auto-installing missing nodes: %s", missing)
    failed = install_custom_nodes(comfy_cli_path, missing)
    if failed:
        names = ", ".join(failed)
        return f"Failed to auto-install custom node(s): {names}."

    # Restart ComfyUI so it loads the new nodes
    log.info("Restarting ComfyUI to load newly installed nodes...")
    stop_comfyui(comfy_cli_path, comfyui_process)
    clear_object_info_cache()
    time.sleep(2)
    try:
        comfyui_process, comfyui_url = launch_comfyui(comfy_cli_path)
    except (TimeoutError, RuntimeError) as e:
        return f"Nodes installed but ComfyUI failed to restart: {e}"

    # Verify
    still_missing = check_required_nodes(comfyui_url, required)
    if still_missing:
        names = ", ".join(still_missing)
        return f"Nodes installed and ComfyUI restarted, but still missing: {names}."

    log.info("All required nodes now available after restart")
    return None


def _resolve_image_path(path_or_url: str) -> tuple[str, bool]:
    """If path_or_url is a URL, download to a temp file. Returns (local_path, is_temp)."""
    if path_or_url.startswith(("http://", "https://")):
        import tempfile
        import httpx as _httpx
        resp = _httpx.get(path_or_url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        # Guess extension from content-type
        ct = resp.headers.get("content-type", "")
        ext = ".png"
        if "jpeg" in ct or "jpg" in ct:
            ext = ".jpg"
        elif "webp" in ct:
            ext = ".webp"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.write(resp.content)
        tmp.close()
        log.info("Downloaded %s -> %s (%d bytes)", path_or_url, tmp.name, len(resp.content))
        return tmp.name, True
    return path_or_url, False


def _launch_download(pack: dict):
    """Download a model pack's files (blocking). Shows the Qt download dialog if a server
    window is up, otherwise downloads silently."""
    title = f"Downloading {pack.get('display_name', 'model')} files..."
    pack_models = pack.get("models", [])

    if _server_window is not None:
        # Signal the main Qt thread to show the download dialog
        log.info("Requesting download UI via main thread signal for %s", pack["name"])
        _server_window.request_download(models_dir or "", pack_models, title)
    else:
        # No server window yet — download silently
        from server.downloader import DownloadState, download_models
        log.info("No server window available, downloading silently for %s", pack["name"])
        state = DownloadState()
        download_models(models_dir or "", pack_models, state)


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
            _launch_download(pack)
        finally:
            _downloading[pack_name] = False
            log.info("Background download finished for pack: %s", pack_name)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ── Per-request preflight helpers ─────────────────────────────────────

def _check_and_download_models(pack: dict) -> str | None:
    """Ensure a pack's models are present, kicking off a background download if not.

    Returns None when models are ready, or a user-facing message string when the
    models directory is missing or a download is now in progress.
    """
    if not models_dir:
        log.warning("No models directory found")
        return (
            "Cannot find ComfyUI's models directory. "
            "Please run the first-time setup to install ComfyUI, then try again."
        )
    if check_models_present(models_dir, pack):
        return None
    pack_name = pack["display_name"]
    log.info("Models missing for %s, launching download...", pack_name)
    _download_in_background(pack)  # no-ops if a download is already running
    return (
        f"Models for {pack_name} are being downloaded. "
        "A download progress window should be open — check your taskbar. "
        "If you don't see it, restart Claude Desktop to re-trigger the setup. "
        "Please try again once the download completes."
    )


def _ensure_comfyui() -> str | None:
    """Ensure ComfyUI is reachable, launching it via comfy-cli if needed.

    Updates the comfyui_url / comfyui_process globals. Returns None on success,
    or a user-facing error message string on failure.
    """
    global comfyui_process, comfyui_url

    log.info("Scanning for ComfyUI (comfyui_url=%s)...", comfyui_url)
    found_url = find_comfyui_url(comfyui_url)
    log.info("find_comfyui_url returned: %s", found_url)
    if found_url:
        comfyui_url = found_url
        return None

    log.info("ComfyUI not found, attempting launch... (comfy_cli=%s)", comfy_cli_path)
    if not comfy_cli_path:
        log.error("comfy-cli not available, cannot auto-launch")
        return (
            "ComfyUI is not running and comfy-cli was not found to launch it.\n\n"
            "Install comfy-cli with: pip install comfy-cli"
        )
    try:
        comfyui_process, comfyui_url = launch_comfyui(comfy_cli_path)
    except (TimeoutError, RuntimeError) as e:
        log.error("ComfyUI launch failed: %s", e)
        return (
            f"Error: {e}\n\n"
            "If this error persists, try stopping any running ComfyUI processes and retrying."
        )
    return None


async def _run_generation(prompt: str, pack: dict, aspect_ratio: str) -> CallToolResult:
    """Create, start, store, and await a ComfyJob for the given pack."""
    ComfyJob.cleanup_old(_jobs)
    job = ComfyJob(prompt, pack, aspect_ratio, comfyui_url)
    job.start()
    _jobs[job.token] = job
    return await wait_for_job(job, _get_output_dir())


# ── Startup ───────────────────────────────────────────────────────────

def _apply_loras_to_pack(pack: dict) -> None:
    """Apply per-pack LoRAs from local_config.json (anima pack only, model-only).

    Mutates pack['workflow'] in place. Malformed entries and injection failures are
    logged and skipped rather than failing startup.
    """
    raw_loras = load_local_config().get("pack_loras", {}).get(pack["name"])
    if raw_loras and pack["name"] != "anima":
        log.warning("Pack '%s': pack_loras configured but LoRAs are only supported for "
                    "the 'anima' pack — ignoring", pack["name"])
        return
    if not raw_loras:
        return

    loras = []
    for entry in raw_loras:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict) or not entry.get("name"):
            log.warning("Pack '%s': skipping malformed LoRA entry %r", pack["name"], entry)
            continue
        name = entry["name"]
        try:
            strength = float(entry.get("strength", 1.0))
        except (TypeError, ValueError):
            log.warning("Pack '%s': invalid strength for LoRA '%s', using 1.0", pack["name"], name)
            strength = 1.0
        trigger = str(entry.get("trigger") or "").strip()
        if models_dir and not os.path.isfile(os.path.join(models_dir, "loras", name)):
            log.warning(
                "Pack '%s': LoRA file not found in models/loras: %s (generation will fail "
                "until you place it there)", pack["name"], name
            )
        loras.append({"name": name, "strength": strength, "trigger": trigger})

    if loras:
        try:
            pack["lora_toggles"] = inject_loras(pack["workflow"], loras, pack.get("lora_target"))
            log.info(
                "Pack '%s': injected %d LoRA(s): %s", pack["name"], len(loras),
                ", ".join(
                    f"{l['name']}@{l['strength']}" + (f" [trigger: {l['trigger']}]" if l["trigger"] else "")
                    for l in loras
                )
            )
        except Exception as e:
            log.error("Pack '%s': failed to inject LoRAs (%s); serving pack unmodified", pack["name"], e)


def _apply_pack_customizations(packs: list[dict], groups: dict[str, list[dict]],
                               anima_steps: str | None) -> None:
    """Finalize each resolved pack's tool description (group override + artist-list
    substitution, via the shared resolve_tool_description), apply the anima step override, and
    inject LoRAs (mutates packs in place)."""
    for pack in packs:
        pack["tool_description"] = resolve_tool_description(pack, groups, env_reader=_env)
        log.info("Pack '%s' tool description finalized", pack["name"])

        if pack["name"] == "anima" and anima_steps:
            try:
                steps = int(anima_steps)
                pack["workflow"]["19"]["inputs"]["steps"] = steps
                log.info("Pack 'anima' steps overridden to %d", steps)
            except ValueError:
                log.warning("Invalid ANIMA_STEPS value '%s', keeping default", anima_steps)

        _apply_loras_to_pack(pack)


def _load_custom_pack(custom_workflow: str | None,
                      prompt_node_title: str | None) -> tuple[dict | None, str | None]:
    """Load the user's custom workflow as a synthetic pack. Returns (custom_pack, error)."""
    if not custom_workflow:
        return None, None
    if not os.path.isfile(custom_workflow):
        log.warning("Custom workflow path not found: %s", custom_workflow)
        return None, f"Custom workflow file not found: {custom_workflow}"

    log.info("Loading custom workflow: %s", custom_workflow)
    try:
        wf, pnid, snids = load_custom_workflow(custom_workflow, prompt_node_title)
    except ValueError as exc:
        log.error("Failed to load custom workflow: %s", exc)
        return None, str(exc)

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
    return custom_pack, None


def _launch_comfyui_background() -> None:
    """Warm up ComfyUI in a background thread so it's ready before the first tool call.
    Non-fatal: if it isn't up yet, tools retry via _ensure_comfyui() on demand."""
    if not comfy_cli_path:
        return

    def _bg():
        if _ensure_comfyui():
            log.warning("ComfyUI not ready at startup (will retry on first request)")

    threading.Thread(target=_bg, daemon=True).start()


def startup() -> tuple[list[dict], dict[str, list[dict]], dict | None, str | None]:
    """Load model packs, detect ComfyUI, return (selected_packs, groups, custom_pack, custom_workflow_error)."""
    global comfyui_url, comfy_cli_path, models_dir

    comfyui_url = _env("COMFYUI_URL") or COMFYUI_DEFAULT_URL
    custom_workflow = _env("CUSTOM_WORKFLOW")
    custom_workflow_prompt_node_title = _env("CUSTOM_WORKFLOW_PROMPT_NODE")
    anima_artists = _env("ANIMA_ARTISTS")
    anima_steps = _env("ANIMA_STEPS")

    log.info("=== Comfy-Gen-MCP startup ===")
    log.info("Mode: %s", "HTTP" if is_http_mode() else "DXT/stdio")
    log.info("COMFYUI_URL=%s", comfyui_url)
    log.info("CUSTOM_WORKFLOW=%s", custom_workflow or "(none)")
    log.info("ANIMA_ARTISTS=%s", anima_artists or "(default)")
    log.info("ANIMA_STEPS=%s", anima_steps or "(default: 30)")

    # Detect comfy-cli and ComfyUI. This cold-starts comfy-cli (slow on first launch), so
    # run it on a worker thread behind a responsive "starting up" dialog. A static splash
    # can't survive this — the event loop isn't running yet, so the main thread blocking
    # here would leave any plain window frozen/blank.
    def _detect_environment():
        try:
            cli = find_comfy_cli()
            return cli, (find_models_dir(cli) if cli else None)
        except Exception as e:
            log.error("Environment detection failed: %s", e)
            return None, None

    from server.ui import run_with_progress
    comfy_cli_path, models_dir = run_with_progress(
        "Starting Comfy-Gen-MCP…\n\nDetecting your ComfyUI installation.\n"
        "This can take a moment on first launch.",
        _detect_environment,
    )
    log.info("comfy-cli: %s", comfy_cli_path or "NOT FOUND")
    log.info("Models dir: %s", models_dir or "NOT FOUND")
    if not comfy_cli_path:
        log.warning("comfy-cli not found — ComfyUI management unavailable")

    # Load model packs and group by tool_name
    all_packs = load_all_packs(MODEL_PACKS_DIR)
    if not all_packs:
        log.error("No model packs found in %s", MODEL_PACKS_DIR)
    groups = group_packs_by_tool(all_packs)
    for tool_name, group in groups.items():
        if len(group) > 1:
            log.info("Tool '%s' has %d packs: %s", tool_name, len(group), [p["name"] for p in group])

    # First-run or ComfyUI-missing setup
    local_cfg = load_local_config()

    def _refresh_after_setup():
        global comfyui_url, models_dir
        cfg = load_local_config()
        if cfg.get("comfyui_url"):
            comfyui_url = cfg["comfyui_url"]
        if comfy_cli_path:
            models_dir = find_models_dir(comfy_cli_path)

    needs_setup = local_cfg.get("setup_version") != EXTENSION_VERSION or not models_dir
    if needs_setup:
        from server.ui import run_first_time_setup
        log.info("Setup needed — launching wizard (blocking, in-process)...")
        run_first_time_setup(all_packs, groups, in_process=True)
        _refresh_after_setup()
        if not models_dir:
            log.error("ComfyUI models dir still not found after setup. Exiting.")
            sys.exit(1)

    # Resolve one pack per tool_name group based on user config
    packs = resolve_pack_selections(groups, env_reader=_env)
    log.info("Resolved packs: %s", [p["name"] for p in packs])

    # Finalize tool descriptions (group override + artist list) and apply anima steps + LoRAs.
    _apply_pack_customizations(packs, groups, anima_steps)

    # Load custom workflow as a separate tool
    custom_pack, custom_workflow_error = _load_custom_pack(
        custom_workflow, custom_workflow_prompt_node_title
    )

    # Log model status
    for pack in packs:
        if models_dir:
            present = check_models_present(models_dir, pack)
            log.info("Pack '%s': models %s", pack["name"], "OK" if present else "MISSING (will download on use)")
        else:
            log.warning("Pack '%s': no models_dir, cannot check models", pack["name"])

    # Ensure ComfyUI-Manager is present before launching (needed for `comfy node install`)
    if comfy_cli_path:
        ensure_manager_installed(comfy_cli_path)

    # Launch ComfyUI in background so it's ready by the time a tool is called
    _launch_comfyui_background()

    return packs, groups, custom_pack, custom_workflow_error


# ── Tool registration ─────────────────────────────────────────────────

def register_tools(mcp: FastMCP, packs: list[dict], custom_pack: dict | None, custom_workflow_error: str | None = None):
    """Register all tools on the FastMCP instance."""

    # Custom workflow tool
    @mcp.tool(name="generate_custom_image", description=CUSTOM_DESC)
    async def generate_custom_image(prompt: str, aspect_ratio: str = "square") -> CallToolResult:
        if custom_pack is None:
            if custom_workflow_error:
                msg = f"Custom workflow failed to load: {custom_workflow_error}"
            else:
                msg = (
                    "No custom workflow is configured. "
                    "To use this tool, set a workflow path in Settings > Extensions > Configure > Custom Workflow Path."
                )
            return CallToolResult(
                content=[TextContent(type="text", text=msg)]
            )

        log.info("generate_custom_image called, aspect=%s, prompt=%r", aspect_ratio, prompt[:100])

        err = _ensure_comfyui()
        if err:
            return CallToolResult(content=[TextContent(type="text", text=err)])

        return await _run_generation(prompt, custom_pack, aspect_ratio)

    # Fetch result tool
    @mcp.tool(name="fetch_result", description=FETCH_DESC)
    async def fetch_result(request_token: str) -> CallToolResult:
        log.info("fetch_result called, token=%s", request_token)
        if request_token not in _jobs:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown or expired request token: {request_token}")]
            )
        return await wait_for_job(_jobs[request_token], _get_output_dir())

    # Edit image tool
    _edit_pack_path = os.path.join(MODEL_PACKS_DIR, "flux2klein_edit.json")
    _edit_pack = None
    if os.path.isfile(_edit_pack_path):
        with open(_edit_pack_path, encoding="utf-8") as f:
            _edit_pack = json.load(f)
            _edit_pack["_source_path"] = os.path.abspath(_edit_pack_path)

    @mcp.tool(name="edit_image", description=EDIT_DESC)
    async def edit_image(prompt: str, image_path: str, second_image_path: str = "") -> CallToolResult:
        # Resolve URLs to local temp files
        temp_files = []
        try:
            image_path, is_temp = _resolve_image_path(image_path)
            if is_temp:
                temp_files.append(image_path)
            if second_image_path:
                second_image_path, is_temp = _resolve_image_path(second_image_path)
                if is_temp:
                    temp_files.append(second_image_path)
        except Exception as e:
            for f in temp_files:
                try: os.remove(f)
                except OSError: pass
            return CallToolResult(
                content=[TextContent(type="text", text=f"Failed to download image: {e}")]
            )

        if not os.path.isfile(image_path):
            return CallToolResult(
                content=[TextContent(type="text", text=f"File not found: {image_path}")]
            )
        if second_image_path and not os.path.isfile(second_image_path):
            return CallToolResult(
                content=[TextContent(type="text", text=f"File not found: {second_image_path}")]
            )

        if _edit_pack is None:
            return CallToolResult(
                content=[TextContent(type="text", text="Image editing is not available (edit pack not found).")]
            )

        use_multi = bool(second_image_path)
        log.info("edit_image called, multi=%s, path=%s, prompt=%r", use_multi, image_path, prompt[:100])

        msg = _check_and_download_models(_edit_pack)
        if msg:
            return CallToolResult(content=[TextContent(type="text", text=msg)])

        err = _ensure_comfyui()
        if err:
            return CallToolResult(content=[TextContent(type="text", text=err)])

        required = _edit_pack.get("required_nodes")
        if required:
            node_err = _ensure_nodes(required)
            if node_err:
                return CallToolResult(content=[TextContent(type="text", text=node_err)])

        # Upload image(s) to ComfyUI
        try:
            uploaded_name1 = upload_image(comfyui_url, image_path)
            uploaded_name2 = upload_image(comfyui_url, second_image_path) if use_multi else None
        except Exception as e:
            log.error("Failed to upload image: %s", e)
            return CallToolResult(
                content=[TextContent(type="text", text=f"Failed to upload image to ComfyUI: {e}")]
            )
        finally:
            for f in temp_files:
                try: os.remove(f)
                except OSError: pass

        # Pick workflow variant and inject image filenames
        import copy
        if use_multi:
            wf = copy.deepcopy(_edit_pack["workflow_multi"])
            prompt_node_id = _edit_pack["prompt_node_id_multi"]
            seed_nodes = _edit_pack["seed_nodes_multi"]
            image_nodes = _edit_pack["image_nodes_multi"]
            uploaded_names = [uploaded_name1, uploaded_name2]
        else:
            wf = copy.deepcopy(_edit_pack["workflow"])
            prompt_node_id = _edit_pack["prompt_node_id"]
            seed_nodes = _edit_pack["seed_nodes"]
            image_nodes = _edit_pack["image_nodes"]
            uploaded_names = [uploaded_name1]

        # Inject image filenames into LoadImage nodes
        for node_id, filename in zip(image_nodes, uploaded_names):
            wf[node_id]["inputs"]["image"] = filename

        # Build a synthetic pack for ComfyJob — prompt and seeds are injected by build_prompt()
        edit_job_pack = {
            "name": _edit_pack["name"],
            "workflow": wf,
            "prompt_node_id": prompt_node_id,
            "seed_nodes": seed_nodes,
        }

        return await _run_generation(prompt, edit_job_pack, "square")

    # Per-pack tools
    def _make_handler(tool_name: str):
        async def handler(prompt: str, aspect_ratio: str = "square") -> CallToolResult:
            pack = _active_packs[tool_name]
            log.info("%s called (pack=%s), aspect=%s, prompt=%r", tool_name, pack["name"], aspect_ratio, prompt[:100])

            msg = _check_and_download_models(pack)
            if msg:
                return CallToolResult(content=[TextContent(type="text", text=msg)])

            err = _ensure_comfyui()
            if err:
                return CallToolResult(content=[TextContent(type="text", text=err)])

            required = pack.get("required_nodes")
            if required:
                node_err = _ensure_nodes(required)
                if node_err:
                    return CallToolResult(content=[TextContent(type="text", text=node_err)])

            return await _run_generation(prompt, pack, aspect_ratio)
        return handler

    for pack in packs:
        _active_packs[pack["tool_name"]] = pack
        mcp.tool(name=pack["tool_name"], description=pack["tool_description"])(_make_handler(pack["tool_name"]))

    total = len(packs) + 3  # packs + custom + edit_image + fetch_result
    log.info("Registered %d tool(s)", total)


# ── Main ──────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(description="Comfy-Gen-MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP connector instead of stdio (DXT)")
    parser.add_argument("-p", "--port", type=int, default=9247, help="HTTP server port (default: 9247)")
    parser.add_argument("-t", "--tunnel", nargs="?", const="temp", help="Start a cloudflare tunnel (HTTP mode only)")
    return parser.parse_args()


def _seed_env_from_config() -> None:
    """Seed missing user settings into local_config.json, then export configured values as
    env vars so startup() picks them up. Env vars already set take precedence."""
    local_cfg = load_local_config()
    if ensure_user_settings(local_cfg):
        save_local_config(local_cfg)
        log.info("Seeded missing user settings in local_config.json")
    # ANIMA_ARTISTS is intentionally NOT seeded here: artists are configured per-pack via
    # pack_settings.anima.artist_list (Settings panel). ANIMA_ARTISTS stays a power-user
    # env override that, when unset, lets the per-pack value take effect.
    env_map = {
        "COMFYUI_URL": "comfyui_url",
        "CUSTOM_WORKFLOW": "custom_workflow",
        "CUSTOM_WORKFLOW_PROMPT_NODE": "custom_workflow_prompt_node",
        "ANIMA_STEPS": "anima_steps",
    }
    for env_key, cfg_key in env_map.items():
        val = local_cfg.get(cfg_key)
        if val and env_key not in os.environ:
            os.environ[env_key] = str(val)  # env values must be strings (anima_steps is int)


def _build_http_app(args) -> tuple[FastMCP, str]:
    """Create the FastMCP app for HTTP mode, including the /alive liveness route.
    Returns (mcp, mcp_path)."""
    local_cfg = load_local_config()
    mcp_path = local_cfg.get("mcp_path")
    if not mcp_path:
        mcp_path = f"/mcp/{secrets.token_urlsafe(32)}"
        local_cfg["mcp_path"] = mcp_path
        save_local_config(local_cfg)
        log.info("Generated new MCP path (saved to local_config.json)")

    mcp = FastMCP(
        "Comfy-Gen-MCP",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        port=args.port,
        streamable_http_path=mcp_path,
    )
    log.info("HTTP mode: port=%d, path=%s", args.port, mcp_path)

    # Liveness + keepalive endpoint. The shim polls this to know the server is up, and pings it
    # to keep the server alive. The first hit arms the managed-shutdown timer (see _armed).
    _keepalive_ts[0] = time.monotonic()

    @mcp.custom_route("/alive", methods=["GET"])
    async def _alive(request):
        from starlette.responses import PlainTextResponse
        _keepalive_ts[0] = time.monotonic()
        _armed[0] = True
        return PlainTextResponse("ok")

    return mcp, mcp_path


def restart_server() -> None:
    """Relaunch this server process to apply settings, then let the caller quit.

    Spawns a faithful copy of how we were started (sys.orig_argv reproduces the `-m server.main`
    or run_http.py form) detached, with COMFY_RESTART=1 so the replacement waits for us to free
    the port before binding. ComfyUI is stopped during the post-window cleanup so the replacement
    starts it fresh. Works the same in standalone and managed mode — the shim isn't involved.
    """
    orig = list(getattr(sys, "orig_argv", []) or [])
    cmd = [sys.executable] + orig[1:] if len(orig) > 1 else [sys.executable, "-m", "server.main", "--http"]

    env = os.environ.copy()
    env["COMFY_RESTART"] = "1"
    log.info("Restarting server: %s (cwd=%s)", cmd, _START_CWD)

    kwargs = {
        "cwd": _START_CWD, "env": env,
        "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (not DETACHED_PROCESS) — gives python.exe an invisible console so the
        # restart doesn't flash a window, matching how the bootstrapper's install.py launches us.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        log.error("Failed to spawn replacement server: %s", e)
        raise


def _wait_for_port_free(port: int, timeout: float = 15.0) -> None:
    """Block until nothing is listening on 127.0.0.1:port (the old instance has exited), or until
    timeout. Returns immediately on a free port, so normal launches don't pay for this."""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return  # nothing accepting connections → port is free
        time.sleep(0.3)
    log.warning("Port %d still busy after %.0fs; binding anyway", port, timeout)


def _run_http_server(mcp: FastMCP, args, mcp_path: str) -> None:
    """Run the HTTP server: optional tunnel, the MCP server thread, then the Qt window on the
    main thread (blocks until quit), then ComfyUI/tunnel cleanup."""
    from server.tunnel import start_cloudflare_tunnel
    from server.ui import show_url_window, show_server_running_window, run_with_progress

    local_cfg = load_local_config()

    # Tunnel mode is opt-in via --tunnel or the Settings "Expose via Cloudflare tunnel" toggle
    # (use_tunnel in local_config). No startup prompt: a shim-managed instance just leaves it
    # off, and standalone users set it in the wizard's settings page or the Settings panel.
    tunnel_proc = None
    use_tunnel = args.tunnel is not None or bool(local_cfg.get("use_tunnel"))

    # Start tunnel if needed
    full_url = None
    if use_tunnel:
        try:
            def _start_tunnel():
                return start_cloudflare_tunnel(args.port)
            tunnel_proc, tunnel_url = run_with_progress("Starting cloudflare tunnel...", _start_tunnel)
            full_url = f"{tunnel_url}{mcp_path}"
            log.info("Tunnel URL: %s", full_url)
        except RuntimeError as e:
            log.error("Tunnel failed: %s", e)

    # If we're the replacement spawned by a self-restart, wait for the outgoing instance to free
    # the port before uvicorn binds it (closes the restart bind race). No-op on a fresh launch.
    if os.environ.pop("COMFY_RESTART", None):
        _wait_for_port_free(args.port)

    # Start MCP server in background daemon thread
    server_thread = threading.Thread(
        target=lambda: mcp.run(transport="streamable-http"),
        daemon=True,
    )
    server_thread.start()
    log.info("MCP server started in background on port %d", args.port)

    # Main thread: Qt window (blocks until Quit from tray)
    def _store_window(w):
        global _server_window
        _server_window = w

    # Once armed (the shim has pinged at least once), the window self-closes when the pings go
    # stale — the shim (Claude Desktop) is gone. A never-pinged standalone server never arms.
    def _stale() -> bool:
        if not _armed[0]:
            return False
        idle = time.monotonic() - _keepalive_ts[0]
        # Hard cap: covers a stalled-but-alive shim (permission prompt) or the MCP disabled with the
        # app still open — both keep claude.exe running, so only time decides here.
        if idle > MANAGED_GRACE_SECONDS:
            log.info("Shim keepalive stale (>%ds) — shutting down", MANAGED_GRACE_SECONDS)
            return True
        # Fast path: pings lapsed AND Claude Desktop is gone (app closed) — don't wait the full grace.
        if idle > MANAGED_FAST_GRACE_SECONDS and not _claude_desktop_running():
            log.info("Claude Desktop gone and pings lapsed (>%ds) — shutting down",
                     MANAGED_FAST_GRACE_SECONDS)
            return True
        return False

    # Managed mode for the UI = "the shim is pinging us" = armed. Reuse pings-arm-it as the
    # signal so the window can hide Quit/Uninstall and reword its footer (no separate flag).
    def _managed() -> bool:
        return _armed[0]

    # The window's status poller needs the live ComfyUI URL — which may be a fallback port if 8188
    # was unavailable — so read the global at call time rather than capturing its initial value.
    def _comfyui_url() -> str:
        return comfyui_url

    if full_url:
        show_url_window(full_url, on_ready=_store_window, stale_check=_stale, managed_check=_managed,
                        restart_cb=restart_server, comfyui_url_getter=_comfyui_url)
    else:
        show_server_running_window(args.port, mcp_path, on_ready=_store_window, stale_check=_stale,
                                   managed_check=_managed, restart_cb=restart_server,
                                   comfyui_url_getter=_comfyui_url)

    # Window closed → cleanup → exit
    log.info("Window closed, shutting down...")
    if tunnel_proc:
        tunnel_proc.kill()
    # Always stop ComfyUI on shutdown, including self-restart, so the replacement comes up clean and
    # re-launches it. (A fallback-port instance can't be re-attached anyway: re-attach probes :8188.)
    if comfy_cli_path:
        stop_comfyui(comfy_cli_path, comfyui_process)


def main():
    args = _parse_args()

    # DXT/stdio entry: run the thin shim, which starts the real HTTP server (if needed)
    # and proxies MCP calls to it. All real logic lives in the HTTP server below.
    if not is_http_mode():
        from server.shim import run as run_shim
        run_shim()
        return

    # ── HTTP server: the one real runtime ─────────────────────────────
    # Single-instance guard: only one runtime may own the port. A duplicate (Claude Desktop
    # spawned the shim twice, or two shims raced a cold start) exits here — before any Qt/tray —
    # so the user never sees a second window. On a self-restart the outgoing instance still holds
    # the lock while it shuts ComfyUI down, so the replacement WAITS for it rather than exiting.
    from server.singleton import acquire_runtime_lock
    global _runtime_lock
    restart = bool(os.environ.get("COMFY_RESTART"))
    _runtime_lock = acquire_runtime_lock(args.port, wait_timeout=30.0 if restart else 0.0)
    if _runtime_lock is None:
        log.info("Another runtime already holds the single-instance lock on port %d; exiting.",
                 args.port)
        return

    _seed_env_from_config()
    packs, _groups, custom_pack, custom_workflow_error = startup()
    mcp, mcp_path = _build_http_app(args)
    register_tools(mcp, packs, custom_pack, custom_workflow_error)
    _run_http_server(mcp, args, mcp_path)


if __name__ == "__main__":
    main()
