"""Thin stdio->HTTP shim (DXT entrypoint).

Claude Desktop launches this over stdio. It warms up the real HTTP server (the one
standalone users run, with its tray/setup UI/ComfyUI management) and proxies every MCP
call to it. All real logic lives in the HTTP server; this module only bridges transports
and manages the server's lifetime via a keepalive.

Endpoint resolution can be overridden for testing via SHIM_MCP_URL / SHIM_ALIVE_URL, and
spawning can be disabled with SHIM_NO_SPAWN=1 (point it at a server you started by hand).
"""

import logging
import os
import shutil
import subprocess
import sys
import time

import anyio
import httpx
import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from server.config import LOCAL_CONFIG_PATH, load_local_config, save_local_config
from server.tool_specs import build_tool_specs

log = logging.getLogger("comfy-mcp.shim")

DEFAULT_MCP_PORT = 9247
SPAWN_GRACE_MESSAGE = (
    "The image generation server is starting up. Please try again in a few seconds."
)

# Background warm/keepalive cadence (seconds)
KEEPALIVE_INTERVAL = 15
READINESS_POLL_INTERVAL = 1.0
READINESS_SPAWN_TIMEOUT = 120.0


# ── Endpoint resolution ───────────────────────────────────────────────

def _resolve_endpoints() -> tuple[str, str]:
    """Return (mcp_url, alive_url). Ensures mcp_path exists in local_config.json so the
    shim and the server it spawns agree on the URL even on first run."""
    if os.environ.get("SHIM_MCP_URL"):
        return os.environ["SHIM_MCP_URL"], os.environ.get("SHIM_ALIVE_URL", "")

    cfg = load_local_config()
    port = cfg.get("mcp_port", DEFAULT_MCP_PORT)
    mcp_path = cfg.get("mcp_path")
    if not mcp_path:
        import secrets
        mcp_path = f"/mcp/{secrets.token_urlsafe(32)}"
        cfg["mcp_path"] = mcp_path
        save_local_config(cfg)
        log.info("Generated new MCP path (saved to local_config.json)")

    base = f"http://127.0.0.1:{port}"
    return base + mcp_path, base + "/alive"


# ── Tool list ──────────────────────────────────────────────────────────

def _shim_tools() -> list[types.Tool]:
    """The correct tool list, computed locally from the bundled packs + local_config.

    No server / cache needed — see server.tool_specs. Claude Desktop caches the first
    list_tools result and won't refresh mid-session, so this must be right from the start.
    """
    return [types.Tool(**spec) for spec in build_tool_specs()]


# ── Server liveness + spawn ───────────────────────────────────────────

async def _is_alive(alive_url: str) -> bool:
    if not alive_url:
        return True  # test mode: assume the hand-started server is up
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(alive_url)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


_spawn_attempted = False

# ── Self-updating runtime (bootstrapper) ──────────────────────────────
# The packaged mcpb ships a Go bootstrapper dist under <bundle>/bootstrap/. Instead of
# running this frozen bundled copy of the server, the shim launches that bootstrapper, which
# git-pulls the latest server code into a stable runtime dir and runs it. So bug fixes that
# land in server code reach users WITHOUT reinstalling the extension. Only the shim itself,
# the tool list (tool_specs + packs), and the bootstrapper dist stay frozen until a reinstall.
_BOOTSTRAP_EXE = "comfyui-image-gen-mcp.exe"
_BUNDLED_BOOTSTRAP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bootstrap"
)
# Stable, space-free, survives extension updates/uninstalls (unlike the extension dir).
_RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".comfy-gen-mcp", "runtime")
_WIN_DETACHED = 0x00000008  # DETACHED_PROCESS


def _bootstrap_available() -> bool:
    """True when this mcpb ships the bootstrapper dist (the normal packaged case). False when
    running from a source checkout, on non-Windows, or when SHIM_NO_BOOTSTRAP forces direct
    spawn — all of which fall back to running this checkout's server in place."""
    return (
        sys.platform == "win32"
        and not os.environ.get("SHIM_NO_BOOTSTRAP")
        and os.path.isfile(os.path.join(_BUNDLED_BOOTSTRAP_DIR, _BOOTSTRAP_EXE))
    )


def _sync_bootstrap_dist() -> str:
    """Copy the bundled bootstrapper dist into the stable runtime dir, refreshing only files
    that changed (so a newer mcpb updates the dist) and skipping any that are locked — e.g.
    the .exe of an instance that's still running, whose existing copy is what we'd use anyway.
    Returns the runtime exe path."""
    os.makedirs(_RUNTIME_DIR, exist_ok=True)
    for name in os.listdir(_BUNDLED_BOOTSTRAP_DIR):
        src = os.path.join(_BUNDLED_BOOTSTRAP_DIR, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(_RUNTIME_DIR, name)
        try:
            if os.path.isfile(dst):
                s, d = os.stat(src), os.stat(dst)
                if s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) < 2:
                    continue  # unchanged (copy2 preserves mtime) — leave it
            shutil.copy2(src, dst)  # copy2 preserves mtime for the comparison above
        except OSError as e:
            log.warning("Could not refresh bootstrap file %s: %s", name, e)
    return os.path.join(_RUNTIME_DIR, _BOOTSTRAP_EXE)


def _popen_detached(args: list[str], cwd: str, env: dict) -> None:
    kwargs = {"cwd": cwd, "env": env,
              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | _WIN_DETACHED
    subprocess.Popen(args, **kwargs)


def _spawn_server() -> None:
    """Spawn the real HTTP server detached.

    Normally this launches the bundled bootstrapper (self-updating runtime); from a source
    checkout it falls back to running this checkout's server directly. The server is pointed at
    the shim's config file via COMFY_CONFIG_PATH so they agree on the mcp_path token (the
    bootstrapper chain doesn't forward CLI args, so it goes through env). No managed flag is
    passed: the shim's /alive keepalive pings (see _warm_and_keepalive) arm the server's
    self-shutdown on their own."""
    global _spawn_attempted
    if _spawn_attempted or os.environ.get("SHIM_NO_SPAWN"):
        return
    _spawn_attempted = True

    env = os.environ.copy()
    env["COMFY_CONFIG_PATH"] = LOCAL_CONFIG_PATH

    if _bootstrap_available():
        try:
            exe = _sync_bootstrap_dist()
            log.info("Spawning self-updating runtime via bootstrapper: %s (cwd=%s)", exe, _RUNTIME_DIR)
            _popen_detached([exe], _RUNTIME_DIR, env)
            return
        except Exception as e:
            log.error("Bootstrapper spawn failed (%s); falling back to direct spawn", e)

    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = [sys.executable, "-m", "server.main", "--http"]
    log.info("Spawning HTTP server directly: %s (cwd=%s)", args, cwd)
    try:
        _popen_detached(args, cwd, env)
    except Exception as e:
        log.error("Failed to spawn HTTP server: %s", e)


# ── Upstream session helper ───────────────────────────────────────────

async def _with_session(mcp_url: str, op):
    """Open a fresh streamable-http session, run op(session), return its result.

    The server runs stateless_http, so a per-call session (initialize + op) is correct
    and naturally survives server restarts.
    """
    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await op(session)


async def _live_tools(mcp_url: str, alive_url: str) -> list[types.Tool] | None:
    """The running server's authoritative tool list, or None if it isn't reachable.

    Prefer this over the locally-computed _shim_tools(): the server is the source of truth (it
    reflects the live config and can't drift), and on a Claude Desktop reconnect this is what
    surfaces settings changes. Falls back to None (→ local specs) before the server is up — e.g.
    the very first run, where the server is still warming for minutes."""
    if not await _is_alive(alive_url):
        return None
    try:
        with anyio.fail_after(5):
            result = await _with_session(mcp_url, lambda s: s.list_tools())
        return list(result.tools)
    except Exception as e:
        log.warning("Live list_tools failed (%s); falling back to local specs", e)
        return None


# ── Shim server ───────────────────────────────────────────────────────

def build_server(mcp_url: str, alive_url: str) -> Server:
    """Build the low-level stdio Server that proxies to the HTTP server."""
    server = Server("Comfy-Gen-MCP")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        # Only spawn when the server isn't already up. An unconditional spawn here starts a
        # duplicate on every Claude Desktop reload (Ctrl+R): the reload relaunches the shim,
        # resetting _spawn_attempted, while the detached HTTP server is still alive. Mirror
        # _call_tool's alive-guarded spawn.
        if not await _is_alive(alive_url):
            _spawn_server()
            return _shim_tools()
        # Server is up: prefer its authoritative list (reflects live config, can't drift),
        # falling back to local specs on a transient read error.
        live = await _live_tools(mcp_url, alive_url)
        return live if live is not None else _shim_tools()

    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
        if not await _is_alive(alive_url):
            _spawn_server()
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=SPAWN_GRACE_MESSAGE)]
            )
        try:
            return await _with_session(mcp_url, lambda s: s.call_tool(name, arguments))
        except Exception as e:
            log.error("Upstream call_tool('%s') failed: %s", name, e)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Image server error: {e}")],
                isError=True,
            )

    return server


async def _warm_and_keepalive(alive_url: str) -> None:
    """Background task: spawn the HTTP server and wait for it to come up, then keepalive-ping
    for the rest of the session. These pings arm the server's self-shutdown: it stays alive
    while we ping and shuts down once they go stale (shim gone).

    The tool list no longer depends on this — it's computed locally in _list_tools."""
    try:
        deadline = time.monotonic() + READINESS_SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            if await _is_alive(alive_url):
                break
            _spawn_server()
            await anyio.sleep(READINESS_POLL_INTERVAL)

        if not alive_url:
            return

        # Keepalive only: each ping arms the server's self-shutdown (it stays up while we ping,
        # shuts down once they go stale). We deliberately do NOT respawn on missed pings — a
        # missed ping is not a reliable death signal (a 2s timeout is easily exceeded while
        # ComfyUI is generating under load), and a false positive spawns a duplicate that races
        # the live server for the port and leaves a zombie window. Planned restarts are handled
        # by the server re-execing itself; a genuine crash is recovered by restarting Claude
        # Desktop (which respawns the shim + server fresh).
        while True:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.get(alive_url)
            except httpx.HTTPError:
                pass
            await anyio.sleep(KEEPALIVE_INTERVAL)
    except Exception:
        # A crash here silently stops all pings → the managed server shuts down after the
        # grace window. Log it so that failure isn't invisible.
        log.exception("Keepalive task crashed")
        raise


async def _run_async() -> None:
    mcp_url, alive_url = _resolve_endpoints()
    log.info("Shim starting: mcp_url=%s alive=%s", mcp_url, alive_url or "(test mode)")
    server = build_server(mcp_url, alive_url)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_warm_and_keepalive, alive_url)
        async with stdio_server() as (read, write):
            init_options = server.create_initialization_options()
            await server.run(read, write, init_options)
        tg.cancel_scope.cancel()


def run() -> None:
    """Entry point for DXT/stdio mode."""
    anyio.run(_run_async)


if __name__ == "__main__":
    run()
