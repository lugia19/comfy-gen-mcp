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

# User-facing config keys surfaced in local_config.json so users can discover
# and edit them. Keep in sync with manifest.json's user_config block.
USER_CONFIG_DEFAULTS = {
    "comfyui_url": COMFYUI_DEFAULT_URL,
    "custom_workflow": "",
    "custom_workflow_prompt_node": "",
    "anima_artists": "@cutesexyrobutts, @nyantcha, @bone nigi",
}


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
    for key, default in USER_CONFIG_DEFAULTS.items():
        if key not in cfg:
            cfg[key] = default
            changed = True
    return changed
