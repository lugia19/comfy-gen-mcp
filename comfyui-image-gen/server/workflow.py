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
) -> dict:
    """Deep-copy workflow, inject prompt text, randomize seeds, and set dimensions.

    seed_nodes: [{"node_id": "19", "field": "seed"}, ...]
    dimension_nodes: {"width": [{"node_id": "28", "field": "width"}], "height": [...]}
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

    return wf


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
