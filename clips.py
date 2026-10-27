"""Dashcam clips: hardware-transcoded MP4 exports of a window of camera footage.

A clip is one MP4 plus one JSON sidecar in the clip store directory. The
sidecar is the source of truth: it records the request (route, camera, window,
bitrate) and the lifecycle (queued -> encoding -> ready | error). An MP4 with
no sidecar is not a clip and is ignored; a "ready" sidecar whose MP4 has gone
missing is stale and is cleaned up on listing.

The store lives outside the route data (realdata) and outside the install
directory, for two reasons: the device's route-retention logic deletes old
routes and must never take exported clips with them, and a redeploy replaces
the install directory wholesale, which would wipe every clip the user kept.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("connect")

# =============================================================================
# Store: sidecars, listing, naming
# =============================================================================

CAMERAS = {"road": "fcamera.hevc", "wide": "ecamera.hevc", "driver": "dcamera.hevc"}
BITRATES_MBPS = (5, 8, 12)
DEFAULT_BITRATE_MBPS = 8
MAX_CLIP_SEC = 120
MIN_CLIP_SEC = 1
BOOKMARK_PAD_SEC = 30

STATUS_QUEUED = "queued"
STATUS_ENCODING = "encoding"
STATUS_READY = "ready"
STATUS_ERROR = "error"


def default_filename(route_date: str, camera: str, start_sec: float, end_sec: float) -> str:
    """Default clip name (no extension), e.g. 2026-10-27--08-14-33_road_02m10s-02m50s."""
    s, e = int(start_sec), int(end_sec)
    return (f"{route_date}_{camera}_{s // 60:02d}m{s % 60:02d}s-"
            f"{e // 60:02d}m{e % 60:02d}s")


def validate_filename(name: str) -> str:
    """Return name if it is a bare, non-hidden .mp4 filename; else raise ValueError."""
    if (not name or name != os.path.basename(name) or "\\" in name
            or name.startswith(".") or not name.endswith(".mp4") or name == ".mp4"):
        raise ValueError(f"invalid clip filename: {name!r}")
    return name


class ClipStore:
    """Clip directory accessor. The directory is created lazily by write()."""

    def __init__(self, clips_dir: str):
        self.dir = Path(clips_dir)

    def sidecar_path(self, filename: str) -> Path:
        return self.dir / (Path(filename).stem + ".json")

    def mp4_path(self, filename: str) -> Path:
        return self.dir / filename

    def temp_path(self, filename: str) -> Path:
        return self.dir / f".{filename}"

    def lock_path(self, filename: str) -> Path:
        return self.dir / f".{filename}.lock"

    def write(self, clip: dict) -> None:
        """Atomically write the sidecar (tmp + rename) so readers never see a partial file."""
        os.makedirs(self.dir, exist_ok=True)
        path = self.sidecar_path(clip["filename"])
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(clip))
        os.replace(tmp, path)

    def read(self, filename: str) -> dict | None:
        try:
            return json.loads(self.sidecar_path(filename).read_text())
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict]:
        """All clips, newest request first. Drops (and deletes) ready sidecars with no MP4."""
        try:
            paths = [p for p in self.dir.glob("*.json") if not p.name.startswith(".")]
        except OSError:
            return []
        clips = []
        for p in paths:
            try:
                clip = json.loads(p.read_text())
            except (OSError, ValueError):
                log.warning("clips: unreadable sidecar %s", p)
                continue
            if (not isinstance(clip, dict) or not isinstance(clip.get("filename"), str)
                    or isinstance(clip.get("requested_at"), bool)
                    or not isinstance(clip.get("requested_at", 0), (int, float))):
                log.warning("clips: malformed sidecar %s; skipping", p)
                continue
            size = clip.get("size")
            if isinstance(size, bool) or not isinstance(size, (int, float)):
                clip["size"] = None
            if clip.get("status") == STATUS_READY and not self.mp4_path(clip["filename"]).exists():
                log.warning("clips: %s ready but MP4 missing; removing sidecar", clip["filename"])
                p.unlink(missing_ok=True)
                continue
            clips.append(clip)
        clips.sort(key=lambda c: c.get("requested_at") or 0, reverse=True)
        return clips

    def queued(self) -> list[dict]:
        """Queued clips, oldest request first (encode order)."""
        q = [c for c in self.list() if c.get("status") == STATUS_QUEUED]
        q.sort(key=lambda c: c.get("requested_at") or 0)
        return q

    def total_bytes(self) -> int:
        return sum(c.get("size") or 0 for c in self.list() if c.get("status") == STATUS_READY)

    def delete(self, filename: str) -> bool:
        """Remove sidecar, MP4, temp and lock. True if a sidecar or MP4 existed."""
        existed = False
        for p in (self.sidecar_path(filename), self.mp4_path(filename)):
            if p.exists():
                existed = True
            p.unlink(missing_ok=True)
        self.temp_path(filename).unlink(missing_ok=True)
        self.lock_path(filename).unlink(missing_ok=True)
        return existed

    def unique_filename(self, base: str) -> str:
        """base.mp4, else base_2.mp4, base_3.mp4, ... (first with no sidecar and no MP4)."""
        n = 1
        while True:
            name = f"{base}.mp4" if n == 1 else f"{base}_{n}.mp4"
            if not self.sidecar_path(name).exists() and not self.mp4_path(name).exists():
                return name
            n += 1
