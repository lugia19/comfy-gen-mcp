"""
CLI entry point for setup UI — used as a subprocess by DXT mode.

    python -m server.setup_ui --comfyui
    python -m server.setup_ui --download <models_dir> <pack_json>
    python -m server.setup_ui --first-run <models_dir> <packs_dir>

Imports the actual UI from server.ui (PyQt6).
"""

import json
import logging
import os
import sys

_server_dir = os.path.dirname(os.path.abspath(__file__))
_lib_dir = os.path.join(_server_dir, "lib")
if os.path.isdir(_lib_dir):
    sys.path.insert(0, _lib_dir)
sys.path.insert(0, os.path.dirname(_server_dir))

log = logging.getLogger("comfy-mcp.setup_ui")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,
)


def main():
    if len(sys.argv) < 2:
        print("Usage: setup_ui.py --comfyui | --download <models_dir> <pack_json> | --first-run <models_dir> <packs_dir>", file=sys.stderr)
        sys.exit(1)

    # Check lockfile to prevent duplicate setup windows (Claude Desktop restarts the server)
    from server.config import _EXT_DIR
    lockfile = os.path.join(_EXT_DIR, ".setup_running.lock")
    if os.path.isfile(lockfile):
        import time
        try:
            age = time.time() - os.path.getmtime(lockfile)
            if age < 300:
                log.info("Setup lockfile exists (%.0fs old) — another setup is running, exiting.", age)
                sys.exit(0)
            else:
                log.warning("Stale setup lockfile (%.0fs old), ignoring.", age)
        except OSError:
            pass

    mode = sys.argv[1]

    if mode == "--comfyui":
        from server.config import COMFYUI_DEFAULT_EXE, load_local_config
        comfyui_ok = COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE)
        if not comfyui_ok:
            cfg = load_local_config()
            comfyui_ok = bool(cfg.get("comfyui_exe")) and os.path.isfile(cfg.get("comfyui_exe", ""))
        if comfyui_ok:
            log.info("ComfyUI already found, exiting.")
            sys.exit(0)
        from server.ui import run_comfyui_setup
        run_comfyui_setup()

    elif mode == "--download":
        if len(sys.argv) < 4:
            print("Usage: setup_ui.py --download <models_dir> <pack_json>", file=sys.stderr)
            sys.exit(1)
        models_dir = sys.argv[2]
        pack_path = sys.argv[3]
        with open(pack_path, encoding="utf-8") as f:
            pack = json.load(f)
        title = f"Downloading {pack.get('display_name', 'model')} files..."
        from server.ui import run_download_ui
        run_download_ui(models_dir, pack["models"], title)

    elif mode == "--first-run":
        if len(sys.argv) < 4:
            print("Usage: setup_ui.py --first-run <models_dir> <packs_dir>", file=sys.stderr)
            sys.exit(1)
        models_dir = sys.argv[2]
        packs_dir = sys.argv[3]
        packs = []
        if os.path.isdir(packs_dir):
            for fname in sorted(os.listdir(packs_dir)):
                if fname.endswith(".json"):
                    with open(os.path.join(packs_dir, fname), encoding="utf-8") as f:
                        packs.append(json.load(f))
        if not packs:
            log.error("No model packs found in %s", packs_dir)
            sys.exit(1)

        from server.config import COMFYUI_DEFAULT_EXE, load_local_config
        need_comfyui = True
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            need_comfyui = False
        else:
            cfg = load_local_config()
            if cfg.get("comfyui_exe") and os.path.isfile(cfg.get("comfyui_exe", "")):
                need_comfyui = False

        from server.ui import run_first_time_setup
        run_first_time_setup(models_dir, packs, need_comfyui)

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
