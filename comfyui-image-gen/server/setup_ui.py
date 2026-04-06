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

        from server.model_pack import load_all_packs, group_packs_by_tool
        packs = load_all_packs(packs_dir)
        if not packs:
            log.error("No model packs found in %s", packs_dir)
            sys.exit(1)
        groups = group_packs_by_tool(packs)

        from server.config import COMFYUI_DEFAULT_EXE, load_local_config
        need_comfyui = True
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            need_comfyui = False
        else:
            cfg = load_local_config()
            if cfg.get("comfyui_exe") and os.path.isfile(cfg.get("comfyui_exe", "")):
                need_comfyui = False

        from server.ui import run_first_time_setup
        run_first_time_setup(models_dir, packs, groups, need_comfyui)

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
