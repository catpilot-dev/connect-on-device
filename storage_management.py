"""
Storage management for Connect on Device.

Handles route preservation, soft-deletion, disk cleanup, and download streaming.
Keeps server.py slim by isolating all storage logic here.

Cleanup is a wrapper around openpilot's stock deleter (system/loggerd/deleter.py)
with COD-specific logic:
- Recycled routes: auto-purge after 7 days
- COD-saved routes: respected unless emergency (<5GB)
- xattr-preserved routes (from comma cloud): always respected by COD
- Target: 10GB free (more conservative than stock 5GB)
"""

import io
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path

from route_store import _route_counter

logger = logging.getLogger("connect.storage")

# File types available for download
DOWNLOAD_FILES = {
    "rlog": ["rlog.zst", "rlog"],
    "qcamera": ["qcamera.ts"],
    "fcamera": ["fcamera.hevc"],
    "ecamera": ["ecamera.hevc"],
    "qlog": ["qlog.zst", "qlog"],
}

# COD cleanup thresholds
MIN_FREE_BYTES = 10 * 1024 * 1024 * 1024   # 10 GB — phase 1 threshold
EMERGENCY_BYTES = 5 * 1024 * 1024 * 1024    # 5 GB — phase 2 (emergency) threshold
RECYCLE_TTL = 7 * 86400                      # 7 days before recycled routes auto-purge


def get_storage_info(store) -> dict:
    """Get disk usage stats for the data directory."""
    stat = shutil.disk_usage(store.data_dir)
    return {
        "total": stat.total,
        "used": stat.used,
        "free": stat.free,
        "percent_free": round(stat.free / stat.total * 100, 1),
        "hidden_count": len(store._hidden),
        "preserved_count": len(store._preserved),
    }


def has_xattr_preserve(store, local_id: str) -> bool:
    """Check if any segment of a route has the user.preserve xattr (comma cloud)."""
    info = store._raw.get(local_id)
    if not info:
        return False
    for seg in info["segments"]:
        seg_path = Path(seg["path"])
        if not seg_path.exists():
            continue
        try:
            os.getxattr(str(seg_path), b"user.preserve")
            return True
        except OSError:
            pass
    return False


def _free_bytes(store) -> int:
    """Current free bytes on the data partition."""
    return shutil.disk_usage(store.data_dir).free


def run_cleanup(store) -> dict:
    """Single cleanup pass — COD wrapper around stock deleter logic.

    Phase 0: Expired recycled routes (>7 days) — always, regardless of storage.
    Phase 1: Normal routes (not saved, not xattr-preserved) — when free < 10GB.
    Phase 2: COD-saved routes — emergency only, when free < 5GB after phase 1.

    xattr-preserved routes (comma cloud) are never deleted by COD.

    Returns summary of actions taken.
    """
    now = time.time()
    deleted = []

    # ── Phase 0: Expired recycled routes (always) ───────────────────────
    expired = [
        (lid, hide_time) for lid, hide_time in list(store._hidden.items())
        if now - hide_time > RECYCLE_TTL and lid in store._raw
    ]
    for lid, hide_time in expired:
        age_days = (now - hide_time) / 86400
        _delete_route_from_disk(store, lid)
        deleted.append({"route": lid, "reason": "recycled_expired", "age_days": round(age_days, 1)})
        logger.info("Cleanup: purged expired recycled route %s (%.1f days old)", lid, age_days)

    # ── Phase 1: Normal routes when free < 10GB ─────────────────────────
    free = _free_bytes(store)
    if free < MIN_FREE_BYTES:
        # Candidates: not saved, not hidden, not xattr-preserved
        candidates = []
        for lid in list(store._raw.keys()):
            if lid in store._preserved or lid in store._hidden:
                continue
            if has_xattr_preserve(store, lid):
                continue
            candidates.append(lid)
        # Sort oldest first by route counter (lowest = oldest)
        candidates.sort(key=_route_counter)

        for lid in candidates:
            _delete_route_from_disk(store, lid)
            deleted.append({"route": lid, "reason": "low_storage"})
            logger.info("Cleanup: deleted normal route %s (low storage)", lid)
            free = _free_bytes(store)
            if free >= MIN_FREE_BYTES:
                break

    # ── Phase 2: Emergency — COD-saved routes when free < 5GB ───────────
    free = _free_bytes(store)
    if free < EMERGENCY_BYTES:
        saved = [lid for lid in list(store._preserved) if lid in store._raw]
        saved.sort(key=_route_counter)
        for lid in saved:
            if has_xattr_preserve(store, lid):
                continue
            _delete_route_from_disk(store, lid)
            deleted.append({"route": lid, "reason": "emergency"})
            logger.warning("Cleanup: deleted SAVED route %s (emergency, free < 5GB)", lid)
            free = _free_bytes(store)
            if free >= EMERGENCY_BYTES:
                break

    if deleted:
        store._save_metadata()

    return {"free_bytes": free, "deleted": deleted}


def _delete_route_from_disk(store, local_id: str):
    """Remove all segment directories for a route from disk and clean up state."""
    info = store._raw.get(local_id)
    if info:
        for seg in info["segments"]:
            seg_path = Path(seg["path"])
            if seg_path.exists():
                shutil.rmtree(seg_path, ignore_errors=True)

    store._hidden.pop(local_id, None)
    store._preserved.discard(local_id)
    store._raw.pop(local_id, None)
    store._metadata.pop(local_id, None)


def build_download_tar(store, local_id: str, file_types: list[str], segments: list[int] | None = None) -> io.BytesIO | None:
    """Build a tar.gz archive of requested files across segments.

    Args:
        store: RouteStore instance
        local_id: Route local_id (e.g. "00000042--abc123")
        file_types: List of file type keys from DOWNLOAD_FILES
        segments: Optional list of segment numbers to include (None = all)

    Returns:
        BytesIO containing tar.gz data, or None if no files found.
    """
    info = store._raw.get(local_id)
    if not info:
        return None

    buf = io.BytesIO()
    files_added = 0

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for seg in sorted(info["segments"], key=lambda s: s["number"]):
            if segments is not None and seg["number"] not in segments:
                continue
            seg_path = Path(seg["path"])
            seg_name = f"{local_id}--{seg['number']}"

            for ftype in file_types:
                candidates = DOWNLOAD_FILES.get(ftype, [])
                for fname in candidates:
                    fpath = seg_path / fname
                    if fpath.exists():
                        arcname = f"{seg_name}/{fname}"
                        tar.add(str(fpath), arcname=arcname)
                        files_added += 1
                        break  # use first match (e.g. rlog.zst over rlog)

    if files_added == 0:
        return None

    buf.seek(0)
    return buf
