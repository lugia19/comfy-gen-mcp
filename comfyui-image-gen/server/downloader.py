"""HuggingFace model downloader with thread-safe progress tracking."""

import hashlib
import logging
import os
import threading
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("comfy-mcp")

CHUNK_SIZE = 1024 * 1024  # 1 MB


@dataclass
class DownloadState:
    status: str = "idle"        # "idle" | "downloading" | "complete" | "error"
    current_file: str = ""
    current_bytes: int = 0      # bytes downloaded for current file
    total_bytes: int = 0        # total bytes for current file
    overall_bytes: int = 0      # cumulative bytes downloaded across all files
    overall_total: int = 0      # total bytes across all files to download
    file_index: int = 0
    file_count: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **kwargs):
        with self.lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "current_file": self.current_file,
                "current_bytes": self.current_bytes,
                "total_bytes": self.total_bytes,
                "overall_bytes": self.overall_bytes,
                "overall_total": self.overall_total,
                "file_index": self.file_index,
                "file_count": self.file_count,
                "error": self.error,
            }


def download_models(models_dir: str, models: list[dict], state: DownloadState):
    """Download missing model files. Takes an explicit models list."""
    log.info("Checking for missing models in: %s", models_dir)
    missing = []
    for model in models:
        dest = os.path.join(models_dir, model["subfolder"], model["filename"])
        if not os.path.isfile(dest):
            missing.append(model)
            log.info("  MISSING: %s/%s", model["subfolder"], model["filename"])
        else:
            log.info("  OK: %s/%s", model["subfolder"], model["filename"])

    if not missing:
        log.info("All models already present, nothing to download.")
        state.update(status="complete")
        return

    overall_total = sum(m["size_bytes"] for m in missing)
    log.info("Need to download %d model(s), total ~%.1f GB", len(missing), overall_total / 1_073_741_824)
    state.update(status="downloading", file_count=len(missing), overall_total=overall_total, overall_bytes=0)

    completed_bytes = 0

    for i, model in enumerate(missing):
        subfolder_dir = os.path.join(models_dir, model["subfolder"])
        os.makedirs(subfolder_dir, exist_ok=True)

        dest = os.path.join(subfolder_dir, model["filename"])
        part = dest + ".part"

        state.update(
            file_index=i,
            current_file=model["filename"],
            current_bytes=0,
            total_bytes=model["size_bytes"],
        )

        log.info("Downloading %s (%d/%d) from %s", model["filename"], i + 1, len(missing), model["url"])
        log.info("  Expected size: %d bytes (~%.1f GB)", model["size_bytes"], model["size_bytes"] / 1_073_741_824)
        log.info("  Dest: %s", dest)

        try:
            req = urllib.request.Request(model["url"], headers={"User-Agent": "comfyui-image-gen/1.0"})
            sha256 = hashlib.sha256()
            with urllib.request.urlopen(req) as resp, open(part, "wb") as f:
                content_length = resp.headers.get("Content-Length")
                log.info("  Server Content-Length: %s", content_length or "unknown")
                downloaded = 0
                last_log = 0
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha256.update(chunk)
                    downloaded += len(chunk)
                    state.update(current_bytes=downloaded, overall_bytes=completed_bytes + downloaded)
                    if downloaded - last_log >= 100 * 1024 * 1024:
                        pct = (downloaded / model["size_bytes"] * 100) if model["size_bytes"] else 0
                        log.info("  Progress: %.1f%% (%d / %d bytes)", pct, downloaded, model["size_bytes"])
                        last_log = downloaded

            # Verify checksum
            actual_hash = sha256.hexdigest()
            expected_hash = model.get("sha256")
            if expected_hash:
                if actual_hash != expected_hash:
                    os.remove(part)
                    raise ValueError(
                        f"Checksum mismatch for {model['filename']}: "
                        f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
                    )
                log.info("  SHA256 verified: %s", actual_hash[:16])

            os.replace(part, dest)
            completed_bytes += os.path.getsize(dest)
            state.update(overall_bytes=completed_bytes)
            log.info("Downloaded %s (%d bytes)", model["filename"], os.path.getsize(dest))

        except Exception as e:
            if os.path.exists(part):
                try:
                    os.remove(part)
                    log.info("  Cleaned up partial file: %s", part)
                except OSError:
                    log.warning("  Could not remove partial file: %s", part)
            state.update(status="error", error=f"Failed to download {model['filename']}: {e}")
            log.error("Download failed for %s: %s", model["filename"], e, exc_info=True)
            return

    state.update(status="complete")
    log.info("All %d model(s) downloaded successfully.", len(missing))
