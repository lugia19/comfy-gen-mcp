"""ComfyUI Desktop detection, launching, and health checks."""

import json
import logging
import os
import platform
import struct
import subprocess
import threading
import time

import httpx

from .config import COMFYUI_CONFIG_PATH, COMFYUI_DEFAULT_EXE, COMFYUI_DEFAULT_URL, load_local_config, save_local_config

log = logging.getLogger("comfy-mcp")

# Paths checked during the last find_comfyui_install() call (for error messages)
_last_searched_paths: list[str] = []


def _sanitize_path(path: str) -> str:
    """Strip quotes and normalize a file path."""
    path = path.strip().strip('"').strip("'")
    return os.path.normpath(path) if path else ""


def _resolve_lnk(lnk_path: str) -> str | None:
    """Parse a Windows .lnk shortcut and return its target path, or None."""
    try:
        with open(lnk_path, "rb") as f:
            content = f.read()
        # Header is 76 bytes; flags at offset 0x14
        if len(content) < 76:
            return None
        flags = struct.unpack_from("<I", content, 0x14)[0]
        pos = 76
        # Skip LinkTargetIDList if present (flag bit 0)
        if flags & 0x01:
            id_list_size = struct.unpack_from("<H", content, pos)[0]
            pos += 2 + id_list_size
        # Read LinkInfo if present (flag bit 1)
        if flags & 0x02:
            link_info_start = pos
            local_base_path_offset = struct.unpack_from("<I", content, pos + 0x10)[0]
            if local_base_path_offset:
                path_start = link_info_start + local_base_path_offset
                end = content.index(b"\x00", path_start)
                return content[path_start:end].decode("utf-8", errors="replace")
    except Exception as e:
        log.debug("Failed to parse .lnk file %s: %s", lnk_path, e)
    return None


def find_comfyui_install() -> str | None:
    """Return the path to the ComfyUI Desktop executable, or None.

    Priority: COMFYUI_EXE env var → local_config.json → Start Menu shortcut → default path.
    """
    global _last_searched_paths
    _last_searched_paths = []

    # 1. DXT user setting (env var) — ignore unsubstituted "${user_config.*}" placeholders
    env_exe = _sanitize_path(os.environ.get("COMFYUI_EXE", ""))
    if env_exe and not env_exe.startswith("${"):
        _last_searched_paths.append(f"DXT setting: {env_exe}")
        if os.path.isfile(env_exe):
            log.info("Found ComfyUI exe from settings: %s", env_exe)
            return env_exe
        else:
            log.warning("COMFYUI_EXE from settings does not exist: %s", env_exe)

    # 2. Saved custom path from setup UI browse
    local_cfg = load_local_config()
    saved_exe = _sanitize_path(local_cfg.get("comfyui_exe", ""))
    if saved_exe:
        _last_searched_paths.append(f"Saved path: {saved_exe}")
        if os.path.isfile(saved_exe):
            log.info("Found ComfyUI exe from local config: %s", saved_exe)
            return saved_exe
        else:
            log.warning("Saved ComfyUI exe no longer exists: %s", saved_exe)

    # 3. Windows Start Menu shortcut
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            lnk_path = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "ComfyUI.lnk")
            if os.path.isfile(lnk_path):
                target = _resolve_lnk(lnk_path)
                if target:
                    target = _sanitize_path(target)
                    _last_searched_paths.append(f"Start Menu shortcut: {target}")
                    if os.path.isfile(target):
                        log.info("Found ComfyUI exe from Start Menu shortcut: %s", target)
                        # Save for future use so we don't re-parse the .lnk every time
                        local_cfg["comfyui_exe"] = target
                        save_local_config(local_cfg)
                        return target
                    else:
                        log.warning("Start Menu shortcut target does not exist: %s", target)

    # 4. Default platform path
    if COMFYUI_DEFAULT_EXE:
        _last_searched_paths.append(f"Default path: {COMFYUI_DEFAULT_EXE}")
    log.info("Looking for ComfyUI exe at default: %s", COMFYUI_DEFAULT_EXE)
    if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
        log.info("Found ComfyUI exe at default path: %s", COMFYUI_DEFAULT_EXE)
        return COMFYUI_DEFAULT_EXE

    log.info("ComfyUI exe not found. Searched: %s", _last_searched_paths)
    return None

def find_models_dir() -> str | None:
    """Find the ComfyUI models directory.

    Priority: explicit path in local_config.json (for portable ComfyUI) →
    ComfyUI Desktop's own config.json.
    """
    # 1. User override — portable installs, or custom location.
    local_cfg = load_local_config()
    saved_models_dir = _sanitize_path(local_cfg.get("models_dir", ""))
    if saved_models_dir:
        if os.path.isdir(saved_models_dir):
            log.info("Using models dir from local_config.json: %s", saved_models_dir)
            return saved_models_dir
        log.warning("Saved models_dir does not exist: %s", saved_models_dir)

    # 2. Read ComfyUI Desktop's own config
    log.info("Looking for ComfyUI config at: %s", COMFYUI_CONFIG_PATH)
    if COMFYUI_CONFIG_PATH and os.path.isfile(COMFYUI_CONFIG_PATH):
        try:
            with open(COMFYUI_CONFIG_PATH, encoding="utf-8") as f:
                config = json.load(f)
            base_path = config.get("basePath")
            log.info("ComfyUI config basePath: %s", base_path)
            if base_path:
                models_dir = os.path.join(base_path, "models")
                if os.path.isdir(models_dir):
                    log.info("Models dir from config: %s", models_dir)
                    return models_dir
                else:
                    log.warning("Models dir from config does not exist: %s", models_dir)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read ComfyUI config: %s", e)
    else:
        log.info("ComfyUI config file not found")

    # No config.json found — we can't reliably guess the models path.
    # The user needs to run ComfyUI Desktop at least once so it creates its config.
    log.info("No ComfyUI config found — cannot determine models directory. Please run ComfyUI Desktop at least once.")
    return None


PORT_SCAN_START = 8000
PORT_SCAN_END = 8010

COMFYUI_PROCESS_NAMES = {"ComfyUI.exe", "ComfyUI"}


def _is_comfyui_process_running() -> bool:
    """Check if a ComfyUI process is running (fast, no network)."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ComfyUI.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "ComfyUI.exe" in result.stdout
        else:
            result = subprocess.run(
                ["pgrep", "-f", "ComfyUI"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
    except Exception as e:
        log.debug("Process check failed: %s", e)
        return False  # assume not running if we can't check


def _check_url(url: str) -> bool:
    """Check if ComfyUI is responding at a specific URL."""
    try:
        status = httpx.get(f"{url}/system_stats", timeout=2).status_code
        return status == 200
    except Exception:
        return False


def _scan_ports(custom_url: str | None = None) -> str | None:
    """Scan for ComfyUI on ports 8000-8010 (or check custom URL only)."""
    if custom_url and custom_url != COMFYUI_DEFAULT_URL:
        if _check_url(custom_url):
            log.info("ComfyUI found at custom URL: %s", custom_url)
            return custom_url
        return None

    for port in range(PORT_SCAN_START, PORT_SCAN_END + 1):
        url = f"http://127.0.0.1:{port}"
        if _check_url(url):
            log.info("ComfyUI found at %s", url)
            return url
    return None


def find_comfyui_url(custom_url: str | None = None) -> str | None:
    """Find a running ComfyUI instance.

    If a custom URL is configured (different from the default), check it directly —
    the user has told us where ComfyUI lives, so skip the process-name fast path
    which only matches ComfyUI Desktop and breaks for portable/standalone installs.

    Otherwise, gate the port scan on a cheap process-name check.
    """
    if custom_url and custom_url != COMFYUI_DEFAULT_URL:
        if _check_url(custom_url):
            log.info("ComfyUI found at custom URL: %s", custom_url)
            return custom_url
        log.info("ComfyUI not responding at configured URL: %s", custom_url)
        return None

    if not _is_comfyui_process_running():
        log.info("ComfyUI process not running, skipping port scan")
        return None

    log.info("ComfyUI process detected, scanning for URL...")
    url = _scan_ports(custom_url)
    if url:
        return url

    log.info("ComfyUI process running but not responding on ports %d-%d", PORT_SCAN_START, PORT_SCAN_END)
    return None


_object_info_cache: dict | None = None


def check_required_nodes(url: str, required: dict[str, str]) -> list[str]:
    """Check if required ComfyUI custom nodes are installed.

    Args:
        url: ComfyUI base URL
        required: {class_type: package_name} mapping

    Returns:
        List of missing package names (empty if all present).
    """
    global _object_info_cache
    if _object_info_cache is None:
        try:
            resp = httpx.get(f"{url}/object_info", timeout=10)
            resp.raise_for_status()
            _object_info_cache = resp.json()
            log.info("Loaded object_info: %d node types available", len(_object_info_cache))
        except Exception as e:
            log.error("Failed to fetch /object_info from %s: %s", url, e)
            return []  # can't check, let it fail later with a ComfyUI error

    missing = []
    for class_type, package_name in required.items():
        if class_type not in _object_info_cache:
            log.warning("Required node %s (%s) not found in ComfyUI", class_type, package_name)
            if package_name not in missing:
                missing.append(package_name)
    return missing


def check_model_exists(models_dir: str, subfolder: str, filename: str) -> bool:
    """Check if a model file exists in the expected location."""
    path = os.path.join(models_dir, subfolder, filename)
    exists = os.path.isfile(path)
    log.debug("Model check: %s -> %s", path, "EXISTS" if exists else "MISSING")
    return exists


def _minimize_comfyui_window():
    """Wait for the ComfyUI Desktop window to appear, then hide it (Windows only)."""
    if platform.system() != "Windows":
        return

    import ctypes
    user32 = ctypes.windll.user32

    SW_HIDE = 0
    WINDOW_TITLES = [b"ComfyUI", b"Comfy"]

    for _ in range(60):
        for title in WINDOW_TITLES:
            hwnd = user32.FindWindowA(None, title)
            if hwnd:
                user32.ShowWindow(hwnd, SW_HIDE)
        time.sleep(0.5)


def launch_comfyui(exe_path: str, custom_url: str | None = None) -> tuple[subprocess.Popen, str]:
    """Spawn ComfyUI Desktop and wait until it's ready (up to 120s).

    Returns (process, url) — the URL may differ from the default if ComfyUI
    picked a different port.
    """
    log.info("Launching ComfyUI Desktop: %s", exe_path)
    # Only redirect stdout to DEVNULL. stderr must stay inherited —
    # redirecting it to DEVNULL breaks tqdm/ComfyUI Manager's stderr hook on Windows.
    proc = subprocess.Popen(
        [exe_path], stdout=subprocess.DEVNULL
    )
    threading.Thread(target=_minimize_comfyui_window, daemon=True).start()

    for i in range(120):
        found_url = _scan_ports(custom_url)
        if found_url:
            log.info("ComfyUI is ready at %s (took ~%ds).", found_url, i)
            return proc, found_url
        if i % 10 == 0 and i > 0:
            log.info("Still waiting for ComfyUI to start... (%ds)", i)
        time.sleep(1)

    log.error("ComfyUI failed to start within 120 seconds, killing process")
    proc.kill()
    raise TimeoutError("ComfyUI did not start within 120 seconds.")


def upload_image(comfyui_url: str, file_path: str) -> str:
    """Upload a local image to ComfyUI's input directory. Returns the uploaded filename."""
    log.info("Uploading image to ComfyUI: %s", file_path)
    with open(file_path, "rb") as f:
        resp = httpx.post(
            f"{comfyui_url}/upload/image",
            files={"image": (os.path.basename(file_path), f)},
            timeout=30,
        )
    resp.raise_for_status()
    name = resp.json()["name"]
    log.info("Uploaded as: %s", name)
    return name
