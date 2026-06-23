import io
import json
import locale
import logging
import os
import re
import sys
import threading
import subprocess
import time

import googletrans
from PyQt6 import QtWidgets, QtCore, QtGui
import dulwich
from dulwich import porcelain, client, repo
import requests

logsDir = "logs"

if not os.path.exists(logsDir):
    os.makedirs(logsDir)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
debug_handler = logging.FileHandler(os.path.join(logsDir,'install-debug.log'))
error_handler = logging.FileHandler(os.path.join(logsDir,'install-error.log'))
debug_handler.setLevel(logging.DEBUG)
error_handler.setLevel(logging.ERROR)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
debug_handler.setFormatter(formatter)
error_handler.setFormatter(formatter)

logger.addHandler(debug_handler)
logger.addHandler(error_handler)

subprocess_flags = 0
if os.name == 'nt':
    subprocess_flags = subprocess.CREATE_NO_WINDOW

uv_path = os.environ.get("UV_PATH", "uv")
venv_path = os.environ.get("VENV_PATH", "venv")
# Read-only shipped inputs (repo.json, icon) live here; generated files (logs,
# repo clone, the "installing" marker) are written to the current directory.
resource_dir = os.environ.get("RESOURCE_DIR", ".")

def uv_pip_install(*args, **kwargs):
    cmd = [uv_path, 'pip', 'install', '--python', venv_path, '--no-progress'] + list(args)
    defaults = dict(capture_output=True, check=True, text=True, creationflags=subprocess_flags)
    defaults.update(kwargs)
    return subprocess.run(cmd, **defaults)

logger.debug("All prerequisites available (installed by Go launcher).")
repoData = json.load(open(os.path.join(resource_dir, "repo.json")))

colors_dict = {
    "primary_color":"#1A1D22",
    "secondary_color":"#282C34",
    "hover_color":"#596273",
    "text_color":"#FFFFFF",
    "toggle_color":"#4a708b",
    "green":"#3a7a3a",
    "yellow":"#7a7a3a",
    "red":"#7a3a3a"
}

translator = googletrans.Translator()

class SignalEmitter(QtCore.QObject):
    signal = QtCore.pyqtSignal()

class StrSignalEmitter(QtCore.QObject):
    signal = QtCore.pyqtSignal(str)

class BoolSignalEmitter(QtCore.QObject):
    signal = QtCore.pyqtSignal(bool)

class IntSignalEmitter(QtCore.QObject):
    signal = QtCore.pyqtSignal(int)

def translate_ui_text(text):
    if text is None or text == "":
        return text

    if os.name == "nt":
        import ctypes
        windll = ctypes.windll.kernel32
        import locale
        langCode = locale.windows_locale[windll.GetUserDefaultUILanguage()]
        if "_" in langCode:
            langCode = langCode.split("_")[0]
    else:
        import locale
        langCode = locale.getdefaultlocale()[0].split("_")[0]

    counter = 0
    translatedText = None
    while counter < 10:
        try:
            if "en" in langCode.lower():
                translatedText = text
            else:
                translatedText = translator.translate(text, dest=langCode).text
            break
        except TypeError:
            counter += 1
        except Exception:
            logger.debug("Timeout error when trying to use google translate. Not going to translate.")
            break

    if translatedText is None:
        logger.error("Failed to get translation. Leaving it in english.")
        translatedText = text
        translatedText = translatedText[0].upper() + translatedText[1:]

    if langCode not in ['ja', 'zh-cn', 'zh-tw']:
        translatedText = translatedText[0].upper() + translatedText[1:]

    translatedText = translatedText.strip()

    return translatedText

normalInstallText = translate_ui_text("Updating packages")
torchInstallText = translate_ui_text("Updating pytorch, this may take a while...\nNote: The bar not moving is normal.")

def get_stylesheet():
    styleSheet = """
    * {
        background-color: {primary_color};
        color: {secondary_color};
    }

    QLabel {
        color: {text_color};
    }

    QMessageBox {
        background-color: {primary_color};
        color: {text_color};
    }

    QProgressBar {
            border: 0px solid {hover_color};
            text-align: center;
            background-color: {secondary_color};
            color: {text_color};
    }
    QProgressBar::chunk {
        background-color: {toggle_color};
    }

    QPushButton {
        background-color: {secondary_color};
        color: {text_color};
    }

    QPushButton:hover {
        background-color: {hover_color};
    }
    """

    for colorKey, colorValue in colors_dict.items():
        styleSheet = styleSheet.replace("{" + colorKey + "}", colorValue)
    return styleSheet

def format_eta(seconds) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{int(hours)}h {int(minutes)}m"
    elif minutes:
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        return f"{int(seconds)}s"

class DownloadThread(QtCore.QThread):
    setProgressBarTotalSignal = QtCore.pyqtSignal(int)
    updateProgressSignal = QtCore.pyqtSignal(int)
    labelTextSignal = QtCore.pyqtSignal(int)
    doneSignal = QtCore.pyqtSignal()

    def __init__(self, url, location):
        super().__init__()
        self.url = url
        self.location = location

    def run(self):
        response = requests.get(self.url, stream=True)
        total_size_in_bytes = response.headers.get('content-length')

        if total_size_in_bytes is None:
            self.setProgressBarTotalSignal.emit(-1)
        else:
            total_size_in_bytes = int(total_size_in_bytes)
            self.setProgressBarTotalSignal.emit(total_size_in_bytes)

        block_size = 1024 * 16
        try:
            response.raise_for_status()
            file = open(self.location, 'wb')

            start_time = time.time()
            total_data_received = 0
            last_emit_time = start_time
            data_received_since_last_emit = 0

            for data in response.iter_content(block_size):
                total_data_received += len(data)
                data_received_since_last_emit += len(data)

                file.write(data)
                if total_size_in_bytes is not None:
                    current_time = time.time()
                    if current_time - last_emit_time >= 1:
                        elapsed_time_since_last_emit = current_time - last_emit_time
                        download_speed = data_received_since_last_emit / elapsed_time_since_last_emit
                        logger.debug(f"Download speed: {download_speed / 1024 / 1024:.2f} MBps")

                        remaining_data = total_size_in_bytes - total_data_received
                        if download_speed != 0:
                            eta = int(remaining_data / download_speed)
                            logger.debug(f"ETA: {eta} seconds")
                            self.labelTextSignal.emit(eta)
                        self.updateProgressSignal.emit(int((total_data_received / total_size_in_bytes) * 100))
                        last_emit_time = current_time
                        data_received_since_last_emit = 0

            file.flush()
            file.close()

        except requests.exceptions.RequestException as e:
            logger.exception(e)
            if os.path.exists(self.location):
                os.remove(self.location)
            raise

        self.doneSignal.emit()


class DownloadDialog(QtWidgets.QDialog):
    def __init__(self, baseLabelText, url, location):
        super().__init__()
        self.setWindowTitle(translate_ui_text('Download'))
        self.previous_percent_completed = -1

        self.download_thread = DownloadThread(url, location)

        self.download_thread.setProgressBarTotalSignal.connect(self.set_progress_bar)
        self.download_thread.doneSignal.connect(lambda: self.done(0))
        self.download_thread.labelTextSignal.connect(self.set_eta)
        self.download_thread.updateProgressSignal.connect(self.update_progress_bar)

        self.layout = QtWidgets.QVBoxLayout()
        self.baseLabelText = translate_ui_text(baseLabelText)
        self.label = QtWidgets.QLabel(self.baseLabelText)

        self.layout.addWidget(self.label)
        self.progress = QtWidgets.QProgressBar(self)
        self.layout.addWidget(self.progress)
        self.setLayout(self.layout)

    def set_eta(self, ETASeconds):
        self.label.setText(f"{self.baseLabelText} ({format_eta(ETASeconds)})")

    def set_progress_bar(self, amount):
        if amount == -1:
            self.progress.setRange(0, 0)
        else:
            self.progress.setMaximum(100)

    def update_progress_bar(self, percent_completed):
        if percent_completed != self.previous_percent_completed:
            self.progress.setValue(percent_completed)
            self.previous_percent_completed = percent_completed

    def showEvent(self, event):
        super().showEvent(event)
        self.download_thread.start()

    def closeEvent(self, event):
        reply = QtWidgets.QMessageBox.question(
            self,
            translate_ui_text('Confirmation'),
            translate_ui_text('Are you sure you want to quit?'),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.download_thread.terminate()
            event.accept()
            app.exit(0)
            sys.exit(0)
        else:
            event.ignore()


def resolve_torch_wheel_url(reqfile):
    """Use uv's verbose dry-run to resolve the torch wheel URL directly."""
    try:
        result = subprocess.run(
            [uv_path, 'pip', 'install', '--dry-run', '-v', '--no-cache',
             '--python', venv_path, '-r', reqfile],
            capture_output=True, text=True, creationflags=subprocess_flags
        )
        output = result.stdout + "\n" + result.stderr
        logger.debug(f"uv dry-run output:\n{output}")
    except Exception as e:
        logger.error(f"Failed to resolve torch: {e}")
        return None

    # uv verbose output includes lines like:
    # No cache entry for: https://download-r2.pytorch.org/whl/cu121/torch-2.5.1%2Bcu121-cp311-cp311-win_amd64.whl#sha256=...
    for line in output.splitlines():
        match = re.search(r'(https?://\S+/torch-[^\s#]+\.whl)', line, re.IGNORECASE)
        if match:
            url = match.group(1)
            logger.debug(f"Resolved torch URL from uv: {url}")
            return url

    logger.debug("No torch download URL found in uv output.")
    return None


class PackageThread(QtCore.QThread):
    setLabelTextSignal = QtCore.pyqtSignal(str)
    setProgressMaxSignal = QtCore.pyqtSignal(int)
    updateProgressSignal = QtCore.pyqtSignal(int)
    doneSignal = QtCore.pyqtSignal()
    showErrorSignal = QtCore.pyqtSignal(str)
    downloadSignal = QtCore.pyqtSignal(str, str)
    def __init__(self, packages):
        super().__init__()
        self.packages = packages
        self.downloadDone = threading.Event()
    def run(self):
        total_packages = len(self.packages)

        for i, package in enumerate(self.packages):
            package: str
            try:

                completed_process = None
                if package.startswith("-r"):
                    reqfile = package[2:].strip()
                    logger.debug(f"Installing {package}")
                    self.setLabelTextSignal.emit(torchInstallText)

                    # Resolve torch wheel URL for manual download with progress
                    url = resolve_torch_wheel_url(reqfile)

                    if url is not None:
                        logger.debug(f"Torch wheel URL: {url}")
                        import urllib.parse
                        filename = urllib.parse.unquote(url.split("?")[0].rsplit("/", 1)[-1])
                        logger.debug(f"Filename: {filename}")

                        if os.path.exists(filename):
                            logger.debug("Deleting existing file...")
                            os.remove(filename)

                        if self.downloadDone.is_set():
                            self.downloadDone.clear()

                        self.downloadSignal.emit(url, filename)
                        self.downloadDone.wait()

                        if not os.path.exists(filename):
                            self.showErrorSignal.emit(f"An error occurred while installing package '{package}', we were unable to download the corresponding wheel.")
                            return

                        completed_process = uv_pip_install(filename)
                        logger.debug(completed_process.stdout)
                        os.remove(filename)

                    # Install remaining deps from the requirements file
                    completed_process = uv_pip_install('--upgrade', '-r', reqfile)
                else:
                    index = min([package.find(char) for char in ['=', '~', '>'] if package.find(char) != -1], default=-1)
                    packageName = package if index == -1 else package[:index]
                    logger.debug(f"Installing {packageName}")
                    self.setLabelTextSignal.emit(f"{normalInstallText} ({packageName})")

                    completed_process = uv_pip_install('--upgrade', package)
                if completed_process is not None:
                    logger.debug(completed_process.stdout)
                logger.debug(f"Current progress: {int((i + 1) / total_packages * 100)}%")
                self.updateProgressSignal.emit(int((i + 1) / total_packages * 100))
            except subprocess.CalledProcessError as e:
                self.showErrorSignal.emit(f"An error occurred while installing package '{package}':\n{e.stderr}")
                return

        self.doneSignal.emit()

class PackageDownloadDialog(QtWidgets.QDialog):
    def __init__(self, packages):
        super().__init__()
        self.setWindowTitle(translate_ui_text('Download Progress'))
        self.packages = packages

        self.previous_percent_completed = -1

        self.layout = QtWidgets.QVBoxLayout()
        self.label = QtWidgets.QLabel(normalInstallText)
        self.layout.addWidget(self.label)

        self.progress = QtWidgets.QProgressBar(self)
        self.progress.setMaximum(100)

        self.layout.addWidget(self.progress)

        self.setLayout(self.layout)

        self.packageThread = PackageThread(packages)
        self.packageThread.doneSignal.connect(lambda: self.done(0))
        self.packageThread.setLabelTextSignal.connect(self.setText)
        self.packageThread.updateProgressSignal.connect(self.update_progress_bar)
        self.packageThread.showErrorSignal.connect(self.showErrorAndExit)
        self.packageThread.downloadSignal.connect(self.downloadFile)

    def downloadFile(self, url, location):
        DownloadDialog("Downloading PyTorch", url, location).exec()
        self.packageThread.downloadDone.set()

    def showErrorAndExit(self, error):
        logger.error(error)
        QtWidgets.QMessageBox.critical(self, 'Error', error)
        sys.exit(1)

    def showEvent(self, event):
        super().showEvent(event)
        self.packageThread.start()

    def setText(self, newText:str):
        if self.label.text() != newText:
            self.label.setText(newText)


    def finish(self):
        self.done(0)

    def update_progress_bar(self, percent_completed):
        if percent_completed != self.previous_percent_completed:
            self.progress.setValue(percent_completed)
            self.previous_percent_completed = percent_completed

    def closeEvent(self, event):
        reply = QtWidgets.QMessageBox.question(
            self,
            translate_ui_text('Confirmation'),
            translate_ui_text('Are you sure you want to quit?'),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.packageThread.terminate()
            event.accept()
            app.exit(0)
            sys.exit(0)
        else:
            event.ignore()

def clone_or_pull(gitUrl, targetDirectory):
    if not os.path.exists(targetDirectory):
        porcelain.clone(gitUrl, target=targetDirectory)
    else:
        porcelain.pull(targetDirectory, gitUrl)

def run_startup(repo_dir, script):
    if os.path.exists(os.path.join(repo_dir, script)):
        previousDir = os.getcwd()
        os.chdir(repo_dir)
        try:
            subprocess.check_output([sys.executable, script], stderr=subprocess.STDOUT, creationflags=subprocess_flags)
        except subprocess.CalledProcessError as e:
            os.chdir(previousDir)

            error_message = e.output.decode('utf-8')
            sys.stderr.write(f"Startup script subprocess stderr:\n {error_message}\n")
            if "ModuleNotFoundError" in error_message:
                open("installing", "w").close()
                raise ValueError("Missing module.")
            else:
                raise


def check_requirements(repo_dir):
    req_file = os.path.join(repo_dir, 'requirements.txt')
    packages = []
    torch_req_file = os.path.join(repo_dir, 'requirements-torch.txt')
    if os.path.exists(torch_req_file):
        packages.append('-r ' + torch_req_file)

    if os.path.exists(req_file):
        with open(req_file, 'r') as f:
            lines = f.read().splitlines()

        packages += [line.split('#', 1)[0].strip() for line in lines if line.split('#', 1)[0].strip()]

    packages = [package for package in packages if "pyqt6" not in package.lower()]

    return packages


def check_if_latest(repo_path, remote_url) -> bool:
    gitRepo = dulwich.repo.Repo(repo_path)
    head = gitRepo[b"HEAD"]
    gitClient, path = client.get_transport_and_path(remote_url)
    remote_refs = gitClient.get_refs(path)
    return head.id == remote_refs[b"HEAD"]

app = QtWidgets.QApplication([])
def main():
    if "icon" in repoData:
        app.setWindowIcon(QtGui.QIcon(os.path.join(resource_dir, repoData["icon"])))
    app.setStyleSheet(get_stylesheet())
    repoURL = repoData["repo_url"]
    repoDir = repoData["repo_dir"]
    startupScript = repoData["startup_script"]

    if os.path.exists("installing") or not os.path.exists(repoDir) or not check_if_latest(repoDir, repoURL):
        open("installing", 'w').close()
        messageBox = QtWidgets.QMessageBox()
        messageBox.setWindowTitle(translate_ui_text("Update"))
        messageBox.setText(translate_ui_text("Updating Github repository..."))
        messageBox.setStandardButtons(QtWidgets.QMessageBox.StandardButton.NoButton)
        signalEmitter = SignalEmitter()
        signalEmitter.signal.connect(lambda: messageBox.done(0))
        def thread_func():
            clone_or_pull(repoURL, repoDir)
            signalEmitter.signal.emit()
        pullThread = threading.Thread(target=thread_func)
        pullThread.start()
        QtCore.QTimer.singleShot(1, lambda: (messageBox.activateWindow(), messageBox.raise_()))
        messageBox.show()
        app.exec()
        packages = check_requirements(repoDir)

        if len(packages) > 0:
            dialog = PackageDownloadDialog(packages)
            dialog.show()
            app.exec()
        os.remove("installing")
    try:
        run_startup(repoDir, startupScript)
        app.exit(0)
        sys.exit(0)
    except ValueError:
        exit(99)


if __name__ == "__main__":
    if os.name == "nt":
        import ctypes
        myappid = u'lugia19.installer'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    main()
