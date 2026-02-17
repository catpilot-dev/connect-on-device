"""
Storage management for Connect on Device.

Handles route preservation, soft-deletion, disk cleanup, and download streaming.
Keeps server.py slim by isolating all storage logic here.
"""

import io
import logging
import shutil
import tarfile
import time
from pathlib import Path

logger = logging.getLogger("connect.storage")

# File types available for download
DOWNLOAD_FILES = {
    "rlog": ["rlog.zst", "rlog"],
    "qcamera": ["qcamera.ts"],
    "fcamera": ["fcamera.hevc"],
    "ecamera": ["ecamera.hevc"],
    "qlog": ["qlog.zst", "qlog"],
}


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


def run_cleanup(store) -> dict:
    """Run storage cleanup based on free space thresholds.

    Called during scan(). Deletes:
    - Hidden routes when free < 20% (oldest first)
    - Non-preserved routes when free < 10% (oldest first)
    - Preserved routes when free < 10% and non-preserved exhausted (last resort)

    Returns summary of actions taken.
    """
    stat = shutil.disk_usage(store.data_dir)
    free_pct = stat.free / stat.total * 100
    deleted = []

    if free_pct >= 20:
        return {"free_pct": round(free_pct, 1), "deleted": []}

    # Phase 1: Delete hidden routes (oldest first) when < 20% free
    if store._hidden:
        hidden_with_mtime = []
        for local_id in list(store._hidden):
            info = store._raw.get(local_id)
            if info:
                hidden_with_mtime.append((local_id, info["mtime"]))
        hidden_with_mtime.sort(key=lambda x: x[1])

        for local_id, _mtime in hidden_with_mtime:
            _delete_route_from_disk(store, local_id)
            deleted.append({"route": local_id, "reason": "hidden"})
            logger.info("Cleanup: deleted hidden route %s", local_id)

            stat = shutil.disk_usage(store.data_dir)
            free_pct = stat.free / stat.total * 100
            if free_pct >= 20:
                break

    if free_pct >= 10:
        store._save_metadata()
        return {"free_pct": round(free_pct, 1), "deleted": deleted}

    # Phase 2: Delete non-preserved, non-hidden routes (oldest first) when < 10% free
    normal_routes = []
    for local_id, info in store._raw.items():
        if local_id not in store._preserved and local_id not in store._hidden:
            normal_routes.append((local_id, info["mtime"]))
    normal_routes.sort(key=lambda x: x[1])

    for local_id, _mtime in normal_routes:
        _delete_route_from_disk(store, local_id)
        deleted.append({"route": local_id, "reason": "space_critical"})
        logger.info("Cleanup: deleted normal route %s (space critical)", local_id)

        stat = shutil.disk_usage(store.data_dir)
        free_pct = stat.free / stat.total * 100
        if free_pct >= 10:
            break

    # Phase 3: Last resort — delete preserved routes (oldest first)
    if free_pct < 10 and store._preserved:
        preserved_routes = []
        for local_id in list(store._preserved):
            info = store._raw.get(local_id)
            if info:
                preserved_routes.append((local_id, info["mtime"]))
        preserved_routes.sort(key=lambda x: x[1])

        for local_id, _mtime in preserved_routes:
            _delete_route_from_disk(store, local_id)
            deleted.append({"route": local_id, "reason": "space_emergency"})
            logger.warning("Cleanup: deleted PRESERVED route %s (emergency)", local_id)

            stat = shutil.disk_usage(store.data_dir)
            free_pct = stat.free / stat.total * 100
            if free_pct >= 10:
                break

    if deleted:
        store._save_metadata()

    return {"free_pct": round(free_pct, 1), "deleted": deleted}


def _delete_route_from_disk(store, local_id: str):
    """Remove all segment directories for a route from disk and clean up state."""
    info = store._raw.get(local_id)
    if info:
        for seg in info["segments"]:
            seg_path = Path(seg["path"])
            if seg_path.exists():
                shutil.rmtree(seg_path, ignore_errors=True)

    store._hidden.discard(local_id)
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
