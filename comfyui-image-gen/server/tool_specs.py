"""Single source of truth for the MCP tool list (names, descriptions, input schemas).

Both the HTTP server (server.main.register_tools) and the stdio shim (server.shim) need to
agree on exactly which tools exist and how they're described. The shim must report this list
*before* the HTTP server is even up (Claude Desktop caches the first list_tools result and
won't refresh mid-session), so the list has to be computable locally — from the bundled pack
JSONs plus local_config.json — with no ComfyUI, no Qt, and no running server.

Keep this module dependency-light: it imports only model_pack + config (both leaf-light).
Do NOT import PyQt6, comfyui, or FastMCP here, or the thin shim stops being thin.
"""

import logging
import os

from server.config import MODEL_PACKS_DIR, load_local_config
from server.model_pack import group_packs_by_tool, load_all_packs, resolve_pack_selections

log = logging.getLogger("comfy-mcp")


# ── Input schemas (mirror the handler signatures in server.main.register_tools) ──

_ASPECT_PROP = {
    "type": "string",
    "enum": ["square", "portrait", "landscape", "tall", "wide"],
    "default": "square",
    "description": "Image shape: square (1:1), portrait (3:4), landscape (4:3), tall (9:16), wide (16:9).",
}

# generate_* tools: handler(prompt: str, aspect_ratio: str = "square")
GENERATION_SCHEMA = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}, "aspect_ratio": _ASPECT_PROP},
    "required": ["prompt"],
}

# edit_image(prompt: str, image_path: str, second_image_path: str = "")
EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "image_path": {"type": "string"},
        "second_image_path": {"type": "string"},
    },
    "required": ["prompt", "image_path"],
}

# fetch_result(request_token: str)
FETCH_SCHEMA = {
    "type": "object",
    "properties": {"request_token": {"type": "string"}},
    "required": ["request_token"],
}


# ── Static tool descriptions (the three tools that aren't model packs) ──

CUSTOM_DESC = (
    "Generate an image using a user-provided custom ComfyUI workflow. "
    "This tool only works if the user has configured a custom workflow path in the extension settings. "
    "Use natural language to describe the image. "
    "The aspect_ratio parameter controls image shape: "
    "square (1:1), portrait (3:4), landscape (4:3), tall (9:16), wide (16:9). Default is square."
)

FETCH_DESC = (
    "Fetch the result of an image generation that is still in progress. "
    "Use this when a generation tool returns a request_token instead of an image."
)

EDIT_DESC = (
    "Edit an image using a text prompt. "
    "image_path can be a local file path (e.g. C:/Users/me/photo.png) or a publicly accessible URL. "
    "Previously generated images return their saved_path — use that.\n\n"
    "If the user uploads an image to the chat to be edited, ask them to provide either "
    "the file path on their local machine or a public URL to the image instead, "
    "as uploaded chat images cannot be accessed directly. The URL must not require login to access. "
    "On Windows, the user can get a file's path by holding Shift, right-clicking the file, and selecting 'Copy as path'. "
    "Suggest https://litterbox.catbox.moe/ as a free temporary file host if they need one.\n\n"
    "Optionally provide second_image_path to reference a second image. "
    "When using two images, refer to them as 'image1' and 'image2' in the prompt.\n\n"
    "IMPORTANT: The generated image may not appear inline in the conversation, but it IS sent to the user. "
    "Do not assume the generation failed just because you cannot see the image.\n\n"
    "Prompting tips:\n"
    "- Be precise and verbatim when describing desired changes (e.g. 'change the text to say \"Hello World\"')\n"
    "- For targeted edits, say 'change nothing else' and mention what should stay the same\n"
    "- Describe what you want the result to look like, not the editing operation"
)


def _env(key: str) -> str | None:
    """Read env var, treating unset / unsubstituted ${user_config.*} as None.

    Mirrors server.main._env so pack selection / artist overrides resolve identically.
    """
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("${"):
        return None
    return val


def resolve_tool_description(pack: dict, groups: dict[str, list[dict]], env_reader=None) -> str:
    """Final tool description for a resolved pack.

    Single implementation of the description rules, shared by the server and the shim:
      1. For a multi-pack tool group, use ``group_tool_description``.
      2. Substitute ``{artist_list}`` from ANIMA_ARTISTS env → pack_settings → default.
      3. Substitute ``{lora_triggers}`` with the configured trigger-gated LoRA words
         (pack_loras), or nothing when none are configured.
    """
    desc = pack["tool_description"]
    tool_name = pack["tool_name"]
    if len(groups.get(tool_name, [])) > 1 and pack.get("group_tool_description"):
        desc = pack["group_tool_description"]

    if pack.get("default_artist_list"):
        anima_artists = env_reader("ANIMA_ARTISTS") if env_reader else None
        artists_str = (
            anima_artists
            or load_local_config().get("pack_settings", {}).get(pack["name"], {}).get("artist_list")
            or pack["default_artist_list"]
        )
        parts = [a.strip() for a in artists_str.split(",") if a.strip()]
        if parts:
            preferred = parts[0]
            others = ", ".join(parts[1:]) if len(parts) > 1 else "none"
            artist_display = f"preferred default: {preferred}, others available: {others}"
        else:
            artist_display = artists_str
        desc = desc.replace("{artist_list}", artist_display)

    if "{lora_triggers}" in desc:
        loras = load_local_config().get("pack_loras", {}).get(pack["name"], [])
        triggers: list[str] = []
        for e in loras:
            trig = (e.get("trigger") or "").strip() if isinstance(e, dict) else ""
            if trig and trig not in triggers:  # dedupe, preserve order; skip always-on LoRAs
                triggers.append(trig)
        if triggers:
            lora_text = (
                "\n\nThe following trigger words will cause a LoRA to be applied to the prompt "
                "(these can be either artist styles, usually prefixed with @, or concept tags). "
                "These triggers must be used verbatim: " + ", ".join(triggers) + "."
            )
        else:
            lora_text = ""
        desc = desc.replace("{lora_triggers}", lora_text)

    return desc


# Tools defined statically below (not as model-pack generation tools). A pack whose
# tool_name collides with one of these (e.g. flux2klein_edit → "edit_image") is the backing
# data for that static tool, not its own generation tool — the server registers the static
# one and FastMCP drops the pack duplicate, so we skip it here to match exactly.
_STATIC_TOOL_NAMES = {"generate_custom_image", "edit_image", "fetch_result"}


def build_tool_specs(env_reader=None) -> list[dict]:
    """Compute the full tool list as ``[{name, description, inputSchema}]``.

    Derived entirely from the bundled packs + local_config.json — no ComfyUI / server / Qt.
    Always includes the three static tools, even if no packs load.
    """
    if env_reader is None:
        env_reader = _env

    specs: list[dict] = []
    try:
        groups = group_packs_by_tool(load_all_packs(MODEL_PACKS_DIR))
        for pack in resolve_pack_selections(groups, env_reader=env_reader):
            if pack["tool_name"] in _STATIC_TOOL_NAMES:
                continue  # e.g. the edit pack backs the static edit_image tool
            specs.append({
                "name": pack["tool_name"],
                "description": resolve_tool_description(pack, groups, env_reader),
                "inputSchema": GENERATION_SCHEMA,
            })
    except Exception as e:  # never let a bad pack take out the whole tool list
        log.error("Failed to build pack tool specs: %s", e)

    specs.append({"name": "generate_custom_image", "description": CUSTOM_DESC, "inputSchema": GENERATION_SCHEMA})
    specs.append({"name": "edit_image", "description": EDIT_DESC, "inputSchema": EDIT_SCHEMA})
    specs.append({"name": "fetch_result", "description": FETCH_DESC, "inputSchema": FETCH_SCHEMA})
    return specs
