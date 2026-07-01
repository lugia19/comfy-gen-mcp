"""Cross-process single-instance guard for the HTTP runtime.

Only one real `--http` runtime may be alive at a time. Claude Desktop can spawn the stdio shim
twice (reconnect/reload), and two shims can race a cold start — both then spawn the runtime. We
arbitrate with an advisory exclusive file lock held for the process lifetime: it wins the
simultaneous-start race atomically (unlike a momentary port/`/alive` probe), and the OS releases
it automatically when the holder dies (unlike a PID file, so there's no stale-lock problem).
"""

import logging
import os
import time

from server.config import _APPDATA_DIR

log = logging.getLogger("comfy-mcp")


def _lock_path(port: int) -> str:
    """Lock file lives in the shared, update-surviving app-data dir (next to local_config.json /
    logs/). The port is in the name so a standalone instance on another port can't false-conflict."""
    return os.path.join(_APPDATA_DIR, f"runtime-{port}.lock")


def _try_lock(handle) -> bool:
    """Take a non-blocking exclusive lock on the open file. True on success, False if held."""
    fd = handle.fileno()
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_runtime_lock(port: int, wait_timeout: float = 0.0):
    """Acquire the single-instance lock for `port`, or return None if another runtime holds it.

    Returns the open file handle on success — the CALLER MUST KEEP IT ALIVE (store it in a
    module global) for the whole process, or it'll be GC'd/closed and the lock released. No
    explicit unlock is needed: the OS drops the lock when the process exits.

    wait_timeout > 0 retries until the deadline before giving up (used by the self-restart path,
    where the outgoing instance still holds the lock for a moment while it shuts ComfyUI down).
    """
    os.makedirs(_APPDATA_DIR, exist_ok=True)
    path = _lock_path(port)
    # Keep the handle open across retries so we don't churn file descriptors; we only re-attempt
    # the lock itself.
    handle = open(path, "a+")
    deadline = time.monotonic() + wait_timeout
    while True:
        if _try_lock(handle):
            return handle
        if time.monotonic() >= deadline:
            handle.close()
            return None
        time.sleep(0.5)
