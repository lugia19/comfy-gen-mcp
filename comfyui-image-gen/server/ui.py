"""PyQt6 UI for Comfy-Gen-MCP — setup wizards, dialogs, and server windows."""

import json
import logging
import os
import platform
import sys
import threading
import webbrowser

import threading as _threading
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from server.config import COMFYUI_DEFAULT_EXE, EXTENSION_VERSION, load_local_config, save_local_config

log = logging.getLogger("comfy-mcp")


def _get_icon_path() -> str | None:
    """Get the path to the tray/app icon."""
    if getattr(sys, "frozen", False):
        path = os.path.join(sys._MEIPASS, "server", "tray_icon.png")
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_icon.png")
    return path if os.path.isfile(path) else None


def _get_app() -> QApplication:
    """Get or create the QApplication singleton."""
    app = QApplication.instance()
    if app is None:
        # Windows: set app user model ID so taskbar shows our icon, not Python's
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.lugia19.comfyui-image-gen")

        app = QApplication(sys.argv)
        _apply_dark_theme(app)

        # Set application-wide icon
        icon_path = _get_icon_path()
        if icon_path:
            app.setWindowIcon(QIcon(icon_path))
    return app


def _apply_dark_theme(app: QApplication):
    """Apply a dark color palette to the application."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
    app.setPalette(palette)


# ── 1. ComfyUI Detection ─────────────────────────────────────────────

def run_comfyui_setup(in_process: bool = False):
    """Show ComfyUI detection/install dialog."""
    log.info("Opening ComfyUI setup UI (in_process=%s)", in_process)
    app = _get_app()

    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP — Setup")
    dialog.setMinimumWidth(520)

    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("<h3>ComfyUI Desktop is required but was not found.</h3>"))

    btn_layout = QHBoxLayout()
    download_btn = QPushButton("Download ComfyUI")
    download_btn.clicked.connect(lambda: webbrowser.open("https://www.comfy.org/download"))
    browse_btn = QPushButton("Browse...")

    def on_browse():
        if platform.system() == "Windows":
            path, _ = QFileDialog.getOpenFileName(dialog, "Select ComfyUI executable", "", "ComfyUI (ComfyUI.exe);;All Files (*)")
        else:
            path, _ = QFileDialog.getOpenFileName(dialog, "Select ComfyUI executable", "", "All Files (*)")
        if path and os.path.isfile(path):
            log.info("User selected custom ComfyUI exe: %s", path)
            cfg = load_local_config()
            cfg["comfyui_exe"] = path
            save_local_config(cfg)
            status_label.setText(f"Saved: {path}")
            status_label.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, dialog.accept)

    browse_btn.clicked.connect(on_browse)
    btn_layout.addWidget(download_btn)
    btn_layout.addWidget(browse_btn)
    layout.addLayout(btn_layout)

    status_label = QLabel("Waiting for ComfyUI to be installed...")
    status_label.setStyleSheet("color: gray;")
    layout.addWidget(status_label)

    def check_comfyui():
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            log.info("ComfyUI detected at %s", COMFYUI_DEFAULT_EXE)
            status_label.setText("ComfyUI detected!")
            status_label.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, dialog.accept)
            return
        cfg = load_local_config()
        if cfg.get("comfyui_exe") and os.path.isfile(cfg["comfyui_exe"]):
            log.info("ComfyUI found via local config: %s", cfg["comfyui_exe"])
            status_label.setText("ComfyUI detected!")
            status_label.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, dialog.accept)
            return

    timer = QTimer(dialog)
    timer.timeout.connect(check_comfyui)
    timer.start(5000)
    QTimer.singleShot(1000, check_comfyui)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        log.info("ComfyUI setup cancelled by user")
        sys.exit(0)


# ── 2. First-Time Setup Wizard ───────────────────────────────────────

def run_first_time_setup(models_dir: str, packs: list[dict], groups: dict[str, list[dict]], need_comfyui: bool, in_process: bool = False):
    """First-run wizard: ComfyUI detection → pack selection → artist config → download."""
    log.info("Opening first-time setup (need_comfyui=%s, %d packs, %d groups)", need_comfyui, len(packs), len(groups))
    from server.comfyui import find_models_dir
    from server.downloader import DownloadState, download_models

    app = _get_app()
    state = {"models_dir": models_dir}

    wizard = QDialog()
    wizard.setWindowTitle("Comfy-Gen-MCP — First Time Setup")
    wizard.setMinimumWidth(520)
    wizard.setMinimumHeight(400)

    main_layout = QVBoxLayout(wizard)
    stack = QWidget()
    stack_layout = QVBoxLayout(stack)
    main_layout.addWidget(stack)

    pages: list[QWidget] = []
    current_page = [0]

    def show_page(idx):
        for i, p in enumerate(pages):
            p.setVisible(i == idx)
        current_page[0] = idx

    # ── Page 0: ComfyUI detection ──
    comfyui_page = QWidget()
    cl = QVBoxLayout(comfyui_page)
    cl.addWidget(QLabel("<h3>ComfyUI Desktop is required but was not found.</h3>"))

    cbtn_layout = QHBoxLayout()
    dl_btn = QPushButton("Download ComfyUI")
    dl_btn.clicked.connect(lambda: webbrowser.open("https://www.comfy.org/download"))
    br_btn = QPushButton("Browse...")

    def on_browse_comfyui():
        if platform.system() == "Windows":
            path, _ = QFileDialog.getOpenFileName(wizard, "Select ComfyUI executable", "", "ComfyUI (ComfyUI.exe);;All Files (*)")
        else:
            path, _ = QFileDialog.getOpenFileName(wizard, "Select ComfyUI executable", "", "All Files (*)")
        if path and os.path.isfile(path):
            cfg = load_local_config()
            cfg["comfyui_exe"] = path
            save_local_config(cfg)
            comfyui_status.setText(f"Saved: {path}")
            comfyui_status.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, lambda: _advance_from_comfyui())

    br_btn.clicked.connect(on_browse_comfyui)
    cbtn_layout.addWidget(dl_btn)
    cbtn_layout.addWidget(br_btn)
    cl.addLayout(cbtn_layout)
    comfyui_status = QLabel("Waiting for ComfyUI to be installed...")
    comfyui_status.setStyleSheet("color: gray;")
    cl.addWidget(comfyui_status)
    cl.addStretch()
    stack_layout.addWidget(comfyui_page)
    pages.append(comfyui_page)

    def check_comfyui_auto():
        if COMFYUI_DEFAULT_EXE and os.path.isfile(COMFYUI_DEFAULT_EXE):
            comfyui_status.setText("ComfyUI detected!")
            comfyui_status.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, lambda: _advance_from_comfyui())
            return
        cfg = load_local_config()
        if cfg.get("comfyui_exe") and os.path.isfile(cfg["comfyui_exe"]):
            comfyui_status.setText("ComfyUI detected!")
            comfyui_status.setStyleSheet("color: #2a82da;")
            QTimer.singleShot(500, lambda: _advance_from_comfyui())

    comfyui_timer = QTimer(wizard)
    comfyui_timer.timeout.connect(check_comfyui_auto)
    comfyui_timer.start(5000)

    # ── Page 1: "Run ComfyUI first" (if no models_dir) ──
    run_first_page = QWidget()
    rfl = QVBoxLayout(run_first_page)
    rfl.addWidget(QLabel("<h3>ComfyUI needs to be run at least once.</h3>"))
    rfl.addWidget(QLabel("Please open ComfyUI Desktop, complete its initial setup,\nthen click the button below."))
    run_first_status = QLabel("")
    run_first_status.setStyleSheet("color: red;")
    rfl.addWidget(run_first_status)
    check_again_btn = QPushButton("I've run ComfyUI — check again")

    def on_check_models_dir():
        resolved = find_models_dir()
        if resolved:
            state["models_dir"] = resolved
            log.info("Models dir resolved: %s", resolved)
            show_page(2)  # pack selection
        else:
            run_first_status.setText("Models directory still not found. Please run ComfyUI first.")

    check_again_btn.clicked.connect(on_check_models_dir)
    rfl.addWidget(check_again_btn)
    rfl.addStretch()
    stack_layout.addWidget(run_first_page)
    pages.append(run_first_page)

    def _advance_from_comfyui():
        comfyui_timer.stop()
        resolved = find_models_dir()
        if resolved:
            state["models_dir"] = resolved
            show_page(2)  # pack selection
        else:
            show_page(1)  # run ComfyUI first

    # ── Page 2: Pack selection ──
    select_page = QWidget()
    sl = QVBoxLayout(select_page)
    sl.addWidget(QLabel("<h3>Select which image models to install:</h3>"))

    def _format_bytes(n):
        if n >= 1_000_000_000:
            return f"{n / 1_073_741_824:.1f} GB"
        if n >= 1_000_000:
            return f"{n / 1_048_576:.0f} MB"
        return f"{n / 1024:.0f} KB"

    # Build group-aware selection UI
    # For single-pack groups: checkbox (as before)
    # For multi-pack groups: radio buttons (pick one) + skip option
    pack_checkboxes: list[tuple[dict, QCheckBox]] = []  # single-pack groups
    radio_groups: list[tuple[str, QButtonGroup, list[tuple[dict, QRadioButton]]]] = []  # multi-pack groups

    for tool_name, group in groups.items():
        if len(group) == 1:
            # Single pack — checkbox as before
            pack = group[0]
            total_size = sum(m["size_bytes"] for m in pack["models"])
            cb = QCheckBox(f"{pack['display_name']} ({_format_bytes(total_size)})")
            cb.setChecked(True)
            desc = QLabel(f"  {pack.get('description', '')}")
            desc.setWordWrap(True)
            desc.setStyleSheet("color: gray; margin-left: 20px; margin-bottom: 8px;")
            sl.addWidget(cb)
            sl.addWidget(desc)
            pack_checkboxes.append((pack, cb))
        else:
            # Multi-pack group — radio buttons
            # Derive a readable header from the tool_name
            header_text = tool_name.replace("generate_", "").replace("_", " ").title() + " Model"
            sl.addWidget(QLabel(f"<b>{header_text}:</b>"))

            btn_group = QButtonGroup(select_page)
            btn_group.setExclusive(True)
            group_radios: list[tuple[dict, QRadioButton]] = []

            # Pre-select user's previous choice if re-running setup
            existing_cfg = load_local_config()
            prev_selection = existing_cfg.get("pack_selections", {}).get(tool_name)

            for pack in group:
                total_size = sum(m["size_bytes"] for m in pack["models"])
                rb = QRadioButton(f"{pack['display_name']} ({_format_bytes(total_size)})")
                if prev_selection:
                    if pack["name"] == prev_selection:
                        rb.setChecked(True)
                elif pack.get("is_default"):
                    rb.setChecked(True)
                desc = QLabel(f"  {pack.get('description', '')}")
                desc.setWordWrap(True)
                desc.setStyleSheet("color: gray; margin-left: 20px; margin-bottom: 4px;")
                btn_group.addButton(rb)
                sl.addWidget(rb)
                sl.addWidget(desc)
                group_radios.append((pack, rb))

            # "Skip" option
            skip_rb = QRadioButton("Skip (download later)")
            skip_rb.setStyleSheet("color: gray;")
            btn_group.addButton(skip_rb)
            sl.addWidget(skip_rb)

            # If no pack has is_default, select the first one
            if not any(rb.isChecked() for _, rb in group_radios):
                group_radios[0][1].setChecked(True)

            radio_groups.append((tool_name, btn_group, group_radios))

    note = QLabel("You can always change your model selection later in Settings.")
    note.setStyleSheet("color: gray;")
    sl.addWidget(note)

    install_btn = QPushButton("Install Selected")

    def on_install():
        # Collect selected packs from checkboxes (single-pack groups)
        selected = [p for p, cb in pack_checkboxes if cb.isChecked()]

        # Collect selected packs from radio groups (multi-pack groups)
        # Also save pack_selections to local_config
        cfg = load_local_config()
        pack_selections = cfg.get("pack_selections", {})
        for tool_name, btn_group, group_radios in radio_groups:
            chosen = next((p for p, rb in group_radios if rb.isChecked()), None)
            if chosen:
                selected.append(chosen)
                pack_selections[tool_name] = chosen["name"]
        cfg["pack_selections"] = pack_selections
        save_local_config(cfg)

        if not selected:
            cfg["setup_version"] = EXTENSION_VERSION
            save_local_config(cfg)
            wizard.accept()
            return
        anima_pack = next((p for p in selected if p.get("default_artist_list")), None)
        if anima_pack:
            artist_entry.setText(anima_pack["default_artist_list"])
            wizard._selected = selected
            show_page(3)  # artist config
        else:
            wizard._selected = selected
            _start_downloads(selected)

    install_btn.clicked.connect(on_install)
    sl.addWidget(install_btn)
    sl.addStretch()
    stack_layout.addWidget(select_page)
    pages.append(select_page)

    # ── Page 3: Artist config ──
    artist_page = QWidget()
    al = QVBoxLayout(artist_page)
    al.addWidget(QLabel("<h3>Anima Artist Styles</h3>"))
    al.addWidget(QLabel("Comma-separated list of @artist tags. The model will default to the first one.\nYou can change this later in Settings > Extensions > Configure."))

    browse_styles_btn = QPushButton("Browse Styles")
    browse_styles_btn.clicked.connect(lambda: webbrowser.open("https://thetacursed.github.io/Anima-Style-Explorer/index.html"))
    al.addWidget(browse_styles_btn)

    artist_entry = QLineEdit()
    al.addWidget(artist_entry)

    continue_btn = QPushButton("Continue")

    def on_artist_continue():
        value = artist_entry.text().strip()
        if value:
            cfg = load_local_config()
            cfg.setdefault("pack_settings", {}).setdefault("anima", {})["artist_list"] = value
            save_local_config(cfg)
            log.info("Saved anima artist_list: %s", value)
        _start_downloads(wizard._selected)

    continue_btn.clicked.connect(on_artist_continue)
    al.addWidget(continue_btn)
    al.addStretch()
    stack_layout.addWidget(artist_page)
    pages.append(artist_page)

    # ── Page 4: Download ──
    dl_page = QWidget()
    dll = QVBoxLayout(dl_page)
    dl_title = QLabel("<h3>Downloading models...</h3>")
    dll.addWidget(dl_title)
    dl_file_label = QLabel("Preparing...")
    dll.addWidget(dl_file_label)
    dl_progress = QProgressBar()
    dll.addWidget(dl_progress)
    dl_detail_label = QLabel("")
    dl_detail_label.setStyleSheet("color: gray;")
    dll.addWidget(dl_detail_label)
    dl_error_label = QLabel("")
    dl_error_label.setStyleSheet("color: red;")
    dl_error_label.setWordWrap(True)
    dll.addWidget(dl_error_label)
    dl_retry_btn = QPushButton("Retry")
    dl_retry_btn.setEnabled(False)
    dll.addWidget(dl_retry_btn)
    dll.addStretch()
    stack_layout.addWidget(dl_page)
    pages.append(dl_page)

    dl_state = DownloadState()
    download_thread_holder = []

    def _start_downloads(selected):
        show_page(4)
        all_models = []
        for pack in selected:
            for m in pack["models"]:
                if not any(e["filename"] == m["filename"] and e["subfolder"] == m["subfolder"] for e in all_models):
                    all_models.append(m)
        log.info("Installing %d pack(s), %d unique model(s)", len(selected), len(all_models))

        def do_download():
            if download_thread_holder and download_thread_holder[0].is_alive():
                return
            dl_state.update(status="idle", error=None)
            dl_retry_btn.setEnabled(False)
            dl_error_label.setText("")
            t = threading.Thread(target=download_models, args=(state["models_dir"], all_models, dl_state), daemon=True)
            download_thread_holder.clear()
            download_thread_holder.append(t)
            t.start()

        dl_retry_btn.clicked.disconnect() if dl_retry_btn.receivers(dl_retry_btn.clicked) > 0 else None
        dl_retry_btn.clicked.connect(do_download)
        do_download()

        def poll():
            snap = dl_state.snapshot()
            if snap["status"] == "downloading":
                idx = snap["file_index"]
                count = snap["file_count"]
                fname = snap["current_file"]
                cur = snap["current_bytes"]
                total = snap["total_bytes"]
                overall = snap["overall_bytes"]
                overall_total = snap["overall_total"]
                dl_file_label.setText(f"File {idx+1}/{count}: {fname} ({_format_bytes(cur)} / {_format_bytes(total)})")
                if overall_total > 0:
                    dl_progress.setValue(int(overall / overall_total * 100))
                    dl_detail_label.setText(f"Overall: {_format_bytes(overall)} / {_format_bytes(overall_total)}")
            elif snap["status"] == "complete":
                dl_file_label.setText("All models downloaded!")
                dl_progress.setValue(100)
                dl_detail_label.setText("Setup complete.")
                cfg = load_local_config()
                cfg["setup_version"] = EXTENSION_VERSION
                save_local_config(cfg)
                QTimer.singleShot(1500, wizard.accept)
                return
            elif snap["status"] == "error":
                dl_error_label.setText(snap["error"])
                dl_retry_btn.setEnabled(True)

        poll_timer = QTimer(wizard)
        poll_timer.timeout.connect(poll)
        poll_timer.start(200)

    # ── Show initial page ──
    if need_comfyui:
        show_page(0)
        QTimer.singleShot(1000, check_comfyui_auto)
    else:
        resolved = find_models_dir()
        if resolved:
            state["models_dir"] = resolved
            show_page(2)
        else:
            show_page(1)

    if wizard.exec() != QDialog.DialogCode.Accepted:
        log.info("First-time setup cancelled by user")
        sys.exit(0)


# ── 3. Download UI ───────────────────────────────────────────────────

def run_download_ui(models_dir: str, models: list[dict], title: str):
    """Show model download progress for a single pack."""
    log.info("Opening download UI: title=%r, %d models", title, len(models))
    from server.downloader import DownloadState, download_models

    def _all_present():
        return all(os.path.isfile(os.path.join(models_dir, m["subfolder"], m["filename"])) for m in models)

    if _all_present():
        log.info("All models already present.")
        return

    app = _get_app()
    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP — Setup")
    dialog.setMinimumWidth(520)

    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"<h3>{title}</h3>"))

    file_label = QLabel("Preparing...")
    layout.addWidget(file_label)
    progress = QProgressBar()
    layout.addWidget(progress)
    detail_label = QLabel("")
    detail_label.setStyleSheet("color: gray;")
    layout.addWidget(detail_label)
    error_label = QLabel("")
    error_label.setStyleSheet("color: red;")
    error_label.setWordWrap(True)
    layout.addWidget(error_label)
    retry_btn = QPushButton("Retry")
    retry_btn.setEnabled(False)
    layout.addWidget(retry_btn)

    dl_state = DownloadState()
    download_thread_holder = []

    def _format_bytes(n):
        if n >= 1_000_000_000:
            return f"{n / 1_073_741_824:.1f} GB"
        if n >= 1_000_000:
            return f"{n / 1_048_576:.0f} MB"
        return f"{n / 1024:.0f} KB"

    def start():
        if download_thread_holder and download_thread_holder[0].is_alive():
            return
        dl_state.update(status="idle", error=None)
        retry_btn.setEnabled(False)
        error_label.setText("")
        t = threading.Thread(target=download_models, args=(models_dir, models, dl_state), daemon=True)
        download_thread_holder.clear()
        download_thread_holder.append(t)
        t.start()

    retry_btn.clicked.connect(start)
    start()

    def poll():
        snap = dl_state.snapshot()
        if snap["status"] == "downloading":
            idx, count = snap["file_index"], snap["file_count"]
            file_label.setText(f"File {idx+1}/{count}: {snap['current_file']} ({_format_bytes(snap['current_bytes'])} / {_format_bytes(snap['total_bytes'])})")
            if snap["overall_total"] > 0:
                progress.setValue(int(snap["overall_bytes"] / snap["overall_total"] * 100))
                detail_label.setText(f"Overall: {_format_bytes(snap['overall_bytes'])} / {_format_bytes(snap['overall_total'])}")
        elif snap["status"] == "complete":
            file_label.setText("All models downloaded!")
            progress.setValue(100)
            detail_label.setText("Download complete.")
            QTimer.singleShot(1500, dialog.accept)
            return
        elif snap["status"] == "error":
            error_label.setText(snap["error"])
            retry_btn.setEnabled(True)

    timer = QTimer(dialog)
    timer.timeout.connect(poll)
    timer.start(200)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        log.info("Download cancelled by user")


# ── 4. Tunnel Choice ─────────────────────────────────────────────────

def show_tunnel_choice(local_cfg: dict, save_fn) -> bool:
    """Show tunnel vs reverse proxy choice dialog. Returns True for tunnel."""
    app = _get_app()
    result = {"use_tunnel": True}

    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP — Connection")
    dialog.setMinimumWidth(400)

    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("<h3>How do you want to expose the server?</h3>"))

    tunnel_radio = QRadioButton("Cloudflare tunnel (easiest, URL changes on restart)")
    tunnel_radio.setChecked(True)
    proxy_radio = QRadioButton("I have my own domain / reverse proxy")
    layout.addWidget(tunnel_radio)
    layout.addWidget(proxy_radio)

    remember_cb = QCheckBox("Remember this choice")
    layout.addWidget(remember_cb)

    start_btn = QPushButton("Start")

    def on_start():
        result["use_tunnel"] = tunnel_radio.isChecked()
        if remember_cb.isChecked():
            local_cfg["use_tunnel"] = result["use_tunnel"]
            save_fn(local_cfg)
            log.info("Saved tunnel preference: %s", result["use_tunnel"])
        dialog.accept()

    start_btn.clicked.connect(on_start)
    layout.addWidget(start_btn)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        log.info("Tunnel choice cancelled by user")
        sys.exit(0)
    return result["use_tunnel"]


# ── 5. URL Window (with tray icon) ───────────────────────────────────

class ServerWindow(QMainWindow):
    """Main server window with system tray icon support."""

    # Signal for cross-thread download UI requests (background thread → main thread)
    download_requested = pyqtSignal(str, object, str)  # models_dir, models (list[dict]), title

    def __init__(self, title: str, url: str | None = None, port: int | None = None, mcp_path: str | None = None):
        super().__init__()
        self.setWindowTitle("Comfy-Gen-MCP — Server")
        self.setMinimumWidth(520)

        # Cross-thread download support
        self._download_done = _threading.Event()
        self.download_requested.connect(self._show_download_dialog)

        self._url = url or f"http://localhost:{port}{mcp_path}"

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel(f"<h2>{title}</h2>"))

        # URL display
        url_label = QLabel("MCP URL:" if url else "Reverse proxy target:")
        layout.addWidget(url_label)
        self._url_entry = QLineEdit(self._url)
        self._url_entry.setReadOnly(True)
        layout.addWidget(self._url_entry)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #2a82da;")
        layout.addWidget(self._status_label)

        copy_btn = QPushButton("Copy URL")
        copy_btn.clicked.connect(self._copy_url)
        layout.addWidget(copy_btn)

        # Instructions (tunnel mode only)
        if url:
            instructions = QLabel(
                "How to add as a connector in Claude.ai:\n\n"
                "1) Go to claude.ai, click on Customize\n"
                "2) Click on Connectors\n"
                "3) Click on the + sign next to the search icon\n"
                "4) Click 'Add custom connector'\n"
                "5) Give it a name and paste the URL above\n"
                "6) Optional: Remove any old versions of the connector"
            )
            instructions.setWordWrap(True)
            layout.addWidget(instructions)

        footer = QLabel("Closing this window hides it to the system tray.\nRight-click the tray icon and select Quit to stop the server.")
        footer.setStyleSheet("color: #4CAF50;")
        layout.addWidget(footer)

        # System tray icon
        self._tray = QSystemTrayIcon(self)
        # Use the app-wide icon (set in _get_app), fall back to system icon
        icon_path = _get_icon_path()
        if icon_path:
            self._tray.setIcon(QIcon(icon_path))
        else:
            self._tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        self._tray.setToolTip("Comfy-Gen-MCP")

        tray_menu = QMenu()
        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        copy_action = QAction("Copy URL", self)
        copy_action.triggered.connect(self._copy_url)
        tray_menu.addAction(copy_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_click)
        self._tray.show()

    def _copy_url(self):
        from server.tunnel import copy_to_clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(self._url)
        copy_to_clipboard(self._url)
        self._status_label.setText("Copied to clipboard!")

    def _show_window(self):
        self.show()
        self.activateWindow()
        self.raise_()

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _quit(self):
        """Actually quit — called from tray menu."""
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, event):
        """Hide to tray instead of quitting (unless we're actually quitting)."""
        if getattr(self, "_quitting", False):
            event.accept()
            return
        log.info("Window close requested — hiding to tray")
        event.ignore()
        self.hide()
        self._tray.showMessage("Comfy-Gen-MCP", "Server is still running. Right-click tray icon to quit.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _show_download_dialog(self, models_dir: str, models, title: str):
        """Slot called on main thread via signal. Shows download UI as a modal dialog."""
        log.info("Showing download dialog on main thread: %s", title)
        run_download_ui(models_dir, models, title)
        self._download_done.set()

    def request_download(self, models_dir: str, models: list[dict], title: str):
        """Called from background thread. Signals main thread and waits for completion."""
        log.info("Requesting download from background thread: %s", title)
        self._download_done.clear()
        self.download_requested.emit(models_dir, models, title)
        self._download_done.wait()
        log.info("Download request completed: %s", title)


def show_url_window(url: str, on_ready=None):
    """Show the tunnel URL window with tray icon. Blocks until Quit.

    on_ready: optional callback receiving the ServerWindow before the event loop starts.
    """
    app = _get_app()
    app.setQuitOnLastWindowClosed(False)  # tray icon keeps app alive
    window = ServerWindow(title="MCP Server Running", url=url)
    window.show()
    if on_ready:
        on_ready(window)
    log.info("Starting Qt event loop for URL window...")
    app.exec()


def show_server_running_window(port: int, mcp_path: str, on_ready=None):
    """Show minimal server running window with tray icon. Blocks until Quit.

    on_ready: optional callback receiving the ServerWindow before the event loop starts.
    """
    app = _get_app()
    app.setQuitOnLastWindowClosed(False)
    window = ServerWindow(title="MCP Server Running", port=port, mcp_path=mcp_path)
    window.show()
    if on_ready:
        on_ready(window)
    log.info("Starting Qt event loop for server window...")
    app.exec()


def run_with_progress(label: str, task_fn) -> object:
    """Show a 'please wait' dialog while task_fn runs in a background thread. Returns task_fn's result."""
    app = _get_app()
    result = [None]
    error = [None]

    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP")
    dialog.setMinimumWidth(350)
    layout = QVBoxLayout(dialog)

    msg = QLabel(label)
    msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(msg)

    progress = QProgressBar()
    progress.setRange(0, 0)  # indeterminate
    layout.addWidget(progress)

    def _run():
        try:
            result[0] = task_fn()
        except Exception as e:
            error[0] = e
        finally:
            QTimer.singleShot(0, dialog.accept)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    dialog.exec()

    if error[0]:
        raise error[0]
    return result[0]
