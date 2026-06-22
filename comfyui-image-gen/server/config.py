"""Constants for the Comfy-Gen-MCP DXT extension."""

import json
import os
import sys

EXTENSION_VERSION = "1.0.3"
COMFYUI_DEFAULT_URL = "http://127.0.0.1:8188"
COMFYUI_DEFAULT_PORT = 8188

MAX_IMAGE_SIZE = 1024
JPEG_QUALITY = 85

_BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXT_DIR = _BUNDLE_DIR

MODEL_PACKS_DIR = os.path.join(_BUNDLE_DIR, "model_packs")
LOCAL_CONFIG_PATH = os.path.join(_EXT_DIR, "local_config.json")

# All app logs we control live here together (server.log + comfyui.log), so the UI can
# surface them with a single "Open Logs Folder" button. Created on demand via ensure_logs_dir.
LOGS_DIR = os.path.join(_EXT_DIR, "logs")


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


def load_local_config() -> dict:
    if os.path.isfile(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_local_config(config: dict):
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
