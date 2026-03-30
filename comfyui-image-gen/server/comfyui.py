"""ComfyUI Desktop detection, launching, and health checks."""

import json
import logging
import os
import platform
import subprocess
import threading
import time

import httpx

from .config import COMFYUI_CONFIG_PATH, COMFYUI_DEFAULT_EXE, COMFYUI_DEFAULT_URL, load_local_config, save_local_config

log = logging.getLogger("comfy-mcp")


def find_comfyui_install() -> str | None:
    """Return the path to the ComfyUI Desktop executable, or None.

    Priority: COMFYUI_EXE env var (DXT settings) → local_config.json (browse) → default path.
    """
    # 1. DXT user setting (env var) — ignore unsubstituted "${user_config.*}" placeholders
    env_exe = os.environ.get("COMFYUI_EXE", "").strip()
    if env_exe and not env_exe.startswith("${"):
        if os.path.isfile(env_exe):
            log.info("Found ComfyUI exe from settings: %s", env_exe)
            return env_exe
        else:
            log.warning("COMFYUI_EXE from settings does not exist: %s", env_exe)

    # 2. Saved custom path from setup UI browse
    local_cfg = load_local_config()
    saved_exe = local_cfg.get("comfyui_exe", "")
    if saved_exe:
        if os.path.isfile(saved_exe):
            log.info("Found ComfyUI exe from local config: %s", saved_exe)
            return saved_exe
        else:
            log.warning("Saved ComfyUI exe no longer exists: %s", saved_exe)

    # 3. Default platform path
    log.info("Looking for ComfyUI exe at default: %s", COMFYUI_DEFAULT_EXE)
    if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
        log.info("Found ComfyUI exe at default path: %s", COMFYUI_DEFAULT_EXE)
        return COMFYUI_DEFAULT_EXE

    log.info("ComfyUI exe not found")
    return None

def find_models_dir() -> str | None:
    """Find the ComfyUI models directory using its config.json (Amendment 1)."""
    # Primary: read ComfyUI's own config
    log.info("Looking for ComfyUI config at: %s", COMFYUI_CONFIG_PATH)
    if COMFYUI_CONFIG_PATH and os.path.isfile(COMFYUI_CONFIG_PATH):
        try:
            with open(COMFYUI_CONFIG_PATH) as f:
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

    First checks if the ComfyUI process is running (fast). If not, returns None
    immediately without scanning ports. If yes, scans ports to find the URL.
    """
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
