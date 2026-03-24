"""
Standalone tkinter setup window for ComfyUI Image Gen DXT.

Two modes:
    python -m server.setup_ui --comfyui
    python -m server.setup_ui --download <models_dir> <pack_json_path>

Exits with code 0 on success, 1 on error/cancel.
"""

import json
import logging
import os
import platform
import sys
import threading
import webbrowser

# Set up import path for subprocess execution.
_server_dir = os.path.dirname(os.path.abspath(__file__))
_lib_dir = os.path.join(_server_dir, "lib")
if os.path.isdir(_lib_dir):
    sys.path.insert(0, _lib_dir)
sys.path.insert(0, os.path.dirname(_server_dir))

from server.config import COMFYUI_DEFAULT_EXE, load_local_config, save_local_config
from server.downloader import DownloadState, download_models

log = logging.getLogger("comfy-mcp.setup_ui")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,
)


def _format_bytes(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_048_576:.0f} MB"
    return f"{n / 1024:.0f} KB"


def _all_models_present(models_dir: str, models: list[dict]) -> bool:
    for m in models:
        if not os.path.isfile(os.path.join(models_dir, m["subfolder"], m["filename"])):
            return False
    return True


def run_comfyui_setup(in_process: bool = False):
    """Show the ComfyUI detection/install screen."""
    log.info("Opening ComfyUI setup UI (in_process=%s)", in_process)
    import tkinter as tk

    root = tk.Tk()
    root.title("ComfyUI Image Gen — Setup")
    root.resizable(False, False)
    root.geometry("520x250")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - 520) // 2
    y = (root.winfo_screenheight() - 250) // 2
    root.geometry(f"+{x}+{y}")

    frame = tk.Frame(root, padx=20, pady=20)
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="ComfyUI Desktop is required but was not found.",
        font=("", 12),
        wraplength=460,
    ).pack(pady=(10, 20))

    def open_download():
        webbrowser.open("https://www.comfy.org/download")

    def browse_for_comfyui():
        from tkinter import filedialog
        if platform.system() == "Windows":
            filetypes = [("ComfyUI Executable", "ComfyUI.exe"), ("All files", "*.*")]
        else:
            filetypes = [("All files", "*")]
        path = filedialog.askopenfilename(title="Select ComfyUI executable", filetypes=filetypes)
        if path and os.path.isfile(path):
            log.info("User selected custom ComfyUI exe: %s", path)
            cfg = load_local_config()
            cfg["comfyui_exe"] = path
            save_local_config(cfg)
            status_label.config(text=f"Saved: {path}", fg="green")
            root.after(500, root.destroy)

    btn_frame = tk.Frame(frame)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="Download ComfyUI", command=open_download, width=20).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Browse...", command=browse_for_comfyui, width=20).pack(side="left", padx=5)

    status_label = tk.Label(frame, text="Waiting for ComfyUI to be installed...", fg="gray")
    status_label.pack(pady=(15, 0))

    def check_comfyui():
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            log.info("ComfyUI detected at %s", COMFYUI_DEFAULT_EXE)
            status_label.config(text="ComfyUI detected!", fg="green")
            root.after(500, root.destroy)
            return
        cfg = load_local_config()
        if cfg.get("comfyui_exe") and os.path.isfile(cfg["comfyui_exe"]):
            log.info("ComfyUI found via local config: %s", cfg["comfyui_exe"])
            status_label.config(text="ComfyUI detected!", fg="green")
            root.after(500, root.destroy)
            return
        root.after(5000, check_comfyui)

    root.after(1000, check_comfyui)
    if in_process:
        root.protocol("WM_DELETE_WINDOW", root.destroy)
    else:
        root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy(), sys.exit(1)))
    root.mainloop()


def run_first_time_setup(models_dir: str, packs: list[dict], need_comfyui: bool, in_process: bool = False):
    """First-run wizard: ComfyUI detection (if needed) → pack selection → download."""
    log.info("Opening first-time setup (need_comfyui=%s, %d packs)", need_comfyui, len(packs))
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("ComfyUI Image Gen — First Time Setup")
    root.resizable(False, False)
    root.geometry("520x400")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - 520) // 2
    y = (root.winfo_screenheight() - 400) // 2
    root.geometry(f"+{x}+{y}")

    # ── Screen 1: ComfyUI detection ─────────────────────────────
    comfyui_frame = tk.Frame(root, padx=20, pady=20)

    tk.Label(
        comfyui_frame,
        text="ComfyUI Desktop is required but was not found.",
        font=("", 12),
        wraplength=460,
    ).pack(pady=(10, 20))

    def open_download():
        webbrowser.open("https://www.comfy.org/download")

    def browse_for_comfyui():
        from tkinter import filedialog
        if platform.system() == "Windows":
            filetypes = [("ComfyUI Executable", "ComfyUI.exe"), ("All files", "*.*")]
        else:
            filetypes = [("All files", "*")]
        path = filedialog.askopenfilename(title="Select ComfyUI executable", filetypes=filetypes)
        if path and os.path.isfile(path):
            log.info("User selected custom ComfyUI exe: %s", path)
            cfg = load_local_config()
            cfg["comfyui_exe"] = path
            save_local_config(cfg)
            comfyui_status.config(text=f"Saved: {path}", fg="green")
            root.after(500, show_pack_selection)

    comfyui_btn_frame = tk.Frame(comfyui_frame)
    comfyui_btn_frame.pack(pady=5)
    tk.Button(comfyui_btn_frame, text="Download ComfyUI", command=open_download, width=20).pack(side="left", padx=5)
    tk.Button(comfyui_btn_frame, text="Browse...", command=browse_for_comfyui, width=20).pack(side="left", padx=5)

    comfyui_status = tk.Label(comfyui_frame, text="Waiting for ComfyUI to be installed...", fg="gray")
    comfyui_status.pack(pady=(15, 0))

    def check_comfyui():
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            log.info("ComfyUI detected at %s", COMFYUI_DEFAULT_EXE)
            comfyui_status.config(text="ComfyUI detected!", fg="green")
            root.after(500, show_pack_selection)
            return
        cfg = load_local_config()
        if cfg.get("comfyui_exe") and os.path.isfile(cfg["comfyui_exe"]):
            log.info("ComfyUI found via local config: %s", cfg["comfyui_exe"])
            comfyui_status.config(text="ComfyUI detected!", fg="green")
            root.after(500, show_pack_selection)
            return
        root.after(5000, check_comfyui)

    # ── Screen 2: Pack selection ────────────────────────────────
    select_frame = tk.Frame(root, padx=20, pady=20)

    tk.Label(
        select_frame,
        text="Select which image models to install:",
        font=("", 12),
    ).pack(pady=(5, 10))

    pack_vars: list[tuple[dict, tk.BooleanVar]] = []
    for pack in packs:
        var = tk.BooleanVar(value=True)  # default all selected
        total_size = sum(m["size_bytes"] for m in pack["models"])
        text = f"{pack['display_name']} — {pack.get('description', '')} ({_format_bytes(total_size)})"
        cb = tk.Checkbutton(select_frame, text=text, variable=var, anchor="w", wraplength=440)
        cb.pack(fill="x", padx=10, pady=2)
        pack_vars.append((pack, var))

    select_note = tk.Label(
        select_frame,
        text="You can always use models you didn't select — they'll download on first use.",
        fg="gray",
        wraplength=460,
    )
    select_note.pack(pady=(10, 5))

    def on_install():
        selected = [p for p, v in pack_vars if v.get()]
        if not selected:
            log.info("No packs selected, marking setup complete")
            cfg = load_local_config()
            cfg["setup_complete"] = True
            save_local_config(cfg)
            root.destroy()
            return

        # Check if any selected pack has artist config
        anima_pack = next((p for p in selected if p.get("default_artist_list")), None)
        if anima_pack:
            select_frame.pack_forget()
            show_artist_config(selected)
        else:
            select_frame.pack_forget()
            start_downloads(selected)

    tk.Button(select_frame, text="Install Selected", command=on_install, width=20).pack(pady=(10, 0))

    # ── Screen 2.5: Artist config (Anima only) ─────────────────
    artist_frame = tk.Frame(root, padx=20, pady=20)

    tk.Label(
        artist_frame,
        text="Anima Artist Styles",
        font=("", 12),
    ).pack(pady=(5, 10))

    tk.Label(
        artist_frame,
        text="Comma-separated list of @artist tags. The model will default to the first one.\nYou can change this later in Settings > Extensions > Configure.",
        wraplength=460,
        fg="gray",
    ).pack(pady=(0, 10))

    def open_style_explorer():
        webbrowser.open("https://thetacursed.github.io/Anima-Style-Explorer/index.html")

    tk.Button(artist_frame, text="Browse Styles", command=open_style_explorer, width=20).pack(pady=(0, 5))

    artist_entry = tk.Entry(artist_frame, width=60)
    artist_entry.pack(fill="x", padx=10, pady=5)

    def show_artist_config(selected):
        anima_pack = next((p for p in selected if p.get("default_artist_list")), None)
        artist_entry.insert(0, anima_pack["default_artist_list"])
        artist_frame.pack(fill="both", expand=True)

        def on_continue():
            value = artist_entry.get().strip()
            if value:
                cfg = load_local_config()
                cfg.setdefault("pack_settings", {}).setdefault("anima", {})["artist_list"] = value
                save_local_config(cfg)
                log.info("Saved anima artist_list: %s", value)
            artist_frame.pack_forget()
            start_downloads(selected)

        tk.Button(artist_frame, text="Continue", command=on_continue, width=20).pack(pady=(10, 0))

    def start_downloads(selected):
        all_models = []
        for pack in selected:
            for m in pack["models"]:
                if not any(existing["filename"] == m["filename"] and existing["subfolder"] == m["subfolder"] for existing in all_models):
                    all_models.append(m)
        log.info("Installing %d pack(s), %d unique model(s)", len(selected), len(all_models))
        show_download(all_models)

    # ── Screen 3: Download ──────────────────────────────────────
    dl_frame = tk.Frame(root, padx=20, pady=20)
    dl_state = DownloadState()

    dl_title_label = tk.Label(dl_frame, text="Downloading models...", font=("", 12))
    dl_title_label.pack(pady=(5, 15))

    dl_file_label = tk.Label(dl_frame, text="Preparing...", anchor="w")
    dl_file_label.pack(fill="x")

    dl_progress = ttk.Progressbar(dl_frame, length=460, mode="determinate")
    dl_progress.pack(fill="x", pady=(5, 5))

    dl_detail_label = tk.Label(dl_frame, text="", anchor="w", fg="gray")
    dl_detail_label.pack(fill="x")

    dl_error_label = tk.Label(dl_frame, text="", fg="red", wraplength=460)
    dl_error_label.pack(fill="x", pady=(10, 0))

    dl_retry_btn = tk.Button(dl_frame, text="Retry", state="disabled")
    dl_retry_btn.pack(pady=(10, 0))

    download_thread: list = []

    def start_download_thread(model_list):
        if download_thread and download_thread[0].is_alive():
            return
        dl_state.update(status="idle", error=None)
        dl_retry_btn.config(state="disabled")
        dl_error_label.config(text="")
        t = threading.Thread(target=download_models, args=(models_dir, model_list, dl_state), daemon=True)
        download_thread.clear()
        download_thread.append(t)
        t.start()
        poll_dl()

    def poll_dl():
        snap = dl_state.snapshot()
        if snap["status"] == "downloading":
            idx = snap["file_index"]
            count = snap["file_count"]
            fname = snap["current_file"]
            cur = snap["current_bytes"]
            total = snap["total_bytes"]
            overall = snap["overall_bytes"]
            overall_total = snap["overall_total"]
            dl_file_label.config(text=f"File {idx+1}/{count}: {fname} ({_format_bytes(cur)} / {_format_bytes(total)})")
            if overall_total > 0:
                dl_progress["value"] = overall / overall_total * 100
                dl_detail_label.config(text=f"Overall: {_format_bytes(overall)} / {_format_bytes(overall_total)}")
            root.after(200, poll_dl)
        elif snap["status"] == "complete":
            log.info("First-time downloads complete")
            dl_file_label.config(text="All models downloaded!")
            dl_progress["value"] = 100
            dl_detail_label.config(text="Setup complete. This window will close automatically.")
            cfg = load_local_config()
            cfg["setup_complete"] = True
            save_local_config(cfg)
            root.after(1500, root.destroy)
        elif snap["status"] == "error":
            dl_error_label.config(text=snap["error"])
            dl_retry_btn.config(state="normal")
            root.after(200, poll_dl)
        else:
            root.after(200, poll_dl)

    def show_download(model_list):
        dl_frame.pack(fill="both", expand=True)
        dl_retry_btn.config(command=lambda: start_download_thread(model_list))
        start_download_thread(model_list)

    def show_pack_selection():
        comfyui_frame.pack_forget()
        select_frame.pack(fill="both", expand=True)

    # ── Start ───────────────────────────────────────────────────
    if need_comfyui:
        comfyui_frame.pack(fill="both", expand=True)
        root.after(1000, check_comfyui)
    else:
        show_pack_selection()

    if in_process:
        root.protocol("WM_DELETE_WINDOW", root.destroy)
    else:
        root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy(), sys.exit(1)))
    root.mainloop()


def run_download_ui(models_dir: str, models: list[dict], title: str):
    """Show the model download progress screen."""
    log.info("Opening download UI: title=%r, models_dir=%s, %d models", title, models_dir, len(models))

    if _all_models_present(models_dir, models):
        log.info("All models already present, nothing to download.")
        return

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("ComfyUI Image Gen — Setup")
    root.resizable(False, False)
    root.geometry("520x280")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - 520) // 2
    y = (root.winfo_screenheight() - 280) // 2
    root.geometry(f"+{x}+{y}")

    dl_state = DownloadState()
    frame = tk.Frame(root, padx=20, pady=20)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text=title, font=("", 12)).pack(pady=(5, 15))

    dl_file_label = tk.Label(frame, text="Preparing...", anchor="w")
    dl_file_label.pack(fill="x")

    dl_progress = ttk.Progressbar(frame, length=460, mode="determinate")
    dl_progress.pack(fill="x", pady=(5, 5))

    dl_detail_label = tk.Label(frame, text="", anchor="w", fg="gray")
    dl_detail_label.pack(fill="x")

    dl_error_label = tk.Label(frame, text="", fg="red", wraplength=460)
    dl_error_label.pack(fill="x", pady=(10, 0))

    retry_btn = tk.Button(frame, text="Retry", state="disabled")
    retry_btn.pack(pady=(10, 0))

    download_thread: list = []

    def start_download():
        if download_thread and download_thread[0].is_alive():
            log.warning("Download already running, ignoring")
            return
        log.info("Starting model download...")
        dl_state.update(status="idle", error=None)
        retry_btn.config(state="disabled")
        dl_error_label.config(text="")
        t = threading.Thread(target=download_models, args=(models_dir, models, dl_state), daemon=True)
        download_thread.clear()
        download_thread.append(t)
        t.start()
        poll_download()

    def poll_download():
        snap = dl_state.snapshot()

        if snap["status"] == "downloading":
            idx = snap["file_index"]
            count = snap["file_count"]
            fname = snap["current_file"]
            cur = snap["current_bytes"]
            total = snap["total_bytes"]
            overall = snap["overall_bytes"]
            overall_total = snap["overall_total"]

            dl_file_label.config(text=f"File {idx + 1}/{count}: {fname} ({_format_bytes(cur)} / {_format_bytes(total)})")
            if overall_total > 0:
                pct = overall / overall_total * 100
                dl_progress["value"] = pct
                dl_detail_label.config(text=f"Overall: {_format_bytes(overall)} / {_format_bytes(overall_total)}")
            root.after(200, poll_download)

        elif snap["status"] == "complete":
            log.info("All downloads complete, closing UI in 1.5s")
            dl_file_label.config(text="All models downloaded!")
            dl_progress["value"] = 100
            dl_detail_label.config(text="Setup complete. This window will close automatically.")
            root.after(1500, root.destroy)

        elif snap["status"] == "error":
            log.error("Download error: %s", snap["error"])
            dl_error_label.config(text=snap["error"])
            retry_btn.config(state="normal")
            root.after(200, poll_download)

        else:
            root.after(200, poll_download)

    retry_btn.config(command=start_download)
    start_download()

    root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy(), sys.exit(1)))
    root.mainloop()


def main():
    if len(sys.argv) < 2:
        print("Usage: setup_ui.py --comfyui | --download <models_dir> <pack_json> | --first-run <models_dir> <packs_dir>", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--comfyui":
        comfyui_ok = COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE)
        if not comfyui_ok:
            cfg = load_local_config()
            comfyui_ok = bool(cfg.get("comfyui_exe")) and os.path.isfile(cfg.get("comfyui_exe", ""))
        if comfyui_ok:
            log.info("ComfyUI already found, exiting.")
            sys.exit(0)
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
        run_download_ui(models_dir, pack["models"], title)

    elif mode == "--first-run":
        if len(sys.argv) < 4:
            print("Usage: setup_ui.py --first-run <models_dir> <packs_dir>", file=sys.stderr)
            sys.exit(1)
        models_dir = sys.argv[2]
        packs_dir = sys.argv[3]

        # Load all pack JSONs
        packs = []
        if os.path.isdir(packs_dir):
            for fname in sorted(os.listdir(packs_dir)):
                if fname.endswith(".json"):
                    with open(os.path.join(packs_dir, fname), encoding="utf-8") as f:
                        packs.append(json.load(f))

        if not packs:
            log.error("No model packs found in %s", packs_dir)
            sys.exit(1)

        # Check if ComfyUI is needed
        need_comfyui = True
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            need_comfyui = False
        else:
            cfg = load_local_config()
            if cfg.get("comfyui_exe") and os.path.isfile(cfg.get("comfyui_exe", "")):
                need_comfyui = False

        run_first_time_setup(models_dir, packs, need_comfyui)

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
