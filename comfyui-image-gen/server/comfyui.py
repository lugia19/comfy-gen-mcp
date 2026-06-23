"""ComfyUI management via comfy-cli — detection, installation, launching, and health checks."""

import logging
import os
import platform
import shutil
import subprocess
import sys
import time

import httpx

from .config import COMFYUI_DEFAULT_PORT, COMFYUI_DEFAULT_URL

log = logging.getLogger("comfy-mcp")

LAUNCH_TIMEOUT = 120

# Detection cache. comfy-cli cold-starts slowly (seconds) and we look up the cli path,
# the ComfyUI workspace, and the GPU repeatedly during startup + the setup wizard. These
# don't change within a run except across an install/uninstall, so memoize them and
# invalidate explicitly via reset_detection_cache().
_DETECT_CACHE: dict = {}


def reset_detection_cache() -> None:
    """Clear cached comfy-cli / ComfyUI-workspace / GPU detection results.

    Call after installing or removing ComfyUI so the next lookup re-detects.
    """
    _DETECT_CACHE.clear()
    log.debug("Detection cache cleared")


def _comfy_env() -> dict[str, str]:
    """Build an environment dict for comfy-cli subprocesses.

    Strips VIRTUAL_ENV and CONDA_PREFIX so comfy-cli uses the workspace's
    own venv instead of the extension's.
    """
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("CONDA_PREFIX", None)
    # Force the child Python (comfy-cli) to use UTF-8 for its own stdout/stderr.
    # Otherwise it inherits Windows' legacy cp1252 codec and crashes with a
    # UnicodeEncodeError the moment it prints a non-Latin-1 char (e.g. the "→"
    # in comfy-cli's "Detected CUDA driver version: X → using Y" message).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_comfy(comfy_cli: str, *args: str, timeout: int = 60,
               cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a comfy-cli command with the correct environment.

    Handles env cleanup, encoding, and logging. ``cwd`` controls the working
    directory; comfy-cli's dependency compiler writes its override.txt to a
    path resolved against cwd, so it must be space-free (see install_comfyui).
    """
    cmd = [comfy_cli, *args]
    log.info("Running: %s (cwd=%s)", cmd, cwd)
    return subprocess.run(
        cmd,
        env=_comfy_env(),
        cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


# ── comfy-cli detection ──────────────────────────────────────────────

def find_comfy_cli() -> str | None:
    """Find the comfy-cli executable.

    Checks PATH first, then the current Python environment's Scripts/bin directory.
    Result is cached for the process (see reset_detection_cache()).
    """
    if "comfy_cli" in _DETECT_CACHE:
        return _DETECT_CACHE["comfy_cli"]
    result = _find_comfy_cli_uncached()
    _DETECT_CACHE["comfy_cli"] = result
    return result


def _find_comfy_cli_uncached() -> str | None:
    found = shutil.which("comfy")
    if found:
        log.info("Found comfy-cli on PATH: %s", found)
        return found

    if platform.system() == "Windows":
        venv_comfy = os.path.join(sys.prefix, "Scripts", "comfy.exe")
    else:
        venv_comfy = os.path.join(sys.prefix, "bin", "comfy")

    if os.path.isfile(venv_comfy):
        log.info("Found comfy-cli in venv: %s", venv_comfy)
        return venv_comfy

    log.info("comfy-cli not found")
    return None


# ── ComfyUI installation detection ──────────────────────────────────

def _comfy_which(comfy_cli: str) -> str | None:
    """Run `comfy which` and return the ComfyUI workspace path, or None.

    Cached per cli path (see reset_detection_cache()) — `comfy which` cold-starts
    comfy-cli, which is slow on a fresh machine.
    """
    key = ("which", comfy_cli)
    if key in _DETECT_CACHE:
        return _DETECT_CACHE[key]
    result = _comfy_which_uncached(comfy_cli)
    # Cache even a None ("not installed yet") so the repeated lookups during the setup
    # wizard don't each re-run comfy-cli. install/remove call reset_detection_cache() so a
    # fresh install is re-detected immediately afterward.
    _DETECT_CACHE[key] = result
    return result


def _comfy_which_uncached(comfy_cli: str) -> str | None:
    try:
        result = _run_comfy(comfy_cli, "which", timeout=15)
        if result.returncode != 0:
            log.debug("comfy which failed (exit %d): %s", result.returncode, result.stderr.strip())
            return None

        for line in result.stdout.splitlines():
            if "Target ComfyUI path:" in line:
                path = line.split(":", 1)[1].strip()
                if path and os.path.isdir(path):
                    return path
        return None
    except Exception as e:
        log.debug("Failed to run comfy which: %s", e)
        return None


def find_comfyui_installation(comfy_cli: str) -> str | None:
    """Find an existing ComfyUI installation via `comfy which`."""
    path = _comfy_which(comfy_cli)
    if path:
        log.info("ComfyUI installation: %s", path)
    else:
        log.info("ComfyUI installation not found")
    return path


def find_models_dir(comfy_cli: str) -> str | None:
    """Find the ComfyUI models directory by querying comfy-cli.

    Cached once found (see reset_detection_cache()). Without this, the post-install UI
    refresh re-runs this on the main thread and each call cold-shells out to comfy-cli
    (seconds each), freezing the window.
    """
    if "models_dir" in _DETECT_CACHE:
        return _DETECT_CACHE["models_dir"]
    result = _find_models_dir_uncached(comfy_cli)
    if result is not None:  # don't cache "not installed yet"; install() primes it
        _DETECT_CACHE["models_dir"] = result
    return result


def _find_models_dir_uncached(comfy_cli: str) -> str | None:
    """Falls back to checking the default install directory if comfy which points elsewhere."""
    install_path = find_comfyui_installation(comfy_cli)
    if install_path:
        models_dir = os.path.join(install_path, "models")
        if os.path.isdir(models_dir):
            log.info("Models dir: %s", models_dir)
            return models_dir
        log.warning("Expected models dir does not exist: %s", models_dir)

    # Fallback: check our default install location
    fallback = os.path.join(_default_install_dir(), "models")
    if os.path.isdir(fallback):
        log.info("Models dir from default install location: %s", fallback)
        default_install = _default_install_dir()
        try:
            _run_comfy(comfy_cli, "set-default", default_install, timeout=10)
            log.info("Corrected default workspace to: %s", default_install)
        except Exception:
            pass
        return fallback

    return None


# ── ComfyUI installation ────────────────────────────────────────────

def _detect_gpu() -> str:
    """Detect GPU type. Returns 'nvidia', 'amd', 'mac', or 'cpu'. Cached per process."""
    if "gpu" in _DETECT_CACHE:
        return _DETECT_CACHE["gpu"]
    result = _detect_gpu_uncached()
    _DETECT_CACHE["gpu"] = result
    return result


def _detect_gpu_uncached() -> str:
    if platform.system() == "Darwin":
        return "mac"

    try:
        subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
        return "nvidia"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(["rocminfo"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return "amd"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "cpu"


def _default_install_dir() -> str:
    """Return the default directory for installing ComfyUI."""
    return os.path.join(os.path.expanduser("~"), ".comfy-gen-mcp", "comfyui")


def _force_remove_readonly(_func, path, _exc_info):
    """onerror handler for shutil.rmtree — clears read-only flag and retries."""
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)
    except Exception:
        pass


def _kill_processes_in_dir(directory: str):
    """Kill Python processes whose command line references the given directory."""
    if platform.system() != "Windows":
        return
    norm_dir = os.path.normpath(directory).lower()
    my_pid = str(os.getpid())
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        for line in result.stdout.splitlines():
            if norm_dir not in line.lower():
                continue
            parts = line.strip().split(",")
            pid = parts[-1].strip() if parts else ""
            if pid.isdigit() and pid != my_pid:
                log.info("Killing python process %s using install dir", pid)
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
    except Exception as e:
        log.debug("Process kill failed: %s", e)


def remove_comfyui_dir(directory: str, comfy_cli: str | None = None) -> bool:
    """Stop ComfyUI, kill lingering processes, and delete the directory.

    Returns True if successfully deleted.
    """
    if not os.path.isdir(directory):
        return True

    if comfy_cli:
        stop_comfyui(comfy_cli)

    _kill_processes_in_dir(directory)

    for attempt in range(5):
        try:
            shutil.rmtree(directory, onerror=_force_remove_readonly)
            log.info("Deleted ComfyUI directory: %s", directory)
            reset_detection_cache()  # installation state changed
            return True
        except Exception as e:
            log.warning("Delete attempt %d failed: %s", attempt + 1, e)
            if attempt < 4:
                _kill_processes_in_dir(directory)
                time.sleep(2)

    log.error("Failed to delete directory after 5 attempts: %s", directory)
    return False


def install_comfyui(comfy_cli: str, gpu: str | None = None, install_dir: str | None = None) -> str:
    """Install ComfyUI via comfy-cli.

    Returns the installation directory path.
    Raises RuntimeError on failure.
    """
    if gpu is None:
        gpu = _detect_gpu()
    if install_dir is None:
        install_dir = _default_install_dir()

    install_parent = os.path.dirname(install_dir)
    os.makedirs(install_parent, exist_ok=True)

    if os.path.isdir(install_dir):
        remove_comfyui_dir(install_dir, comfy_cli)

    cli_args = ["--skip-prompt", "--workspace", install_dir, "install", "--fast-deps"]
    gpu_flag_map = {"nvidia": "--nvidia", "amd": "--amd", "mac": "--m-series", "cpu": "--cpu"}
    if gpu in gpu_flag_map:
        cli_args.append(gpu_flag_map[gpu])

    # Run from the install dir's parent, NOT our (extension) cwd. comfy-cli's
    # dependency compiler writes override.txt to a path resolved against the
    # working dir and hands it to `uv` unquoted; if that path contains a space
    # (our extension lives under "...\Claude Extensions\...") uv splits it and
    # fails with "File not found: ...\Claude". The install parent is space-free
    # for normal install locations.
    if " " in install_parent:
        log.warning("Install path contains a space (%s); comfy-cli/uv may fail. "
                    "Consider installing ComfyUI to a space-free location.", install_parent)
    result = _run_comfy(comfy_cli, *cli_args, timeout=600, cwd=install_parent)

    if result.returncode != 0:
        log.error("comfy install failed:\nstdout: %s\nstderr: %s", result.stdout, result.stderr)
        raise RuntimeError(f"ComfyUI installation failed (exit {result.returncode}): {result.stderr.strip()}")

    log.info("ComfyUI installed successfully")

    # We passed --workspace install_dir, so ComfyUI lives at install_dir itself (or, on
    # some comfy-cli versions, a nested ComfyUI/ subdir). Trust those before falling back
    # to `comfy which`, which needs a default workspace that we only set below.
    def _looks_like_comfyui(d: str) -> bool:
        return os.path.isfile(os.path.join(d, "main.py")) and os.path.isdir(os.path.join(d, "models"))

    install_path = None
    for candidate in (install_dir, os.path.join(install_dir, "ComfyUI")):
        if _looks_like_comfyui(candidate):
            install_path = candidate
            break
    if not install_path:
        install_path = _comfy_which(comfy_cli)
    if not install_path:
        raise RuntimeError("ComfyUI installed but installation path could not be determined")

    try:
        _run_comfy(comfy_cli, "set-default", install_path, timeout=10)
        log.info("Set default ComfyUI workspace: %s", install_path)
    except Exception as e:
        log.warning("Failed to set default workspace: %s", e)

    # We just installed it and know exactly where it is. Prime the detection cache with the
    # known-good path so the wizard's post-install UI refresh (find_models_dir /
    # find_comfyui_installation, called on the main thread) returns instantly instead of
    # cold-shelling out to comfy-cli — which is seconds-slow here and, worse, `comfy which`
    # reports "not found" right after install, sending find_models_dir into a fallback that
    # re-runs `set-default` on every call and freezes the window.
    reset_detection_cache()
    _DETECT_CACHE[("which", comfy_cli)] = install_path
    models = os.path.join(install_path, "models")
    if os.path.isdir(models):
        _DETECT_CACHE["models_dir"] = models
    return install_path


# ── ComfyUI URL detection ───────────────────────────────────────────

def _check_url(url: str) -> bool:
    """Check if ComfyUI is responding at a specific URL."""
    try:
        status = httpx.get(f"{url}/system_stats", timeout=2).status_code
        return status == 200
    except Exception:
        return False


def find_comfyui_url(custom_url: str | None = None) -> str | None:
    """Find a running ComfyUI instance."""
    url = custom_url or COMFYUI_DEFAULT_URL
    if _check_url(url):
        log.info("ComfyUI responding at %s", url)
        return url

    if custom_url and custom_url != COMFYUI_DEFAULT_URL:
        log.info("ComfyUI not responding at configured URL: %s", custom_url)
        return None

    log.info("ComfyUI not responding at %s", url)
    return None


# ── ComfyUI launching ───────────────────────────────────────────────

# Last ComfyUI launch failure, surfaced in the server window's status poll (get_launch_error).
# None while ComfyUI is starting/healthy; set to a user-facing message on a failed launch so the
# window can show "failed to start" and nudge the user toward Reinstall instead of hanging on
# "starting...".
_launch_error: list[str | None] = [None]


def get_launch_error() -> str | None:
    return _launch_error[0]


def set_launch_error(msg: str | None) -> None:
    _launch_error[0] = msg


def launch_comfyui(comfy_cli: str, port: int = COMFYUI_DEFAULT_PORT,
                   custom_url: str | None = None) -> tuple[subprocess.Popen, str]:
    """Launch ComfyUI in the background via comfy-cli and wait until it's ready.

    Returns (process, url).
    """
    url = custom_url or f"http://127.0.0.1:{port}"
    set_launch_error(None)  # optimistic: new attempt clears any prior failure

    log.info("Launching ComfyUI via comfy-cli: port=%d", port)
    args = [comfy_cli, "launch", "--", "--port", str(port)]

    # Log ComfyUI output to a file (in our shared logs/ dir) to avoid pipe deadlocks
    from .config import ensure_logs_dir
    comfyui_log = os.path.join(ensure_logs_dir(), "comfyui.log")
    log.info("ComfyUI output log: %s", comfyui_log)
    log_fh = open(comfyui_log, "w", encoding="utf-8")

    proc = subprocess.Popen(
        args,
        env=_comfy_env(),
        stdout=log_fh,
        stderr=log_fh,
    )

    for i in range(LAUNCH_TIMEOUT):
        if _check_url(url):
            log.info("ComfyUI is ready at %s (took ~%ds)", url, i)
            return proc, url
        if proc.poll() is not None:
            log_fh.close()
            log.error("ComfyUI exited during startup (code %d), see %s", proc.returncode, comfyui_log)
            set_launch_error(
                f"ComfyUI exited during startup (exit code {proc.returncode}). "
                "See the log (Settings → Open Logs Folder) for details — a Reinstall often fixes this."
            )
            raise RuntimeError(f"ComfyUI exited during startup (exit code {proc.returncode}). Check {comfyui_log}")
        if i % 10 == 0 and i > 0:
            log.info("Still waiting for ComfyUI to start... (%ds)", i)
        time.sleep(1)

    log.error("ComfyUI failed to start within %d seconds", LAUNCH_TIMEOUT)
    proc.terminate()
    log_fh.close()
    set_launch_error(
        f"ComfyUI did not start within {LAUNCH_TIMEOUT}s. "
        "See the log (Settings → Open Logs Folder) for details — a Reinstall often fixes this."
    )
    raise TimeoutError(f"ComfyUI did not start within {LAUNCH_TIMEOUT} seconds.")


def stop_comfyui(comfy_cli: str, process: subprocess.Popen | None = None):
    """Stop ComfyUI — kills the process tree if we have a handle, otherwise uses comfy stop."""
    log.info("Stopping ComfyUI...")
    if process and process.poll() is None:
        if platform.system() == "Windows":
            # /T kills the entire process tree (comfy launch → python main.py)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
            )
        else:
            import signal
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.info("ComfyUI process tree terminated")
    else:
        try:
            _run_comfy(comfy_cli, "stop", timeout=10)
        except Exception as e:
            log.warning("Failed to stop ComfyUI: %s", e)


# ── Custom node management ──────────────────────────────────────────

_object_info_cache: dict | None = None


def clear_object_info_cache():
    """Clear the cached /object_info response (needed after node installation)."""
    global _object_info_cache
    _object_info_cache = None
    log.info("Cleared object_info cache")


def check_required_nodes(url: str, required: dict[str, str]) -> list[str]:
    """Check if required ComfyUI custom nodes are installed.

    Returns list of missing package names (empty if all present).
    """
    global _object_info_cache
    if _object_info_cache is None:
        try:
            resp = httpx.get(f"{url}/object_info", timeout=10)
            resp.raise_for_status()
            _object_info_cache = resp.json()
            log.info("Loaded object_info: %d node types available", len(_object_info_cache))
        except Exception as e:
            log.error("Failed to fetch /object_info from %s: %s", url, e)
            return []

    missing = []
    for class_type, package_name in required.items():
        if class_type not in _object_info_cache:
            log.warning("Required node %s (%s) not found in ComfyUI", class_type, package_name)
            if package_name not in missing:
                missing.append(package_name)
    return missing


def ensure_manager_installed(comfy_cli: str) -> bool:
    """Ensure ComfyUI-Manager is git cloned into custom_nodes/ (before ComfyUI starts).

    The manager is needed for `comfy node install` to work. Other custom nodes
    are installed via `comfy node install` after ComfyUI is running.
    Returns True if manager is available.
    """
    install_path = _comfy_which(comfy_cli)
    if not install_path:
        log.warning("Cannot ensure manager: ComfyUI installation not found")
        return False

    custom_nodes_dir = os.path.join(install_path, "custom_nodes")
    manager_dir = os.path.join(custom_nodes_dir, "ComfyUI-Manager")
    if os.path.isdir(manager_dir):
        log.info("ComfyUI-Manager already installed")
        return True

    os.makedirs(custom_nodes_dir, exist_ok=True)
    log.info("ComfyUI-Manager not found, installing via git clone...")
    try:
        result = subprocess.run(
            ["git", "clone", "https://github.com/ltdrdata/ComfyUI-Manager.git"],
            cwd=custom_nodes_dir,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode == 0:
            log.info("ComfyUI-Manager installed successfully")
            return True
        log.error("Failed to clone ComfyUI-Manager: %s", result.stderr.strip())
    except Exception as e:
        log.error("Failed to install ComfyUI-Manager: %s", e)
    return False


def install_custom_nodes(comfy_cli: str, nodes: list[str]) -> list[str]:
    """Install custom nodes via comfy-cli (when ComfyUI is running). Returns list of failures."""
    failed = []
    for node in nodes:
        log.info("Installing custom node via comfy-cli: %s", node)
        try:
            result = _run_comfy(comfy_cli, "node", "install", node, timeout=300)
            if result.returncode != 0:
                log.error("Failed to install node %s: %s", node, result.stderr.strip())
                failed.append(node)
            else:
                log.info("Successfully installed node: %s", node)
        except Exception as e:
            log.error("Error installing node %s: %s", node, e)
            failed.append(node)
    return failed


# ── Model and image helpers ──────────────────────────────────────────

def check_model_exists(models_dir: str, subfolder: str, filename: str) -> bool:
    """Check if a model file exists in the expected location."""
    path = os.path.join(models_dir, subfolder, filename)
    exists = os.path.isfile(path)
    log.debug("Model check: %s -> %s", path, "EXISTS" if exists else "MISSING")
    return exists


def upload_image(comfyui_url: str, file_path: str) -> str:
    """Upload a local image to ComfyUI's input directory. Returns the uploaded filename."""
    log.info("Uploading image to ComfyUI: %s", file_path)
    with open(file_path, "rb") as f:
        resp = httpx.post(
            f"{comfyui_url}/upload/image",
            files={"image": (os.path.basename(file_path), f)},
            timeout=30,
        )
    resp.raise_for_status()
    name = resp.json()["name"]
    log.info("Uploaded as: %s", name)
    return name
