"""Declarative schema for the global, statically-known user settings.

Single source of truth for (a) the default values seeded into local_config.json and
(b) how the Settings panel renders each field. Dynamic, pack-derived settings — pack
selection, anima artists, anima LoRAs — are NOT here; the UI builds those from the loaded
model packs.

Each field is a dict:
  key         local_config.json key
  title       label shown in the panel
  description help text shown under the field
  type        "text" | "path" | "int" | "bool" — drives the widget
  default     default value (its native type)
  min, max    (int only) spin-box bounds
  seed        whether to seed into local_config defaults (default True). False = the
              setting is honoured when present but not written proactively, so existing
              first-run behaviour (e.g. the tunnel prompt) is preserved.
  advanced    grouped under an "Advanced" heading in the panel (default False)
"""

from server.config import COMFYUI_DEFAULT_URL

SETTINGS_SCHEMA: list[dict] = [
    {
        "key": "comfyui_url",
        "title": "ComfyUI URL",
        "description": "URL of your ComfyUI instance (leave default unless you have a custom setup).",
        "type": "text",
        "default": COMFYUI_DEFAULT_URL,
    },
    {
        "key": "custom_workflow",
        "title": "Custom Workflow Path",
        "description": (
            "Path to a ComfyUI workflow exported in API format (.json). If set, it adds a "
            "generate_custom_image tool that uses this workflow."
        ),
        "type": "path",
        "default": "",
    },
    {
        "key": "custom_workflow_prompt_node",
        "title": "Custom Workflow Prompt Node Title",
        "description": (
            "Title (_meta.title) of the node where the prompt text is injected.\n\n"
            "If empty, it's auto-detected from the first KSampler's positive input."
        ),
        "type": "text",
        "default": "",
    },
    {
        "key": "use_tunnel",
        "title": "Expose via Cloudflare tunnel",
        "description": (
            "Standalone HTTP mode only: serve the MCP endpoint over a public Cloudflare "
            "tunnel instead of localhost."
        ),
        "type": "bool",
        "default": False,
        "seed": False,
        "advanced": True,
    },
]


def get_defaults() -> dict:
    """Default values to seed into local_config.json (fields with seed != False)."""
    return {f["key"]: f["default"] for f in SETTINGS_SCHEMA if f.get("seed", True)}
