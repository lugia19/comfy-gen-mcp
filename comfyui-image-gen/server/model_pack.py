"""Load and validate model pack JSON files."""

import json
import logging
import os

log = logging.getLogger("comfy-mcp")

REQUIRED_FIELDS = ["name", "display_name", "tool_name", "tool_description", "models", "workflow", "prompt_node_id", "seed_nodes"]


def load_model_pack(path: str) -> dict:
    """Load and validate a single model pack JSON file."""
    log.info("Loading model pack: %s", path)
    with open(path, encoding="utf-8") as f:
        pack = json.load(f)

    missing = [f for f in REQUIRED_FIELDS if f not in pack]
    if missing:
        raise ValueError(f"Model pack {path} missing required fields: {missing}")

    pack["_source_path"] = os.path.abspath(path)
    log.info("  Pack '%s': tool=%s, %d model(s)", pack["name"], pack["tool_name"], len(pack["models"]))
    return pack


def load_all_packs(packs_dir: str) -> list[dict]:
    """Load all .json model pack files from a directory."""
    packs = []
    if not os.path.isdir(packs_dir):
        log.warning("Model packs directory not found: %s", packs_dir)
        return packs

    for filename in sorted(os.listdir(packs_dir)):
        if not filename.endswith(".json"):
            continue
        try:
            pack = load_model_pack(os.path.join(packs_dir, filename))
            packs.append(pack)
        except Exception as e:
            log.error("Failed to load model pack %s: %s", filename, e)

    log.info("Loaded %d model pack(s)", len(packs))
    return packs


def check_models_present(models_dir: str, pack: dict) -> bool:
    """Check if all of a model pack's models exist in models_dir."""
    for m in pack["models"]:
        if not os.path.isfile(os.path.join(models_dir, m["subfolder"], m["filename"])):
            return False
    return True


def get_missing_models(models_dir: str, pack: dict) -> list[dict]:
    """Return list of model definitions that are not yet downloaded."""
    missing = []
    for m in pack["models"]:
        if not os.path.isfile(os.path.join(models_dir, m["subfolder"], m["filename"])):
            missing.append(m)
    return missing


def group_packs_by_tool(packs: list[dict]) -> dict[str, list[dict]]:
    """Group model packs by tool_name. Returns {tool_name: [pack, ...]}."""
    groups: dict[str, list[dict]] = {}
    for pack in packs:
        groups.setdefault(pack["tool_name"], []).append(pack)
    return groups


def resolve_pack_selections(groups: dict[str, list[dict]], env_reader=None) -> list[dict]:
    """Pick one pack per tool_name group based on user config.

    Resolution order:
    1. Env var PACK_SELECT_{TOOL_NAME_UPPER} (DXT settings override)
    2. local_config.json pack_selections[tool_name]
    3. Pack with is_default: true
    4. First pack in group
    """
    from server.config import load_local_config
    local_cfg = load_local_config()
    selections = local_cfg.get("pack_selections", {})

    resolved = []
    for tool_name, group in groups.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue

        # 1. Env var override
        env_key = f"PACK_SELECT_{tool_name.upper()}"
        env_val = env_reader(env_key) if env_reader else None
        if env_val:
            match = next((p for p in group if p["name"] == env_val), None)
            if match:
                log.info("Pack '%s' selected for %s via env var", match["name"], tool_name)
                resolved.append(match)
                continue

        # 2. local_config.json
        cfg_val = selections.get(tool_name)
        if cfg_val:
            match = next((p for p in group if p["name"] == cfg_val), None)
            if match:
                log.info("Pack '%s' selected for %s via local_config", match["name"], tool_name)
                resolved.append(match)
                continue

        # 3. is_default flag
        default = next((p for p in group if p.get("is_default")), None)
        if default:
            log.info("Pack '%s' selected for %s as default", default["name"], tool_name)
            resolved.append(default)
            continue

        # 4. First pack
        log.info("Pack '%s' selected for %s as first in group", group[0]["name"], tool_name)
        resolved.append(group[0])

    return resolved
