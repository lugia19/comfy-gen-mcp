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

from server.config import load_local_config, save_local_config
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


def _spawn_server() -> None:
    """Spawn the real HTTP server detached, marked as managed by this shim."""
    global _spawn_attempted
    if _spawn_attempted or os.environ.get("SHIM_NO_SPAWN"):
        return
    _spawn_attempted = True

    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = [sys.executable, "-m", "server.main", "--http", "--managed-by", str(os.getpid())]
    log.info("Spawning HTTP server: %s (cwd=%s)", args, cwd)
    kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    try:
        subprocess.Popen(args, **kwargs)
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


# ── Shim server ───────────────────────────────────────────────────────

def build_server(mcp_url: str, alive_url: str) -> Server:
    """Build the low-level stdio Server that proxies to the HTTP server."""
    server = Server("Comfy-Gen-MCP")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        # The tool list is computed locally from the bundle — correct even before the HTTP
        # server is up. Kick off a spawn so the server is warming by the time a tool is
        # called, but don't wait on it.
        _spawn_server()
        return _shim_tools()

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
    for the rest of the session (a --managed-by server self-shuts when the pings go stale).

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
