"""PyQt6 UI for Comfy-Gen-MCP — setup wizards, dialogs, and server windows."""

import logging
import os
import platform
import sys
import threading
import time
import webbrowser

from PyQt6.QtCore import Qt, QTimer, QThread, QEventLoop, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
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
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from server.config import EXTENSION_VERSION, MODEL_PACKS_DIR, load_local_config, save_local_config
from server.comfyui import find_comfy_cli, find_models_dir, install_comfyui, remove_comfyui_dir, _detect_gpu, _default_install_dir

log = logging.getLogger("comfy-mcp")


def _format_bytes(n) -> str:
    """Format a byte count as a human-readable GB/MB/KB string."""
    if n >= 1_000_000_000:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_048_576:.0f} MB"
    return f"{n / 1024:.0f} KB"


def run_off_main(label: str, fn, *args, delay_ms: int = 250):
    """Run a potentially-slow blocking call (e.g. a comfy-cli subprocess) without freezing
    the GUI.

    comfy-cli invocations can take several seconds each, so they must never run directly on
    the Qt main thread. On the GUI thread this runs ``fn(*args)`` on a worker and pumps a
    nested event loop, showing a small progress dialog only if the call takes longer than
    ``delay_ms`` (so cache-warm / fast calls show nothing). Off the GUI thread, or with no
    QApplication yet, it just calls ``fn`` directly.
    """
    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return fn(*args)

    state: dict = {}
    loop = QEventLoop()

    def _worker():
        try:
            state["value"] = fn(*args)
        except Exception as e:  # surface to the caller after the loop unwinds
            state["error"] = e
        finally:
            QTimer.singleShot(0, loop.quit)

    threading.Thread(target=_worker, daemon=True).start()

    dlg_holder: dict = {}

    def _maybe_show():
        if "value" in state or "error" in state:
            return  # already finished — don't flash a dialog
        dlg = QDialog()
        dlg.setWindowTitle("Comfy-Gen-MCP")
        dlg.setModal(True)
        dlg.setMinimumWidth(320)
        v = QVBoxLayout(dlg)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        v.addWidget(bar)
        dlg_holder["dlg"] = dlg
        dlg.show()

    QTimer.singleShot(delay_ms, _maybe_show)
    loop.exec()
    if "dlg" in dlg_holder:
        dlg_holder["dlg"].close()
    if "error" in state:
        raise state["error"]
    return state.get("value")


def _open_path(path: str) -> None:
    """Open a file or folder in the OS default handler (file browser / editor)."""
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        import subprocess
        subprocess.Popen(["open", path])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


def _open_config_file(parent=None) -> None:
    """Open local_config.json in the system editor, seeding it if missing."""
    from server.config import LOCAL_CONFIG_PATH, ensure_user_settings, load_local_config, save_local_config
    if not os.path.isfile(LOCAL_CONFIG_PATH):
        cfg = load_local_config()
        ensure_user_settings(cfg)
        try:
            save_local_config(cfg)
        except OSError as e:
            QMessageBox.information(parent, "Config File", f"Could not create config file:\n{LOCAL_CONFIG_PATH}\n\n{e}")
            return
    _open_path(LOCAL_CONFIG_PATH)


def _open_loras_folder(parent=None) -> None:
    """Open ComfyUI's models/loras folder in the file browser, creating it if needed."""
    comfy_cli = find_comfy_cli()
    models_dir = run_off_main("Locating ComfyUI…", find_models_dir, comfy_cli) if comfy_cli else None
    if not models_dir:
        QMessageBox.information(
            parent, "LoRAs Folder Not Found",
            "Could not locate ComfyUI's models directory. Install/launch ComfyUI first."
        )
        return
    loras_dir = os.path.join(models_dir, "loras")
    try:
        os.makedirs(loras_dir, exist_ok=True)
    except OSError as e:
        QMessageBox.information(parent, "LoRAs Folder", f"Could not open folder:\n{loras_dir}\n\n{e}")
        return
    _open_path(loras_dir)


def _open_logs_folder(parent=None) -> None:
    """Open the shared logs folder (server.log + comfyui.log) in the file browser."""
    from server.config import LOGS_DIR, ensure_logs_dir
    try:
        ensure_logs_dir()
    except OSError as e:
        QMessageBox.information(parent, "Logs", f"Could not open logs folder:\n{LOGS_DIR}\n\n{e}")
        return
    _open_path(LOGS_DIR)


def _get_icon_path() -> str | None:
    """Get the path to the tray/app icon."""
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


class _RefreshingComboBox(QComboBox):
    """Editable combo whose item list is re-fetched each time the popup opens.

    Lets the LoRA dropdown pick up newly-dropped-in files without reopening Settings.
    The user's current text (typed or selected) is preserved across the refresh.
    """

    def __init__(self, list_fn, parent=None):
        super().__init__(parent)
        self._list_fn = list_fn

    def showPopup(self):
        current = self.currentText()
        items = self._list_fn()
        if items != [self.itemText(i) for i in range(self.count())]:
            self.blockSignals(True)
            self.clear()
            self.addItems(items)
            self.setCurrentText(current)
            self.blockSignals(False)
        super().showPopup()


def _make_hline() -> QFrame:
    """Thin horizontal separator for section dividers."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _fit_scroll_to_content(scroll: QScrollArea, inner: QWidget, extra: int = 8) -> None:
    """Make a scroll area wide enough to show *inner* without a horizontal scrollbar.

    Sets the scroll area's minimum width to the content's preferred width plus room for the
    vertical scrollbar, and turns horizontal scrolling off entirely so it can never appear.
    The host dialog inherits this as its minimum width, so it opens exactly as wide as the
    content needs — no guessing a fixed width.
    """
    inner.adjustSize()
    vbar = scroll.verticalScrollBar().sizeHint().width()
    frame = 2 * scroll.frameWidth()
    scroll.setMinimumWidth(inner.sizeHint().width() + vbar + frame + extra)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


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

    # Driven manually (see _pulse): the built-in indeterminate sweep (setRange(0,0)) doesn't
    # animate reliably with this Qt/Fusion build, so we bounce the value ourselves.
    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setTextVisible(False)
    progress.setVisible(False)
    layout.addWidget(progress)

    pulse = {"v": 0, "dir": 4}

    def _pulse():
        v = pulse["v"] + pulse["dir"]
        if v >= 100:
            v, pulse["dir"] = 100, -4
        elif v <= 0:
            v, pulse["dir"] = 0, 4
        pulse["v"] = v
        progress.setValue(v)

    pulse_timer = QTimer(parent)
    pulse_timer.timeout.connect(_pulse)

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
        pulse_timer.start(30)
        status_label.setText("Installing ComfyUI... this may take several minutes.")
        error_label.setText("")
        install_state["status"] = "running"
        install_state["error"] = None
        install_state["started"] = time.time()

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
        if install_state["status"] == "running":
            elapsed = int(time.time() - install_state.get("started", time.time()))
            status_label.setText(
                "Installing ComfyUI... this may take several minutes. "
                f"(elapsed {elapsed // 60}:{elapsed % 60:02d})"
            )
        elif install_state["status"] == "done":
            poll_timer.stop()
            pulse_timer.stop()
            progress.setVisible(False)
            status_label.setText("ComfyUI installed successfully!")
            log.info("Install complete, advancing UI")
            QTimer.singleShot(500, on_install_ready)
        elif install_state["status"] == "error":
            poll_timer.stop()
            pulse_timer.stop()
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


# ── Settings form (shared by the setup wizard and the Settings dialog) ──

def _build_settings_form(layout: QVBoxLayout, packs: list[dict],
                         groups: dict[str, list[dict]], parent: QWidget):
    """Populate *layout* with the full settings form and return a ``collect()`` closure.

    Sections: model-pack selection (multi-pack groups), the global scalar fields from
    SETTINGS_SCHEMA, anima artist styles, and the anima LoRA list editor. ``collect()``
    reads the widgets, merges values into local_config.json (preserving unknown keys), and
    saves. *packs* is the full pack list (used to detect the anima pack); *groups* is every
    tool group.
    """
    from server.settings import SETTINGS_SCHEMA
    cfg = load_local_config()
    anima_pack = next((p for p in packs if p.get("default_artist_list")), None)

    # ── Model packs: one radio group per multi-pack tool ──
    radio_groups: list[tuple[str, list[tuple[dict, QRadioButton]]]] = []
    multi = [(tn, g) for tn, g in groups.items() if len(g) > 1]
    if multi:
        layout.addWidget(QLabel("<b>Model packs</b>"))
        for tool_name, group in multi:
            header = tool_name.replace("generate_", "").replace("_", " ").title() + " Model"
            layout.addWidget(QLabel(f"{header}:"))
            btn_group = QButtonGroup(parent)
            btn_group.setExclusive(True)
            prev = cfg.get("pack_selections", {}).get(tool_name)
            group_radios: list[tuple[dict, QRadioButton]] = []
            for pack in group:
                total = sum(m["size_bytes"] for m in pack["models"])
                rb = QRadioButton(f"{pack['display_name']} ({_format_bytes(total)})")
                if prev == pack["name"]:
                    rb.setChecked(True)
                elif not prev and pack.get("is_default"):
                    rb.setChecked(True)
                desc = QLabel(f"  {pack.get('description', '')}")
                desc.setWordWrap(True)
                desc.setStyleSheet("color: gray; margin-left: 20px; margin-bottom: 4px;")
                btn_group.addButton(rb)
                layout.addWidget(rb)
                layout.addWidget(desc)
                group_radios.append((pack, rb))
            if not any(rb.isChecked() for _, rb in group_radios):
                group_radios[0][1].setChecked(True)
            radio_groups.append((tool_name, group_radios))
        layout.addWidget(_make_hline())

    # ── Global scalar fields from the schema ──
    field_widgets: dict[str, tuple[str, QWidget]] = {}

    def add_field(field: dict):
        layout.addWidget(QLabel(f"<b>{field['title']}</b>"))
        if field.get("description"):
            d = QLabel(field["description"])
            d.setWordWrap(True)
            d.setStyleSheet("color: gray;")
            layout.addWidget(d)
        ftype = field["type"]
        cur = cfg.get(field["key"], field["default"])
        if ftype == "text":
            w = QLineEdit(str(cur))
            layout.addWidget(w)
        elif ftype == "path":
            row = QHBoxLayout()
            w = QLineEdit(str(cur))
            browse = QPushButton("Browse...")
            browse.setFixedWidth(100)
            browse.clicked.connect(
                lambda _=False, ww=w: (
                    lambda p: ww.setText(p) if p else None
                )(QFileDialog.getOpenFileName(parent, "Select workflow JSON", "", "JSON (*.json)")[0])
            )
            row.addWidget(w)
            row.addWidget(browse)
            layout.addLayout(row)
        elif ftype == "int":
            w = QSpinBox()
            w.setRange(field.get("min", 0), field.get("max", 1_000_000))
            try:
                w.setValue(int(cur))
            except (TypeError, ValueError):
                w.setValue(int(field["default"]))
            layout.addWidget(w)
        elif ftype == "bool":
            w = QCheckBox("Enabled")
            w.setChecked(bool(cur))
            layout.addWidget(w)
        else:
            return
        field_widgets[field["key"]] = (ftype, w)

    for f in [s for s in SETTINGS_SCHEMA if not s.get("advanced")]:
        add_field(f)

    # ── Anima artist styles ──
    artist_entry = None
    if anima_pack:
        layout.addWidget(_make_hline())
        layout.addWidget(QLabel("<b>Anima Artist Styles</b>"))
        d = QLabel("Comma-separated @artist tags. The model defaults to the first one.")
        d.setWordWrap(True)
        d.setStyleSheet("color: gray;")
        layout.addWidget(d)
        browse_styles = QPushButton("Browse Styles")
        browse_styles.clicked.connect(
            lambda: webbrowser.open("https://thetacursed.github.io/Anima-Style-Explorer/index.html")
        )
        layout.addWidget(browse_styles)
        artist_entry = QLineEdit()
        cur_art = (cfg.get("pack_settings", {}).get("anima", {}).get("artist_list")
                   or anima_pack["default_artist_list"])
        artist_entry.setText(cur_art)
        layout.addWidget(artist_entry)

    # ── Anima LoRAs (list editor) ──
    lora_rows: list[tuple[QComboBox, QDoubleSpinBox, QLineEdit, QWidget]] = []
    if anima_pack:
        layout.addWidget(_make_hline())
        layout.addWidget(QLabel("<b>Anima LoRAs</b>"))
        d = QLabel("Drop .safetensors into the loras folder, then add them here. A LoRA only "
                   "applies when its trigger word is in the prompt — clear the trigger to "
                   "always apply it. Be sure to set the trigger word to the same one used to "
                   "train the LoRA (if applicable).\n\nIf the LoRA adds a new artist or style, "
                   "also add its tag to the Anima Artist Styles list above so the model knows "
                   "to use it.")
        d.setWordWrap(True)
        d.setStyleSheet("color: gray;")
        layout.addWidget(d)

        comfy_cli = find_comfy_cli()
        mdir = run_off_main("Locating ComfyUI…", find_models_dir, comfy_cli) if comfy_cli else None
        loras_dir = os.path.join(mdir, "loras") if mdir else None

        def list_loras() -> list[str]:
            if loras_dir and os.path.isdir(loras_dir):
                return sorted(f for f in os.listdir(loras_dir) if f.lower().endswith(".safetensors"))
            return []

        available = list_loras()

        lora_container = QWidget()
        lora_layout = QVBoxLayout(lora_container)
        lora_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(lora_container)

        def add_lora_row(name: str = "", strength: float = 1.0, trigger: str = "",
                         suggest_trigger: bool = False):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            combo = _RefreshingComboBox(list_loras)
            combo.setEditable(True)
            # Don't let the combo collapse to the width of whatever's currently in the list
            # (it ends up tiny / truncates long filenames). Keep a sensible minimum and let
            # the popup size to its contents so full names are readable.
            combo.setMinimumWidth(260)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
            combo.addItems(available)
            if name:
                combo.setCurrentText(name)
            elif available:
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentText("")
            spin = QDoubleSpinBox()
            spin.setRange(-5.0, 5.0)
            spin.setSingleStep(0.1)
            spin.setValue(float(strength))
            trigger_edit = QLineEdit(trigger)
            trigger_edit.setPlaceholderText("trigger word (optional)")
            trigger_edit.setMinimumWidth(160)
            remove = QPushButton("Remove")
            remove.setFixedWidth(80)
            entry = (combo, spin, trigger_edit, row)
            remove.clicked.connect(lambda: (lora_rows.remove(entry), row.setParent(None)))
            rl.addWidget(combo, 3)
            rl.addWidget(spin, 0)
            rl.addWidget(trigger_edit, 2)
            rl.addWidget(remove, 0)
            lora_layout.addWidget(row)
            lora_rows.append(entry)

            # New rows default their trigger to the LoRA's filename (stem), auto-syncing as
            # the file is changed — until the user edits the trigger themselves. Existing
            # rows keep whatever was stored (empty = always-on).
            if suggest_trigger:
                touched = {"v": False}
                stem = lambda: os.path.splitext(combo.currentText().strip())[0]
                trigger_edit.setText(stem())
                trigger_edit.textEdited.connect(lambda *_: touched.update(v=True))
                combo.currentTextChanged.connect(
                    lambda *_: None if touched["v"] else trigger_edit.setText(stem())
                )

        for e in cfg.get("pack_loras", {}).get("anima", []):
            if isinstance(e, str):
                e = {"name": e}
            add_lora_row(e.get("name", ""), e.get("strength", 1.0), e.get("trigger", ""))

        lora_btn_row = QHBoxLayout()
        add_btn = QPushButton("Add LoRA")
        add_btn.clicked.connect(lambda: add_lora_row(suggest_trigger=True))
        open_loras_btn = QPushButton("Open LoRAs Folder")
        open_loras_btn.clicked.connect(lambda: _open_loras_folder(parent))
        lora_btn_row.addWidget(add_btn)
        lora_btn_row.addWidget(open_loras_btn)
        lora_btn_row.addStretch()
        layout.addLayout(lora_btn_row)

    # ── Advanced fields ──
    for f in [s for s in SETTINGS_SCHEMA if s.get("advanced")]:
        layout.addWidget(_make_hline())
        add_field(f)

    def collect() -> dict:
        c = load_local_config()
        if radio_groups:
            sel = c.get("pack_selections", {})
            for tool_name, group_radios in radio_groups:
                chosen = next((p for p, rb in group_radios if rb.isChecked()), None)
                if chosen:
                    sel[tool_name] = chosen["name"]
            c["pack_selections"] = sel
        for key, (ftype, w) in field_widgets.items():
            if ftype in ("text", "path"):
                c[key] = w.text().strip()
            elif ftype == "int":
                c[key] = int(w.value())
            elif ftype == "bool":
                c[key] = bool(w.isChecked())
        if artist_entry is not None:
            val = artist_entry.text().strip()
            if val:
                c.setdefault("pack_settings", {}).setdefault("anima", {})["artist_list"] = val
        if anima_pack:
            loras = []
            for combo, spin, trigger_edit, _row in lora_rows:
                name = combo.currentText().strip()
                if name:
                    entry = {"name": name, "strength": round(float(spin.value()), 3)}
                    trigger = trigger_edit.text().strip()
                    if trigger:
                        entry["trigger"] = trigger
                    loras.append(entry)
            c.setdefault("pack_loras", {})["anima"] = loras
        save_local_config(c)
        log.info("Settings saved to local_config.json")
        return c

    return collect


def run_settings_dialog(managed: bool = False):
    """Open the Settings dialog (modal). Loads all packs itself so the tray window doesn't
    have to thread them through. Saving writes local_config.json; changes apply on restart.

    managed: when True (shim-managed instance) the Uninstall button is omitted — uninstalling
    is done from Claude Desktop's extension manager, not from a managed server window."""
    from server.model_pack import group_packs_by_tool, load_all_packs

    app = _get_app()  # noqa: F841 — ensures a QApplication exists
    packs = load_all_packs(MODEL_PACKS_DIR)
    groups = group_packs_by_tool(packs)

    dialog = QDialog()
    dialog.setWindowTitle("Comfy-Gen-MCP — Settings")
    dialog.setMinimumHeight(600)
    outer = QVBoxLayout(dialog)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    form_layout = QVBoxLayout(inner)
    collect = _build_settings_form(form_layout, packs, groups, inner)
    form_layout.addStretch()

    result = {"saved": False, "uninstall": False, "restart": False}

    def _on_uninstall():
        comfy_cli = find_comfy_cli()
        install_path = None
        if comfy_cli:
            from server.comfyui import _comfy_which
            install_path = run_off_main("Locating ComfyUI…", _comfy_which, comfy_cli)
        if not install_path or not os.path.isdir(install_path):
            install_path = _default_install_dir()
        app_dir = os.path.dirname(_default_install_dir())  # ~/.comfy-gen-mcp

        if not install_path or not os.path.isdir(install_path):
            QMessageBox.information(
                dialog, "Uninstall",
                "No ComfyUI installation was found to remove.\n\n"
                "To finish uninstalling Comfy-Gen-MCP, remove the extension in Claude Desktop "
                f"(Settings → Extensions). You may also delete:\n{app_dir}")
            return

        reply = QMessageBox.question(
            dialog, "Uninstall ComfyUI",
            f"This permanently deletes ComfyUI and all downloaded models at:\n{install_path}\n\n"
            "The app will then quit so you can remove the program. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok = run_off_main("Uninstalling ComfyUI…", remove_comfyui_dir, install_path, comfy_cli)
        if not ok:
            QMessageBox.warning(
                dialog, "Uninstall",
                "Could not fully delete the installation — some files may still be in use.\n\n"
                f"Path: {install_path}\n\nClose anything using it and delete it manually.")
            return

        QMessageBox.information(
            dialog, "Uninstalled",
            "ComfyUI and downloaded models have been removed.\n\n"
            "To finish uninstalling Comfy-Gen-MCP:\n"
            "• Remove the 'Comfy-Gen-MCP' extension in Claude Desktop (Settings → Extensions).\n"
            f"• You may also delete this folder:\n  {app_dir}\n\n"
            "The app will now quit.")
        result["uninstall"] = True
        dialog.accept()

    # Destructive action pinned to the bottom of the scrollable settings (not the always-visible
    # row). Omitted for shim-managed instances — those uninstall via Claude Desktop's extensions.
    if not managed:
        form_layout.addWidget(_make_hline())
        uninstall_btn = QPushButton("Uninstall program")
        uninstall_btn.setToolTip("Permanently removes the ComfyUI install and all downloaded models. "
                                 "Use this before deleting the program.")
        uninstall_btn.clicked.connect(_on_uninstall)
        form_layout.addWidget(uninstall_btn)

    scroll.setWidget(inner)
    _fit_scroll_to_content(scroll, inner)  # size to content → never a horizontal scrollbar
    outer.addWidget(scroll)

    # Files & logs
    outer.addWidget(_make_hline())
    tools_row = QHBoxLayout()
    open_config_btn = QPushButton("Open Config File")
    open_logs_btn = QPushButton("Open Logs Folder")
    open_config_btn.clicked.connect(lambda: _open_config_file(dialog))
    open_logs_btn.clicked.connect(lambda: _open_logs_folder(dialog))
    tools_row.addWidget(open_config_btn)
    tools_row.addWidget(open_logs_btn)
    outer.addLayout(tools_row)

    notice = QLabel("")
    notice.setStyleSheet("color: #4CAF50;")
    notice.setWordWrap(True)
    outer.addWidget(notice)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    save_btn = QPushButton("Save")
    close_btn = QPushButton("Close")
    btn_row.addWidget(save_btn)
    btn_row.addWidget(close_btn)
    outer.addLayout(btn_row)

    def _save():
        collect()
        result["saved"] = True
        notice.setText("Settings saved.")
        prompt = "Settings saved. Restart now to apply the changes?"
        if managed:
            prompt += (
                "\n\nNote: changes to the available tools/styles only appear in Claude after you "
                "fully restart Claude Desktop. Other changes apply on restart."
            )
        reply = QMessageBox.question(
            dialog, "Restart to apply", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            result["restart"] = True
            dialog.accept()

    save_btn.clicked.connect(_save)
    close_btn.clicked.connect(dialog.accept)
    dialog.exec()
    return result


# ── 2. First-Time Setup Wizard ───────────────────────────────────────

def run_first_time_setup(packs: list[dict], groups: dict[str, list[dict]], in_process: bool = False):
    """First-run wizard: ComfyUI install → Settings → download."""
    log.info("Opening first-time setup (%d packs, %d groups)", len(packs), len(groups))
    from server.downloader import DownloadState, download_models
    from server.model_pack import resolve_pack_selections

    app = _get_app()
    comfy_cli = find_comfy_cli()
    state = {"models_dir": ""}

    # Pre-check: if ComfyUI is already installed, skip straight to pack selection
    if comfy_cli:
        existing_models = run_off_main("Detecting ComfyUI…", find_models_dir, comfy_cli)
        if existing_models:
            state["models_dir"] = existing_models

    wizard = QDialog()
    wizard.setWindowTitle("Comfy-Gen-MCP — First Time Setup")
    wizard.setMinimumWidth(520)  # floor; the settings page widens it to fit its content
    wizard.setMinimumHeight(560)

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
            resolved = run_off_main("Finishing setup…", find_models_dir, comfy_cli)
            if resolved:
                state["models_dir"] = resolved
        _rebuild_settings_page()  # refresh so the LoRA file list reflects the new models dir
        show_page(1)

    _build_comfyui_install_panel(comfyui_page, _advance_from_install)

    cl.addStretch()
    stack_layout.addWidget(comfyui_page)
    pages.append(comfyui_page)

    # ── Page 1: Settings (the same form used post-setup) ──
    settings_page = QWidget()
    spl = QVBoxLayout(settings_page)
    spl.addWidget(QLabel("<h3>Settings</h3>"))
    intro2 = QLabel("Pick your models and review the settings below. You can change any of "
                    "these later from the server window.")
    intro2.setWordWrap(True)
    spl.addWidget(intro2)

    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    spl.addWidget(settings_scroll)

    collect_holder = {"collect": None}

    def _rebuild_settings_page():
        inner = QWidget()
        il = QVBoxLayout(inner)
        collect_holder["collect"] = _build_settings_form(il, packs, groups, inner)
        il.addStretch()
        settings_scroll.setWidget(inner)
        _fit_scroll_to_content(settings_scroll, inner)  # size to content → no horizontal scrollbar

    _rebuild_settings_page()

    settings_continue_btn = QPushButton("Continue")

    def on_settings_continue():
        collect_holder["collect"]()  # persist all settings (incl. pack_selections)
        # Download one resolved pack per group (lazy download covers anything skipped here).
        selected = resolve_pack_selections(groups)
        _start_downloads(selected)

    settings_continue_btn.clicked.connect(on_settings_continue)
    spl.addWidget(settings_continue_btn)
    stack_layout.addWidget(settings_page)
    pages.append(settings_page)

    # ── Page 2: Download ──
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
        show_page(2)
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


# ── URL Window (with tray icon) ──────────────────────────────────────

class ServerWindow(QMainWindow):
    """Main server window with system tray icon support."""

    # Signal for cross-thread download UI requests (background thread → main thread)
    download_requested = pyqtSignal(str, object, str)  # models_dir, models (list[dict]), title

    def __init__(self, title: str, url: str | None = None, port: int | None = None, mcp_path: str | None = None,
                 stale_check=None, managed_check=None, restart_cb=None):
        super().__init__()
        self.setWindowTitle("Comfy-Gen-MCP — Server")
        self.setMinimumWidth(520)
        self._stale_check = stale_check
        self._managed_check = managed_check
        self._restart_cb = restart_cb
        self._managed_applied: bool | None = None  # last managed state pushed to the UI

        # Cross-thread download support
        self._download_done = threading.Event()
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

        # Local (non-tunnel) mode: Claude Desktop reaches this server internally, so the
        # localhost URL is only useful for reaching it from elsewhere.
        if url is None:
            managed_note = QLabel(
                "You don't need this URL for Claude Desktop — it's already set up to work. "
                "It's only needed if you want to reach this server from another machine."
            )
            managed_note.setStyleSheet("color: gray;")
            managed_note.setWordWrap(True)
            layout.addWidget(managed_note)

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

        # Configuration
        layout.addWidget(_make_hline())

        settings_row = QHBoxLayout()
        settings_label = QLabel("Configure models, artist styles, steps, LoRAs, and open the config file / LoRAs / logs.")
        settings_label.setStyleSheet("color: gray;")
        settings_label.setWordWrap(True)
        settings_btn = QPushButton("Settings")
        settings_btn.setFixedWidth(140)
        settings_btn.clicked.connect(self._open_settings)
        settings_row.addWidget(settings_label)
        settings_row.addWidget(settings_btn)
        layout.addLayout(settings_row)

        # Shown when the user saves settings but declines the offered restart.
        self._restart_label = QLabel("Settings saved — your changes apply after a restart.")
        self._restart_label.setStyleSheet("color: orange;")
        self._restart_label.setWordWrap(True)
        self._restart_label.setVisible(False)
        layout.addWidget(self._restart_label)

        # Troubleshooting — ComfyUI status on the left, reinstall control on the right
        layout.addWidget(_make_hline())
        troubleshoot_row = QHBoxLayout()
        self._comfyui_status = QLabel("ComfyUI: starting...")
        self._comfyui_status.setStyleSheet("color: orange;")
        troubleshoot_label = QLabel("Having problems? Try reinstalling ComfyUI.")
        troubleshoot_label.setStyleSheet("color: gray;")
        reinstall_btn = QPushButton("Reinstall")
        reinstall_btn.setFixedWidth(100)
        reinstall_btn.clicked.connect(self._reinstall_comfyui)
        troubleshoot_row.addWidget(self._comfyui_status)
        troubleshoot_row.addStretch()
        troubleshoot_row.addWidget(troubleshoot_label)
        troubleshoot_row.addWidget(reinstall_btn)
        layout.addLayout(troubleshoot_row)

        # ComfyUI status: the reachability check is a blocking HTTP GET that can hang up to its
        # 2s timeout while ComfyUI is starting, so it runs on a background thread (NEVER the GUI
        # thread — that froze the event loop ~2/3 of the time). The worker writes a cached state;
        # a cheap GUI timer just renders it.
        self._comfyui_failed_notified = False  # one tray nudge per failure episode
        self._comfyui_state: tuple[str, str] = ("starting", "")  # (state, tooltip), set by worker
        self._comfyui_poll_stop = threading.Event()
        self._comfyui_worker = threading.Thread(target=self._poll_comfyui_worker, daemon=True)
        self._comfyui_worker.start()
        self._comfyui_render_timer = QTimer(self)
        self._comfyui_render_timer.timeout.connect(self._render_comfyui_status)
        self._comfyui_render_timer.start(1000)

        layout.addStretch()

        # Managed mode: self-close when the spawning shim's keepalive goes stale.
        if self._stale_check is not None:
            self._stale_poll = QTimer(self)
            self._stale_poll.timeout.connect(self._check_stale)
            self._stale_poll.start(5000)

        quit_row = QHBoxLayout()
        self._footer = QLabel("Closing this window hides it to the system tray; the server keeps running. "
                              "Use Quit (here or from the tray icon) to stop the server.")
        self._footer.setStyleSheet("color: #4CAF50;")
        self._footer.setWordWrap(True)
        self._quit_btn = QPushButton("Quit")
        self._quit_btn.setFixedWidth(100)
        self._quit_btn.clicked.connect(self._quit)
        quit_row.addWidget(self._footer)
        quit_row.addWidget(self._quit_btn)
        layout.addLayout(quit_row)

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

        self._quit_action = QAction("Quit", self)
        self._quit_action.triggered.connect(self._quit)
        tray_menu.addAction(self._quit_action)

        self._tray.setContextMenu(tray_menu)
        self._apply_managed_ui()  # set initial Quit/footer state
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
        self._comfyui_poll_stop.set()  # stop the background status poller
        QApplication.quit()

    def _check_stale(self):
        """Managed mode: if the spawning shim is gone/stale, shut the server down."""
        try:
            if self._stale_check and self._stale_check():
                log.info("Managed lifecycle: shutting down (shim gone/stale)")
                self._quit()
        except Exception as e:
            log.warning("stale_check raised: %s", e)

    def closeEvent(self, event):
        """Hide to tray instead of quitting (unless we're actually quitting)."""
        if getattr(self, "_quitting", False):
            event.accept()
            return
        log.info("Window close requested — hiding to tray")
        event.ignore()
        self.hide()
        managed = bool(self._managed_check and self._managed_check())
        msg = ("Server is still running. It stops automatically when Claude Desktop closes."
               if managed else
               "Server is still running. Right-click tray icon to quit.")
        self._tray.showMessage("Comfy-Gen-MCP", msg, QSystemTrayIcon.MessageIcon.Information, 2000)

    def _poll_comfyui_worker(self):
        """Background thread: do the blocking reachability check off the GUI thread and cache the
        result in self._comfyui_state. The GUI never touches the network."""
        from server.comfyui import _check_url, get_launch_error, set_launch_error
        from server.config import COMFYUI_DEFAULT_URL
        while not self._comfyui_poll_stop.is_set():
            try:
                if _check_url(COMFYUI_DEFAULT_URL):
                    set_launch_error(None)  # healthy now — clear any stale failure
                    self._comfyui_state = ("ready", "")
                elif get_launch_error():
                    self._comfyui_state = ("failed", get_launch_error())
                else:
                    self._comfyui_state = ("starting", "")
            except Exception as e:
                log.warning("ComfyUI status poll failed: %s", e)
            self._comfyui_poll_stop.wait(3.0)

    def _apply_managed_ui(self):
        """Hide the Quit affordances (window button + tray action) and reword the footer when the
        shim is managing us (armed). Idempotent — only touches the UI when the state changes."""
        managed = bool(self._managed_check and self._managed_check())
        if managed == self._managed_applied:
            return
        self._managed_applied = managed
        self._quit_btn.setVisible(not managed)
        self._quit_action.setVisible(not managed)
        if managed:
            self._footer.setText("Closing this window hides it to the system tray. "
                                 "The server stops automatically when Claude Desktop closes.")
        else:
            self._footer.setText("Closing this window hides it to the system tray; the server keeps "
                                 "running. Use Quit (here or from the tray icon) to stop the server.")

    def _render_comfyui_status(self):
        """GUI thread: cheap render of the cached status — no network here."""
        self._apply_managed_ui()
        state, tooltip = self._comfyui_state
        if state == "ready":
            self._comfyui_status.setText("ComfyUI: ready")
            self._comfyui_status.setStyleSheet("color: #4CAF50;")
            self._comfyui_status.setToolTip("")
            self._comfyui_failed_notified = False
        elif state == "failed":
            self._comfyui_status.setText("ComfyUI: failed to start")
            self._comfyui_status.setStyleSheet("color: #f44336;")
            self._comfyui_status.setToolTip(tooltip)
            # Surface it once: the window is usually hidden in the tray, so nudge + raise.
            if not self._comfyui_failed_notified:
                self._comfyui_failed_notified = True
                self._tray.showMessage(
                    "ComfyUI failed to start",
                    "Open Comfy-Gen-MCP and click Reinstall to fix it.",
                    QSystemTrayIcon.MessageIcon.Warning, 5000,
                )
                self._show_window()
        else:
            self._comfyui_status.setText("ComfyUI: starting...")
            self._comfyui_status.setStyleSheet("color: orange;")
            self._comfyui_status.setToolTip("")

    def _open_settings(self):
        """Open the Settings dialog; show the restart notice on save, or quit on uninstall."""
        managed = bool(self._managed_check and self._managed_check())
        outcome = run_settings_dialog(managed=managed)
        if outcome.get("uninstall"):
            # ComfyUI was removed — the app must exit so the user can delete the program.
            self._quitting = True
            QApplication.quit()
            return
        if outcome.get("restart"):
            # Spawn the replacement first (it waits for our port), then quit. Works in both modes.
            if self._restart_cb:
                self._restart_cb()
            self._quitting = True
            self._comfyui_poll_stop.set()
            QApplication.quit()
            return
        if outcome.get("saved"):
            self._restart_label.setVisible(True)
            # The notice adds a row; grow the window so it isn't clipped (never shrink).
            self.resize(self.width(), max(self.height(), self.sizeHint().height()))

    def _reinstall_comfyui(self):
        """Nuke the ComfyUI installation directory and quit so the user can re-run setup."""
        comfy_cli = find_comfy_cli()
        install_path = None
        if comfy_cli:
            from server.comfyui import _comfy_which
            install_path = run_off_main("Locating ComfyUI…", _comfy_which, comfy_cli)

        if not install_path or not os.path.isdir(install_path):
            install_path = _default_install_dir()

        if not install_path or not os.path.isdir(install_path):
            QMessageBox.warning(self, "Error", "Could not find a ComfyUI installation to remove.")
            return

        managed = bool(self._managed_check and self._managed_check())
        restart_hint = (
            "restart Claude Desktop to run setup again" if managed
            else "restart it to run setup again"
        )

        reply = QMessageBox.question(
            self,
            "Reinstall ComfyUI",
            f"This will delete the ComfyUI installation at:\n{install_path}\n\n"
            "Downloaded models will also be removed.\n"
            f"The application will then quit — {restart_hint}.",
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

        done_hint = (
            "Restart Claude Desktop to run setup again." if managed
            else "Restart it to run setup again."
        )
        QMessageBox.information(self, "Done", f"ComfyUI has been removed. The application will now quit.\n{done_hint}")
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


def _show_server_window(window_kwargs: dict, on_ready=None, stale_check=None, managed_check=None,
                        restart_cb=None):
    """Show a ServerWindow with tray icon and run the Qt event loop until Quit.

    on_ready: optional callback receiving the ServerWindow before the event loop starts.
    stale_check: optional callable; if it returns True the window self-closes (managed mode).
    managed_check: optional callable; True once the shim is managing us — hides Quit/Uninstall.
    restart_cb: optional callable; spawns a replacement server (Settings → Save → Restart).
    """
    app = _get_app()
    app.setQuitOnLastWindowClosed(False)  # tray icon keeps app alive
    window = ServerWindow(title="MCP Server Running", stale_check=stale_check,
                          managed_check=managed_check, restart_cb=restart_cb, **window_kwargs)
    window.show()
    if on_ready:
        on_ready(window)
    log.info("Starting Qt event loop for server window...")
    app.exec()


def show_url_window(url: str, on_ready=None, stale_check=None, managed_check=None, restart_cb=None):
    """Show the tunnel URL window with tray icon. Blocks until Quit."""
    _show_server_window({"url": url}, on_ready=on_ready, stale_check=stale_check,
                        managed_check=managed_check, restart_cb=restart_cb)


def show_server_running_window(port: int, mcp_path: str, on_ready=None, stale_check=None,
                               managed_check=None, restart_cb=None):
    """Show the minimal local server-running window with tray icon. Blocks until Quit."""
    _show_server_window({"port": port, "mcp_path": mcp_path}, on_ready=on_ready,
                        stale_check=stale_check, managed_check=managed_check, restart_cb=restart_cb)


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
