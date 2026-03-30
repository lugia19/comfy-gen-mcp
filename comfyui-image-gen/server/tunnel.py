"""Cloudflare tunnel and clipboard utilities for HTTP connector mode.

UI functions (show_tunnel_choice, show_url_window, show_server_running_window)
have been moved to server/ui.py (PyQt6).
"""

import io
import logging
import os
import platform
import re
import subprocess
import time

import httpx

log = logging.getLogger("comfy-mcp")

CLOUDFLARED_RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def copy_to_clipboard(text: str) -> bool:
    try:
        if platform.system() == "Windows":
            subprocess.run("clip", input=text.encode(), check=True)
        elif platform.system() == "Darwin":
            subprocess.run("pbcopy", input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except Exception:
        return False


def get_cloudflared_path() -> str:
    """Find cloudflared, or download it if missing."""
    name = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, name)
        if os.path.isfile(candidate):
            return candidate

    ext_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(ext_dir, name)
    if os.path.isfile(local):
        return local

    return _download_cloudflared(local)


def _download_cloudflared(dest: str) -> str:
    """Download the cloudflared binary to dest."""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        asset = "cloudflared-windows-amd64.exe" if machine in ("amd64", "x86_64") else "cloudflared-windows-386.exe"
        url = f"{CLOUDFLARED_RELEASES}/{asset}"
        log.info("Downloading cloudflared from %s", url)
        resp = httpx.get(url, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)

    elif system == "Darwin":
        import tarfile
        asset = "cloudflared-darwin-arm64.tgz" if machine == "arm64" else "cloudflared-darwin-amd64.tgz"
        url = f"{CLOUDFLARED_RELEASES}/{asset}"
        log.info("Downloading cloudflared from %s", url)
        resp = httpx.get(url, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("cloudflared"):
                    member.name = os.path.basename(member.name)
                    tar.extract(member, path=os.path.dirname(dest))
                    break
        os.chmod(dest, 0o755)

    else:
        raise RuntimeError(
            "Auto-download not supported on this platform. "
            "Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        )

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    log.info("Downloaded cloudflared (%.1f MB) to %s", size_mb, dest)
    return dest


def start_cloudflare_tunnel(port: int) -> tuple[subprocess.Popen, str]:
    """Start a temporary cloudflare tunnel and return (process, url)."""
    cloudflared = get_cloudflared_path()
    log.info("Starting cloudflare tunnel on port %d...", port)

    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    for _ in range(60):
        line = proc.stdout.readline().decode(errors="replace")
        match = re.search(r"(https://[^\s]+\.trycloudflare\.com)", line)
        if match:
            url = match.group(1)
            log.info("Cloudflare tunnel URL: %s", url)
            return proc, url
        time.sleep(0.5)

    proc.kill()
    raise RuntimeError("Timed out waiting for cloudflared URL.")
