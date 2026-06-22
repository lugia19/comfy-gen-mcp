"""Workflow utilities — prompt injection, aspect ratio, and custom workflow loading."""

import copy
import json
import math
import random

ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "square":    (1, 1),
    "portrait":  (3, 4),
    "landscape": (4, 3),
    "tall":      (9, 16),
    "wide":      (16, 9),
}


def calc_dimensions(aspect: str, max_pixels: int) -> tuple[int, int]:
    """Compute width and height from an aspect ratio name and pixel budget.

    Rounds to the nearest multiple of 64 (ComfyUI latent alignment).
    """
    w_ratio, h_ratio = ASPECT_RATIOS.get(aspect, (1, 1))
    # scale = sqrt(max_pixels / (w_ratio * h_ratio))
    scale = math.sqrt(max_pixels / (w_ratio * h_ratio))
    w = round(w_ratio * scale / 64) * 64
    h = round(h_ratio * scale / 64) * 64
    return max(w, 64), max(h, 64)


def build_prompt(
    workflow: dict,
    prompt_text: str,
    prompt_node_id: str,
    seed_nodes: list[dict],
    dimension_nodes: dict | None = None,
    aspect_ratio: str = "square",
    max_pixels: int = 1_048_576,
    lora_toggles: list[dict] | None = None,
) -> dict:
    """Deep-copy workflow, inject prompt text, randomize seeds, and set dimensions.

    seed_nodes: [{"node_id": "19", "field": "seed"}, ...]
    dimension_nodes: {"width": [{"node_id": "28", "field": "width"}], "height": [...]}
    lora_toggles: [{"node_id": "58", "trigger": "@mychar", "strength": 0.8}, ...] — each
        conditional LoRA node is set to its strength only when its trigger is a
        case-insensitive substring of the prompt, otherwise disabled (strength 0).
    """
    wf = copy.deepcopy(workflow)
    wf[prompt_node_id]["inputs"]["text"] = prompt_text

    # Randomize seeds
    for sn in seed_nodes:
        nid, field = sn["node_id"], sn["field"]
        if nid in wf:
            wf[nid]["inputs"][field] = random.randint(0, 2**64 - 1)

    # Set dimensions
    if dimension_nodes:
        w, h = calc_dimensions(aspect_ratio, max_pixels)
        for patch in dimension_nodes.get("width", []):
            if patch["node_id"] in wf:
                wf[patch["node_id"]]["inputs"][patch["field"]] = w
        for patch in dimension_nodes.get("height", []):
            if patch["node_id"] in wf:
                wf[patch["node_id"]]["inputs"][patch["field"]] = h

    # Toggle trigger-gated LoRAs: active strength only if the trigger is in the prompt.
    if lora_toggles:
        lowered = prompt_text.lower()
        for tog in lora_toggles:
            nid = tog["node_id"]
            if nid not in wf:
                continue
            trigger = tog.get("trigger") or ""
            active = (not trigger) or (trigger.lower() in lowered)
            wf[nid]["inputs"]["strength_model"] = float(tog["strength"]) if active else 0.0

    return wf


# Model-loader class types and the output index that carries MODEL. LoRAs are applied to
# the model path only (CLIP is left untouched), so only model loaders matter here.
MODEL_LOADERS: dict[str, int] = {
    "UNETLoader": 0,
    "UnetLoaderGGUF": 0,
    "CheckpointLoaderSimple": 0,
    "CheckpointLoader": 0,
}


def _find_loader_source(workflow: dict, loaders: dict[str, int]) -> list | None:
    """Return the [node_id, output_index] link for the first matching loader node, or None."""
    for node_id, node in workflow.items():
        idx = loaders.get(node.get("class_type"))
        if idx is not None:
            return [node_id, idx]
    return None


def _next_node_id(workflow: dict) -> int:
    """Return an integer node id guaranteed not to collide with existing keys."""
    highest = 0
    for key in workflow:
        try:
            highest = max(highest, int(key))
        except (TypeError, ValueError):
            continue
    return highest + 1


def _consumers_of(workflow: dict, source: list) -> list[tuple[str, str]]:
    """Find every (node_id, input_key) whose input link equals *source*.

    A link in ComfyUI API format is a ``[node_id, output_index]`` pair, so matching
    on the exact pair pins it to the MODEL output of the loader.
    """
    matches = []
    for node_id, node in workflow.items():
        for key, val in node.get("inputs", {}).items():
            if isinstance(val, list) and len(val) == 2 and val[0] == source[0] and val[1] == source[1]:
                matches.append((node_id, key))
    return matches


def inject_loras(workflow: dict, loras: list[dict], target: dict | None = None) -> list[dict]:
    """Splice a chain of LoraLoaderModelOnly nodes onto the model path, mutating in place.

    Each entry in *loras* is ``{"name": str, "strength": float, "trigger": str}`` (strength
    defaults to 1.0; trigger is optional). LoRAs are applied to the **model only** — CLIP is
    never touched. The chain is inserted immediately after the model loader, and every
    downstream MODEL consumer is rewired to read from the chain's output.

    Each node's ``strength_model`` is set to the configured (active) strength. For LoRAs with
    a non-empty trigger, a toggle descriptor ``{"node_id", "trigger", "strength"}`` is
    collected and returned so ``build_prompt`` can disable the node (strength 0) per request
    when the trigger isn't present in the prompt. LoRAs without a trigger are always active
    and are not returned.

    *target* optionally overrides auto-detection: ``{"model": [id, idx]}``.

    Raises ValueError if no model source can be found.
    """
    if not loras:
        return []

    target = target or {}
    model_src = target.get("model") or _find_loader_source(workflow, MODEL_LOADERS)
    if model_src is None:
        raise ValueError(
            "Could not locate a model loader to attach LoRAs to. "
            f"Known loaders: {sorted(MODEL_LOADERS)}. "
            "Set a 'lora_target' override in the pack JSON if this workflow is non-standard."
        )

    # Record downstream consumers *before* splicing, so we don't rewire the new nodes.
    model_consumers = _consumers_of(workflow, model_src)

    next_id = _next_node_id(workflow)
    model_head = model_src
    toggles: list[dict] = []
    for lora in loras:
        strength = float(lora.get("strength", 1.0))
        trigger = str(lora.get("trigger") or "").strip()
        node_id = str(next_id)
        next_id += 1
        title = f"Load LoRA (injected{', trigger=' + trigger if trigger else ''})"
        workflow[node_id] = {
            "inputs": {
                "lora_name": lora["name"],
                "strength_model": strength,
                "model": model_head,
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": title},
        }
        model_head = [node_id, 0]
        if trigger:
            toggles.append({"node_id": node_id, "trigger": trigger, "strength": strength})

    # Rewire the original MODEL consumers to the end of the chain.
    for node_id, key in model_consumers:
        workflow[node_id]["inputs"][key] = model_head

    return toggles


def load_custom_workflow(path: str, prompt_node_title: str | None = None) -> tuple[dict, str, list[str]]:
    """Load a custom workflow JSON, returning (workflow, prompt_node_id, sampler_node_ids).

    If *prompt_node_title* is provided, the node whose ``_meta.title`` matches
    (case-insensitive) is used as the prompt node.  Otherwise auto-detection is
    attempted via KSampler tracing.
    """
    with open(path, encoding="utf-8") as f:
        wf = json.load(f)

    # Detect UI-format export (has top-level "nodes" list) vs API format (dict of node_id -> node dict)
    if not isinstance(wf, dict) or "nodes" in wf or not all(isinstance(v, dict) for v in wf.values()):
        raise ValueError(
            f"Workflow at {path} is not in API format. "
            f"In ComfyUI, enable dev mode (Settings > Enable Dev mode Options) and use "
            f"'Save (API Format)' / 'Export (API)' instead of the regular Save."
        )

    # Find all KSamplers (used for seed randomization)
    samplers = []
    for node_id, node in wf.items():
        if node.get("class_type") in ("KSampler", "KSamplerAdvanced"):
            samplers.append(node_id)

    # If the user explicitly provided a prompt node title, find the matching node
    if prompt_node_title:
        target = prompt_node_title.strip().lower()
        for node_id, node in wf.items():
            meta_title = node.get("_meta", {}).get("title", "").strip().lower()
            if meta_title == target:
                return wf, node_id, samplers
        available = [
            f"  {nid}: {n.get('_meta', {}).get('title', '(no title)')}"
            for nid, n in wf.items()
        ]
        raise ValueError(
            f"No node with title '{prompt_node_title}' found in {path}.\n"
            f"Available nodes:\n" + "\n".join(available)
        )

    # Auto-detection: requires at least one KSampler
    if not samplers:
        raise ValueError(
            f"No KSampler found in {path} and no prompt node title configured. "
            f"Please set the 'Custom Workflow Prompt Node Title' in extension settings."
        )

    # Priority 1: node explicitly named "prompt"
    for node_id, node in wf.items():
        meta_title = node.get("_meta", {}).get("title", "").strip().lower()
        if meta_title == "prompt":
            return wf, node_id, samplers

    # Priority 2: trace first KSampler's positive conditioning input
    first = samplers[0]
    prompt_nid = str(wf[first]["inputs"]["positive"][0])
    return wf, prompt_nid, samplers
