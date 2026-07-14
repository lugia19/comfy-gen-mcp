"""Constants for the Comfy-Gen-MCP DXT extension."""

import json
import os
import sys

EXTENSION_VERSION = "1.0.5"
COMFYUI_DEFAULT_URL = "http://127.0.0.1:8188"
COMFYUI_DEFAULT_PORT = 8188

MAX_IMAGE_SIZE = 1024
JPEG_QUALITY = 85

_BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXT_DIR = _BUNDLE_DIR

# Packs ship with the code, so they stay bundle-relative.
MODEL_PACKS_DIR = os.path.join(_BUNDLE_DIR, "model_packs")

# Config + logs must NOT live next to the code: with the self-updating mcpb the stdio shim
# (frozen extension bundle) and the HTTP server it launches (a git-pulled runtime checkout)
# run from different directories, yet must share one local_config.json so they agree on the
# mcp_path token. So both default to a stable per-user app-data dir, overridable via env
# (COMFY_CONFIG_PATH / COMFY_LOGS_DIR — the shim exports the former when it spawns the server
# so the two can't drift even if this default ever changes).
_APPDATA_DIR = os.path.join(os.path.expanduser("~"), ".comfy-gen-mcp")
# Legacy location (config used to live next to the code) — migrated on first read.
_LEGACY_CONFIG_PATH = os.path.join(_EXT_DIR, "local_config.json")

LOCAL_CONFIG_PATH = os.environ.get("COMFY_CONFIG_PATH") or os.path.join(_APPDATA_DIR, "local_config.json")

# All app logs we control live here together (server.log + comfyui.log), so the UI can
# surface them with a single "Open Logs Folder" button. Created on demand via ensure_logs_dir.
LOGS_DIR = os.environ.get("COMFY_LOGS_DIR") or os.path.join(_APPDATA_DIR, "logs")


def ensure_logs_dir() -> str:
    """Create the logs directory if needed and return its path."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    return LOGS_DIR

# Per-pack config containers seeded into local_config.json (not rendered as scalar form
# fields; the Settings panel builds these from the loaded packs):
#   pack_loras       {pack_name: [{"name": "myLora.safetensors", "strength": 0.8}]}  (anima only)
#   pack_selections  {tool_name: pack_name}  (which pack a multi-pack tool uses)
#   pack_settings    {pack_name: {"artist_list": "..."}}
_CONTAINER_DEFAULTS = {
    "pack_loras": {},
    "pack_selections": {},
    "pack_settings": {},
}


def get_user_config_defaults() -> dict:
    """Default values for every user-configurable key. Global scalar settings come from the
    settings schema (the single source of truth); per-pack containers are added here."""
    from server.settings import get_defaults  # lazy: avoids settings<->config import cycle
    return {**get_defaults(), **_CONTAINER_DEFAULTS}


def is_http_mode() -> bool:
    """Check if we're running in HTTP connector mode (--http flag)."""
    return "--http" in sys.argv


def _migrate_legacy_config() -> None:
    """One-time move of a pre-existing bundle-relative local_config.json to the app-data dir,
    so users upgrading from the old layout keep their settings (and mcp_path)."""
    if os.environ.get("COMFY_CONFIG_PATH"):
        return  # explicit override — don't second-guess it
    if os.path.isfile(LOCAL_CONFIG_PATH) or not os.path.isfile(_LEGACY_CONFIG_PATH):
        return
    try:
        os.makedirs(os.path.dirname(LOCAL_CONFIG_PATH), exist_ok=True)
        with open(_LEGACY_CONFIG_PATH, encoding="utf-8") as src:
            data = src.read()
        with open(LOCAL_CONFIG_PATH, "w", encoding="utf-8") as dst:
            dst.write(data)
    except OSError:
        pass


def load_local_config() -> dict:
    _migrate_legacy_config()
    if os.path.isfile(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_local_config(config: dict):
    os.makedirs(os.path.dirname(LOCAL_CONFIG_PATH), exist_ok=True)
    with open(LOCAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def ensure_user_settings(cfg: dict) -> bool:
    """Fill in any missing user-configurable keys with defaults. Returns True if cfg changed."""
    changed = False
    for key, default in get_user_config_defaults().items():
        if key not in cfg:
            cfg[key] = default
            changed = True
    return changed
