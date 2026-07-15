"""Cross-app ComfyUI model-sharing registry.

Apps that manage their own ComfyUI install (this program, comfy-gen-mcp, ...)
each drop one JSON file per install into ~/.comfy-registry/installs/ announcing
where their models dir lives. Every app then points its own install's
extra_model_paths.yaml at all the other entries, so models and LoRAs are
downloaded once and visible everywhere.

Per-install files (not one shared file) — each app only ever rewrites its own
entry, so there is no locking and no read-modify-write race. Staleness is
handled entirely on the consumer side: entries whose models dir no longer
exists are ignored (and a dangling extra_model_paths entry is harmless anyway).

This module is deliberately standalone and dependency-free: it is duplicated
verbatim into the other apps' repos rather than shared as a package.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

_DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".comfy-registry", "installs")


def canon_path(path: str) -> str:
    """Canonical form for path-equality checks (consumers dedupe with this too)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def publish(app_id: str, install_path: str, models_dir: str, root: str | None = None) -> str:
    """Announce this app's install. Keyed by install path, not app id, so two
    instances of the same app (dev + packaged) don't stomp each other's entry.
    Returns the entry file path."""
    root = root or _DEFAULT_ROOT
    os.makedirs(root, exist_ok=True)
    digest = hashlib.sha1(canon_path(install_path).encode("utf-8")).hexdigest()[:8]
    entry_path = os.path.join(root, f"{app_id}-{digest}.json")
    entry = {
        "app": app_id,
        "install_path": install_path,
        "models_dir": models_dir,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(entry_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    return entry_path


def shared_models_dirs(own_models_dir: str, root: str | None = None) -> list[str]:
    """All other registered installs' models dirs, existing ones only, sorted
    for stable yaml output. Own entry is excluded by path, not app id."""
    root = root or _DEFAULT_ROOT
    own = canon_path(own_models_dir)
    dirs: dict[str, str] = {}
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(root, name), encoding="utf-8") as f:
                models_dir = json.load(f).get("models_dir")
        except (OSError, json.JSONDecodeError):
            continue
        if not models_dir or not os.path.isdir(models_dir):
            continue
        canon = canon_path(models_dir)
        if canon != own:
            dirs.setdefault(canon, models_dir)
    return sorted(dirs.values())
