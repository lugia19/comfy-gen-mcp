"""Load and validate model pack JSON files."""

import json
import logging
import os

log = logging.getLogger("comfy-mcp")

REQUIRED_FIELDS = ["name", "display_name", "tool_name", "tool_description", "models", "workflow", "prompt_node_id", "seed_nodes", "dimension_nodes"]


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
