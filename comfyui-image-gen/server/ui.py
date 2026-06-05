"""PyQt6 UI for Comfy-Gen-MCP — setup wizards, dialogs, and server windows."""

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
    QFrame,
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

from server.config import EXTENSION_VERSION, load_local_config, save_local_config
from server.comfyui import find_comfy_cli, find_models_dir, install_comfyui, remove_comfyui_dir, _detect_gpu, _default_install_dir

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

        app = QApplication([sys.argv[0]])
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


def _make_hline() -> QFrame:
    """Thin horizontal separator for section dividers."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ── 1. ComfyUI Installation ──────────────────────────────────────────

def _build_comfyui_install_panel(parent: QWidget, on_install_ready):
    """Build the ComfyUI auto-install panel inside `parent`.

    on_install_ready(): called after ComfyUI is installed successfully.
    """
    layout = parent.layout() if parent.layout() else QVBoxLayout(parent)

    comfy_cli = find_comfy_cli()
    detected_gpu = _detect_gpu()
    gpu_labels = {"nvidia": "NVIDIA (CUDA)", "amd": "AMD (ROCm)", "mac": "Apple Silicon", "cpu": "CPU only"}

    layout.addWidget(QLabel("<b>Install ComfyUI via comfy-cli</b>"))

    # Install directory
    layout.addWidget(QLabel("Install location:"))
    dir_row = QHBoxLayout()
    dir_edit = QLineEdit(_default_install_dir())
    dir_browse_btn = QPushButton("Browse...")
    dir_row.addWidget(dir_edit)
    dir_row.addWidget(dir_browse_btn)
    layout.addLayout(dir_row)

    def on_browse_dir():
        path = QFileDialog.getExistingDirectory(parent, "Select install directory")
        if path:
            dir_edit.setText(path)

    dir_browse_btn.clicked.connect(on_browse_dir)

    # GPU selection
    layout.addWidget(QLabel("GPU type:"))
    gpu_group = QButtonGroup(parent)
    gpu_radios: dict[str, QRadioButton] = {}
    for gpu_id, label in gpu_labels.items():
        rb = QRadioButton(label)
        if gpu_id == detected_gpu:
            rb.setChecked(True)
        gpu_group.addButton(rb)
        gpu_radios[gpu_id] = rb
        layout.addWidget(rb)

    # Status / progress
    status_label = QLabel("")
    status_label.setWordWrap(True)
    layout.addWidget(status_label)

    progress = QProgressBar()
    progress.setRange(0, 0)  # indeterminate
    progress.setVisible(False)
    layout.addWidget(progress)

    error_label = QLabel("")
    error_label.setStyleSheet("color: #d96b6b;")
    error_label.setWordWrap(True)
    layout.addWidget(error_label)

    install_btn = QPushButton("Install ComfyUI")
    layout.addWidget(install_btn)

    if not comfy_cli:
        error_label.setText("comfy-cli not found. It should be installed automatically as a dependency.")
        install_btn.setEnabled(False)

    install_state = {"status": "idle", "error": None}  # idle | running | done | error

    def on_install():
        import shutil
        selected_gpu = next((k for k, rb in gpu_radios.items() if rb.isChecked()), detected_gpu)
        install_dir = dir_edit.text().strip()
        if not install_dir:
            error_label.setText("Please specify an install directory.")
            return

        # If the directory exists and isn't a valid git repo, offer to delete it
        if os.path.isdir(install_dir) and not os.path.isdir(os.path.join(install_dir, ".git")):
            reply = QMessageBox.question(
                parent,
                "Directory exists",
                f"The contents of '{install_dir}' will be deleted, and comfyUI installed there. Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(install_dir)
            except Exception as e:
                error_label.setText(f"Failed to delete directory: {e}")
                return

        install_btn.setEnabled(False)
        dir_edit.setEnabled(False)
        dir_browse_btn.setEnabled(False)
        progress.setVisible(True)
        status_label.setText("Installing ComfyUI... this may take several minutes.")
        error_label.setText("")
        install_state["status"] = "running"
        install_state["error"] = None

        def _run():
            try:
                install_comfyui(comfy_cli, gpu=selected_gpu, install_dir=install_dir)
                install_state["status"] = "done"
            except Exception as e:
                log.error("Install failed: %s", e)
                install_state["error"] = str(e)
                install_state["status"] = "error"

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def poll_install():
        if install_state["status"] == "done":
            poll_timer.stop()
            progress.setVisible(False)
            status_label.setText("ComfyUI installed successfully!")
            log.info("Install complete, advancing UI")
            QTimer.singleShot(500, on_install_ready)
        elif install_state["status"] == "error":
            poll_timer.stop()
            progress.setVisible(False)
            status_label.setText("")
            error_label.setText(f"Installation failed: {install_state['error']}")
            install_btn.setEnabled(True)
            dir_edit.setEnabled(True)
            dir_browse_btn.setEnabled(True)

    poll_timer = QTimer(parent)
    poll_timer.timeout.connect(poll_install)
    poll_timer.start(500)

    install_btn.clicked.connect(on_install)


def run_comfyui_setup(in_process: bool = False):
    """Show ComfyUI installation dialog."""
    log.info("Opening ComfyUI setup UI (in_process=%s)", in_process)
    app = _get_app()

    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP — Setup")
    dialog.setMinimumWidth(520)

    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("<h3>ComfyUI is not installed.</h3>"))

    panel = QWidget()
    panel.setLayout(QVBoxLayout())
    _build_comfyui_install_panel(panel, lambda: QTimer.singleShot(300, dialog.accept))
    layout.addWidget(panel)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        log.info("ComfyUI setup cancelled by user")
        sys.exit(0)


# ── 2. First-Time Setup Wizard ───────────────────────────────────────

def run_first_time_setup(packs: list[dict], groups: dict[str, list[dict]], in_process: bool = False):
    """First-run wizard: ComfyUI install → pack selection → artist config → download."""
    log.info("Opening first-time setup (%d packs, %d groups)", len(packs), len(groups))
    from server.downloader import DownloadState, download_models

    app = _get_app()
    comfy_cli = find_comfy_cli()
    state = {"models_dir": ""}

    # Pre-check: if ComfyUI is already installed, skip straight to pack selection
    if comfy_cli:
        existing_models = find_models_dir(comfy_cli)
        if existing_models:
            state["models_dir"] = existing_models

    wizard = QDialog()
    wizard.setWindowTitle("Comfy-Gen-MCP — First Time Setup")
    wizard.setMinimumWidth(520)
    wizard.setMinimumHeight(500)

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

    # ── Page 0: ComfyUI install ──
    comfyui_page = QWidget()
    cl = QVBoxLayout(comfyui_page)
    cl.addWidget(QLabel("<h3>ComfyUI Setup</h3>"))
    intro = QLabel("ComfyUI will be installed automatically via comfy-cli.")
    intro.setWordWrap(True)
    cl.addWidget(intro)

    def _advance_from_install():
        if comfy_cli:
            resolved = find_models_dir(comfy_cli)
            if resolved:
                state["models_dir"] = resolved
        show_page(1)  # pack selection

    _build_comfyui_install_panel(comfyui_page, _advance_from_install)

    cl.addStretch()
    stack_layout.addWidget(comfyui_page)
    pages.append(comfyui_page)

    # ── Page 1: Pack selection ──
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
            if state["models_dir"]:
                cfg["setup_version"] = EXTENSION_VERSION
                save_local_config(cfg)
            wizard.accept()
            return
        anima_pack = next((p for p in selected if p.get("default_artist_list")), None)
        if anima_pack:
            artist_entry.setText(anima_pack["default_artist_list"])
            wizard._selected = selected
            show_page(2)  # artist config
        else:
            wizard._selected = selected
            _start_downloads(selected)

    install_btn.clicked.connect(on_install)
    sl.addWidget(install_btn)
    sl.addStretch()
    stack_layout.addWidget(select_page)
    pages.append(select_page)

    # ── Page 2: Artist config ──
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

    # ── Page 3: Download ──
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
        show_page(3)
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
    if state["models_dir"]:
        show_page(1)  # ComfyUI already installed, skip to pack selection
    else:
        show_page(0)  # Need to install ComfyUI first

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

        # Troubleshooting
        layout.addWidget(_make_hline())
        troubleshoot_row = QHBoxLayout()
        troubleshoot_label = QLabel("Having problems? Try reinstalling ComfyUI.")
        troubleshoot_label.setStyleSheet("color: gray;")
        reinstall_btn = QPushButton("Reinstall")
        reinstall_btn.setFixedWidth(100)
        reinstall_btn.clicked.connect(self._reinstall_comfyui)
        open_log_btn = QPushButton("Open Log")
        open_log_btn.setFixedWidth(100)
        open_log_btn.clicked.connect(self._open_comfyui_log)
        troubleshoot_row.addWidget(troubleshoot_label)
        troubleshoot_row.addWidget(open_log_btn)
        troubleshoot_row.addWidget(reinstall_btn)
        layout.addLayout(troubleshoot_row)

        # ComfyUI status indicator
        layout.addStretch()
        self._comfyui_status = QLabel("ComfyUI: starting...")
        self._comfyui_status.setStyleSheet("color: orange;")
        self._comfyui_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._comfyui_status)

        self._comfyui_poll = QTimer(self)
        self._comfyui_poll.timeout.connect(self._poll_comfyui)
        self._comfyui_poll.start(3000)

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

    def _poll_comfyui(self):
        from server.comfyui import _check_url
        from server.config import COMFYUI_DEFAULT_URL
        if _check_url(COMFYUI_DEFAULT_URL):
            self._comfyui_status.setText("ComfyUI: ready")
            self._comfyui_status.setStyleSheet("color: #4CAF50;")
        else:
            self._comfyui_status.setText("ComfyUI: starting...")
            self._comfyui_status.setStyleSheet("color: orange;")

    def _open_comfyui_log(self):
        """Open the ComfyUI log file in the system's default text editor."""
        comfy_cli = find_comfy_cli()
        if comfy_cli:
            from server.comfyui import _comfy_which, _default_install_dir
            install_path = _comfy_which(comfy_cli) or _default_install_dir()
        else:
            from server.comfyui import _default_install_dir
            install_path = _default_install_dir()

        log_path = os.path.join(install_path, "comfyui.log")
        if not os.path.isfile(log_path):
            QMessageBox.information(self, "No Log", f"Log file not found:\n{log_path}")
            return

        import subprocess as _sp
        if platform.system() == "Windows":
            os.startfile(log_path)
        elif platform.system() == "Darwin":
            _sp.Popen(["open", log_path])
        else:
            _sp.Popen(["xdg-open", log_path])

    def _reinstall_comfyui(self):
        """Nuke the ComfyUI installation directory and quit so the user can re-run setup."""
        comfy_cli = find_comfy_cli()
        install_path = None
        if comfy_cli:
            from server.comfyui import _comfy_which
            install_path = _comfy_which(comfy_cli)

        if not install_path or not os.path.isdir(install_path):
            install_path = _default_install_dir()

        if not install_path or not os.path.isdir(install_path):
            QMessageBox.warning(self, "Error", "Could not find a ComfyUI installation to remove.")
            return

        reply = QMessageBox.question(
            self,
            "Reinstall ComfyUI",
            f"This will delete the ComfyUI installation at:\n{install_path}\n\n"
            "Downloaded models will also be removed.\n"
            "The application will then quit — restart it to run setup again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if not remove_comfyui_dir(install_path, comfy_cli):
            QMessageBox.warning(
                self, "Error",
                f"Could not fully delete the installation — some files may still be in use.\n\n"
                f"Path: {install_path}\n\n"
                f"Please close any programs using this folder, delete it manually, then restart.",
            )
            return

        cfg = load_local_config()
        cfg.pop("setup_version", None)
        save_local_config(cfg)

        QMessageBox.information(self, "Done", "ComfyUI has been removed. The application will now quit.\nRestart it to run setup again.")
        self._quitting = True
        QApplication.quit()

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
