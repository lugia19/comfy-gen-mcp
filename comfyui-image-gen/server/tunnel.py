"""Cloudflare tunnel and clipboard utilities for HTTP connector mode."""

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

    # Check next to our script
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


def show_tunnel_choice(local_cfg: dict, save_fn) -> bool:
    """Show a tkinter dialog for tunnel vs reverse proxy choice. Returns True for tunnel."""
    import tkinter as tk

    result = {"use_tunnel": True}  # default

    root = tk.Tk()
    root.title("ComfyUI Image Gen — Connection")
    root.resizable(False, False)
    root.geometry("400x220")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - 400) // 2
    y = (root.winfo_screenheight() - 220) // 2
    root.geometry(f"+{x}+{y}")

    frame = tk.Frame(root, padx=20, pady=15)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="How do you want to expose the server?", font=("", 12)).pack(pady=(0, 10))

    choice_var = tk.IntVar(value=1)
    tk.Radiobutton(frame, text="Cloudflare tunnel (easiest, URL changes on restart)", variable=choice_var, value=1, anchor="w").pack(fill="x")
    tk.Radiobutton(frame, text="I have my own domain / reverse proxy", variable=choice_var, value=2, anchor="w").pack(fill="x")

    remember_var = tk.BooleanVar(value=False)
    tk.Checkbutton(frame, text="Remember this choice", variable=remember_var).pack(pady=(10, 5))

    def on_start():
        result["use_tunnel"] = choice_var.get() == 1
        if remember_var.get():
            local_cfg["use_tunnel"] = result["use_tunnel"]
            save_fn(local_cfg)
            log.info("Saved tunnel preference: %s", result["use_tunnel"])
        root.destroy()

    tk.Button(frame, text="Start", command=on_start, width=15).pack(pady=(5, 0))

    root.protocol("WM_DELETE_WINDOW", lambda: (result.update({"use_tunnel": True}), root.destroy()))
    root.mainloop()

    return result["use_tunnel"]


def show_url_window(url: str):
    """Show a tkinter window with the MCP URL, copy button, and connector setup instructions."""
    import tkinter as tk

    root = tk.Tk()
    root.title("ComfyUI Image Gen — MCP Server")
    root.resizable(False, False)
    root.geometry("520x420")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - 520) // 2
    y = (root.winfo_screenheight() - 420) // 2
    root.geometry(f"+{x}+{y}")

    frame = tk.Frame(root, padx=20, pady=15)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="MCP Server Running", font=("", 14, "bold")).pack(pady=(0, 10))

    # URL display
    tk.Label(frame, text="MCP URL:", anchor="w").pack(fill="x")
    url_var = tk.StringVar(value=url)
    url_entry = tk.Entry(frame, textvariable=url_var, state="readonly", width=60)
    url_entry.pack(fill="x", pady=(2, 5))

    status_label = tk.Label(frame, text="", fg="green")
    status_label.pack()

    def do_copy():
        root.clipboard_clear()
        root.clipboard_append(url)
        copy_to_clipboard(url)
        status_label.config(text="Copied to clipboard!")

    tk.Button(frame, text="Copy URL", command=do_copy, width=15).pack(pady=(0, 10))

    # Instructions
    instructions = tk.Label(
        frame,
        text=(
            "How to add as a connector in Claude.ai:\n\n"
            "1) Go to claude.ai, click on Customize\n"
            "2) Click on Connectors\n"
            "3) Click on the + sign next to the search icon\n"
            "4) Click 'Add custom connector'\n"
            "5) Give it a name and paste the URL above\n"
            "6) Optional: Remove any old versions of the connector"
        ),
        justify="left",
        anchor="w",
        wraplength=460,
    )
    instructions.pack(fill="x", pady=(5, 10))

    tk.Label(
        frame,
        text="The server is running in the background.\nYou can close this window. Press Ctrl+C in the console to stop the server.",
        fg="gray",
    ).pack(pady=(5, 0))

    root.mainloop()
