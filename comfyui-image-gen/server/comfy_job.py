"""ComfyJob — encapsulates a single image generation job."""

import asyncio
import base64
import io
import json
import logging
import secrets
import threading
import time

import httpx
from PIL import Image

from server.config import JPEG_QUALITY, MAX_IMAGE_SIZE
from server.workflow import build_prompt

log = logging.getLogger("comfy-mcp")

RESPONSE_TIMEOUT = 55  # seconds — must be under Claude's connector/tool timeout


class ComfyJob:
    """Manages a single image generation request to ComfyUI."""

    def __init__(self, prompt_text: str, pack: dict, aspect_ratio: str, comfyui_url: str):
        self.token = secrets.token_urlsafe(16)
        self.prompt_text = prompt_text
        self.pack = pack
        self.aspect_ratio = aspect_ratio
        self.comfyui_url = comfyui_url

        self.status = "queued"  # "queued" | "running" | "done" | "error"
        self.prompt_id: str | None = None
        self.progress: tuple[int, int] | None = None  # (current_step, total_steps)
        self.result: bytes | None = None
        self.error: str | None = None
        self.started = time.time()

        self._thread: threading.Thread | None = None

    def start(self):
        """Spawn background thread to submit workflow and track progress."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Job %s started for pack '%s'", self.token, self.pack.get("name", "?"))

    def _run(self):
        """Background thread: submit, track via websocket + queue, fetch result."""
        try:
            # Build workflow
            wf = build_prompt(
                self.pack["workflow"],
                self.prompt_text,
                self.pack["prompt_node_id"],
                self.pack["seed_nodes"],
                dimension_nodes=self.pack.get("dimension_nodes"),
                aspect_ratio=self.aspect_ratio,
                max_pixels=self.pack.get("max_pixels", 1_048_576),
            )

            # Submit to ComfyUI
            resp = httpx.post(f"{self.comfyui_url}/prompt", json={"prompt": wf}, timeout=30)
            if resp.status_code != 200:
                body = resp.text[:1000]
                log.error("ComfyUI /prompt returned %d: %s", resp.status_code, body)
                raise RuntimeError(f"ComfyUI rejected the workflow (HTTP {resp.status_code}): {body}")

            self.prompt_id = resp.json()["prompt_id"]
            self.status = "running"
            log.info("Job %s queued as prompt_id=%s", self.token, self.prompt_id)

            # Connect websocket for progress (best-effort)
            ws = None
            try:
                import websocket
                ws_url = self.comfyui_url.replace("http://", "ws://").replace("https://", "wss://")
                ws = websocket.WebSocket()
                ws.settimeout(0.5)
                ws.connect(f"{ws_url}/ws")
                log.info("Job %s: websocket connected", self.token)
            except Exception as e:
                log.info("Job %s: websocket connection failed (progress tracking disabled): %s", self.token, e)
                ws = None

            # Poll queue + websocket until job leaves the queue
            poll_count = 0
            try:
                while True:
                    # Check websocket for progress messages
                    if ws:
                        self._drain_websocket(ws)

                    # Check queue status
                    try:
                        qresp = httpx.get(f"{self.comfyui_url}/queue", timeout=3)
                        if qresp.status_code == 200:
                            queue = qresp.json()
                            running_ids = [item[1] for item in queue.get("queue_running", [])]
                            pending_ids = [item[1] for item in queue.get("queue_pending", [])]

                            if self.prompt_id in running_ids:
                                self.status = "running"
                                if poll_count % 10 == 0:
                                    log.info("Job %s running (poll #%d)", self.token, poll_count)
                            elif self.prompt_id in pending_ids:
                                pos = pending_ids.index(self.prompt_id) + 1
                                if poll_count % 10 == 0:
                                    log.info("Job %s pending, position %d/%d (poll #%d)",
                                             self.token, pos, len(pending_ids), poll_count)
                            else:
                                log.info("Job %s left queue after %d polls", self.token, poll_count)
                                break
                    except Exception as e:
                        log.debug("Job %s: queue poll failed: %s", self.token, e)

                    poll_count += 1
                    time.sleep(1)
            finally:
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass

            # Fetch result from history
            resp = httpx.get(f"{self.comfyui_url}/history/{self.prompt_id}", timeout=30)
            history = resp.json()
            if self.prompt_id not in history:
                raise RuntimeError(f"Job {self.prompt_id} disappeared — not in queue or history. It may have been cancelled.")

            entry = history[self.prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                err_msgs = entry.get("status", {}).get("messages", [])
                log.error("ComfyUI execution error for %s: %s", self.prompt_id, err_msgs)
                raise RuntimeError(f"ComfyUI execution failed: {err_msgs}")

            # Fetch image
            log.info("Job %s completed, fetching image...", self.token)
            for node_output in entry.get("outputs", {}).values():
                if "images" in node_output:
                    img = node_output["images"][0]
                    log.info("Fetching image: %s", img["filename"])
                    img_resp = httpx.get(
                        f"{self.comfyui_url}/view",
                        params={
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output"),
                        },
                        timeout=30,
                    )
                    self.result = img_resp.content
                    self.status = "done"
                    log.info("Job %s done, %d bytes", self.token, len(self.result))
                    return

            raise RuntimeError(f"Job {self.prompt_id} completed but produced no images.")

        except Exception as e:
            self.error = str(e)
            self.status = "error"
            log.error("Job %s failed: %s", self.token, e)

    def _drain_websocket(self, ws):
        """Read all pending websocket messages and update progress for OUR prompt only."""
        while True:
            try:
                raw = ws.recv()
                if isinstance(raw, bytes):
                    continue  # skip binary (preview images)
                msg = json.loads(raw)
                msg_type = msg.get("type")
                data = msg.get("data", {})

                # Only process messages for our prompt_id
                msg_prompt_id = data.get("prompt_id")
                if msg_prompt_id and msg_prompt_id != self.prompt_id:
                    continue

                if msg_type == "progress":
                    self.progress = (data["value"], data["max"])
                    log.info("Job %s progress: %d/%d", self.token, data["value"], data["max"])
                elif msg_type == "executing":
                    # Reset progress when a new node starts executing
                    if data.get("node") is not None:
                        log.info("Job %s executing node: %s", self.token, data.get("node"))
                    else:
                        # node=None means execution finished
                        log.info("Job %s execution finished (via websocket)", self.token)
            except Exception:
                break  # timeout or closed — no more messages

    def get_status_message(self) -> str:
        """Human-readable status string for timeout messages."""
        # Websocket progress takes priority
        if self.progress:
            current, total = self.progress
            pct = int(current / total * 100) if total > 0 else 0
            return f"Step {current}/{total} ({pct}%)"

        # Fall back to queue position check
        if self.prompt_id:
            try:
                resp = httpx.get(f"{self.comfyui_url}/queue", timeout=3)
                if resp.status_code == 200:
                    queue = resp.json()
                    running_ids = [item[1] for item in queue.get("queue_running", [])]
                    pending_ids = [item[1] for item in queue.get("queue_pending", [])]

                    if self.prompt_id in running_ids:
                        return "Currently being generated"
                    if self.prompt_id in pending_ids:
                        pos = pending_ids.index(self.prompt_id) + 1
                        return f"Position {pos} of {len(pending_ids)} in queue"
            except Exception:
                pass

        return "Generating..."

    @staticmethod
    def process_image(img_bytes: bytes) -> str:
        """Convert raw image bytes → resized JPEG → base64 string."""
        img = Image.open(io.BytesIO(img_bytes))
        log.info("Raw image size: %dx%d", img.size[0], img.size[1])
        max_total_pixels = MAX_IMAGE_SIZE * MAX_IMAGE_SIZE
        if img.size[0] * img.size[1] > max_total_pixels:
            scale = (max_total_pixels / (img.size[0] * img.size[1])) ** 0.5
            new_w = int(img.size[0] * scale)
            new_h = int(img.size[1] * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            log.info("Resized to: %dx%d", img.size[0], img.size[1])
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode()
        log.info("JPEG encoded, %d bytes base64", len(b64))
        return b64

    @staticmethod
    def cleanup_old(jobs: dict[str, 'ComfyJob'], max_age: int = 600):
        """Remove jobs older than max_age seconds."""
        cutoff = time.time() - max_age
        stale = [t for t, j in jobs.items() if j.started < cutoff]
        for t in stale:
            del jobs[t]
            log.info("Cleaned up stale job %s", t)


async def wait_for_job(job: ComfyJob) -> 'CallToolResult':
    """Poll a job for up to RESPONSE_TIMEOUT seconds. Return image or token for retry."""
    from mcp.types import CallToolResult, ImageContent, TextContent

    start = time.time()
    while time.time() - start < RESPONSE_TIMEOUT:
        if job.status == "done":
            b64 = ComfyJob.process_image(job.result)
            return CallToolResult(
                content=[ImageContent(type="image", data=b64, mimeType="image/jpeg")]
            )
        if job.status == "error":
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    f"Error: {job.error}\n\n"
                    "If this error persists, try closing ComfyUI from the system tray and retrying. "
                    "If that doesn't help, restarting your PC can clear ghost ComfyUI instances."
                ))]
            )
        await asyncio.sleep(1)

    # Timed out — return token with status info
    status_msg = job.get_status_message()
    return CallToolResult(
        content=[TextContent(type="text", text=(
            f"Generation is still in progress. {status_msg}. "
            f"Use the fetch_result tool with request_token '{job.token}' to retrieve the result."
        ))]
    )
