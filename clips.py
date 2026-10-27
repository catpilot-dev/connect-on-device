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
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

from config import FFMPEG_BIN
from storage_management import EMERGENCY_BYTES

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


# =============================================================================
# Request validation: window, cameras, inputs, provenance, admission
# =============================================================================

SEGMENT_SEC = 60
FRAME_RATE = 20
_CAMERA_BY_FILE = {v: k for k, v in CAMERAS.items()}
_FRAME_COUNT_CACHE: dict[str, int] = {}
FRAME_COUNT_SETTLE_SEC = 60  # don't cache counts of files modified more recently than this


class ClipRequestError(ValueError):
    """A clip request that cannot be accepted. status: 400 bad input, 409 footage/space."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def available_ranges(route: dict) -> dict[str, list[list[int]]]:
    """Per camera, merged [start_sec, end_sec] runs of consecutive recorded segments."""
    numbers: dict[str, list[int]] = {}
    for seg in route.get("_segments", []):
        for fname, cam in _CAMERA_BY_FILE.items():
            if fname in seg["files"]:
                numbers.setdefault(cam, []).append(seg["number"])
    out: dict[str, list[list[int]]] = {}
    for cam, nums in numbers.items():
        runs: list[list[int]] = []
        for n in sorted(nums):
            if runs and runs[-1][1] == n * SEGMENT_SEC:
                runs[-1][1] = (n + 1) * SEGMENT_SEC
            else:
                runs.append([n * SEGMENT_SEC, (n + 1) * SEGMENT_SEC])
        out[cam] = runs
    return out


def _ffprobe_bin() -> str:
    sibling = Path(FFMPEG_BIN).with_name("ffprobe")
    return str(sibling) if sibling.exists() else "ffprobe"


def segment_frame_count(path: str) -> int:
    """Packet count of one .hevc (cached: a recorded segment never changes)."""
    cached = _FRAME_COUNT_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        # A route's last segment can still be growing just after ignition off.
        settled = time.time() - os.path.getmtime(path) >= FRAME_COUNT_SETTLE_SEC
    except OSError:
        settled = False
    try:
        out = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-count_packets", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60, check=True).stdout
        count = int(out.strip().split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        raise ClipRequestError(409, f"Cannot read footage length: {e}") from e
    if settled:
        _FRAME_COUNT_CACHE[path] = count
    return count


def clamp_window(route: dict, store, start_sec: float, end_sec: float,
                 camera: str) -> tuple[float, float, bool]:
    """Clamp end to the real footage length when the window reaches the last segment.

    The last segment is usually shorter than 60 s (recording stopped mid-segment),
    so the nominal end would run past the footage.
    """
    fname = CAMERAS[camera]
    segs = route.get("_segments", [])
    if not segs:
        return start_sec, end_sec, False
    # Anchor on the route's last segment, not the camera's: a camera that stopped
    # early must surface as a missing-footage 409 in collect_inputs, not a silent clamp.
    last = max(s["number"] for s in segs)
    if end_sec <= last * SEGMENT_SEC:
        return start_sec, end_sec, False
    path = store.resolve_segment_path(route["fullname"], last, fname)
    if path is None:
        return start_sec, end_sec, False
    real_end = last * SEGMENT_SEC + segment_frame_count(str(path)) / FRAME_RATE
    if end_sec <= real_end:
        return start_sec, end_sec, False
    if real_end - start_sec <= 0:
        raise ClipRequestError(
            409, f"Cannot export: no {camera} footage for segment {int(start_sec // SEGMENT_SEC)}")
    if real_end - start_sec < MIN_CLIP_SEC:
        raise ClipRequestError(400, "Clip must be at least 1 second of recorded footage")
    return start_sec, real_end, True


def collect_inputs(route: dict, store, camera: str, start_sec: float, end_sec: float) -> list[str]:
    """Consecutive <camera>.hevc paths covering [start, end); 409 if any segment lacks one."""
    fname = CAMERAS[camera]
    first = int(start_sec // SEGMENT_SEC)
    last = max(first, math.ceil(end_sec / SEGMENT_SEC) - 1)
    paths = []
    for seg in range(first, last + 1):
        p = store.resolve_segment_path(route["fullname"], seg, fname)
        if p is None:
            raise ClipRequestError(409, f"Cannot export: no {camera} footage for segment {seg}")
        paths.append(str(p))
    return paths


def _free_bytes(store) -> int:
    return shutil.disk_usage(store.data_dir).free


def check_admission(store, estimate_bytes: int) -> None:
    """Refuse a clip that would leave less than the emergency-cleanup line free."""
    free = _free_bytes(store)
    if free - estimate_bytes < EMERGENCY_BYTES:
        raise ClipRequestError(
            409, f"Not enough free space: {free} bytes free, clip needs {estimate_bytes}")


def _frame_times(store, fullname: str, local_id: str, segment: int):
    from handlers.media import _frame_times_cached  # heavy import, keep lazy
    return _frame_times_cached(store, fullname, local_id, segment)


def provenance(route: dict, store, camera: str, start_sec: float, end_sec: float) -> dict:
    """Where/when the clip was recorded; wall time and GPS are None when logs are missing."""
    seg_no = int(start_sec // SEGMENT_SEC)
    wall = None
    try:
        times = _frame_times(store, route["fullname"], route.get("_local_id"), seg_no)
        idx = int((start_sec % SEGMENT_SEC) * FRAME_RATE)
        if times and 0 <= idx < len(times):
            wall = times[idx]
    except Exception:
        log.warning("clips: frame times unavailable for segment %s", seg_no, exc_info=True)

    gps = None
    seg = next((s for s in route.get("_segments", []) if s["number"] == seg_no), None)
    if seg:
        try:
            from handlers.media import _lookup_gps  # heavy import, keep lazy
            coords = json.loads((Path(seg["path"]) / "coords.json").read_text())
            pt = _lookup_gps(coords, start_sec) if coords else None
            if pt and pt.get("lat") is not None and pt.get("lng") is not None:
                gps = {"lat": pt["lat"], "lng": pt["lng"]}
        except (OSError, ValueError, TypeError, KeyError):
            gps = None
    return {"wall_start_epoch": wall, "gps": gps, "op_version": route.get("version"),
            "git_branch": route.get("git_branch"), "git_commit": route.get("git_commit"),
            "platform": route.get("platform")}


def _number(body: dict, key: str) -> float:
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ClipRequestError(400, f"{key} must be a number of seconds")
    return v


def _validate_body(body: dict) -> tuple[float, float, list[str], float, str | None]:
    start, end = _number(body, "start_sec"), _number(body, "end_sec")
    if start < 0:
        raise ClipRequestError(400, "start_sec must not be negative")
    if end - start < MIN_CLIP_SEC:
        raise ClipRequestError(400, f"Clip must be at least {MIN_CLIP_SEC} second long")
    if end - start > MAX_CLIP_SEC:
        raise ClipRequestError(400, f"Clip can be at most {MAX_CLIP_SEC // 60} minutes long")
    cams = body.get("cameras")
    if not isinstance(cams, list) or not cams:
        raise ClipRequestError(400, "Choose at least one camera")
    for c in cams:
        if not isinstance(c, str) or c not in CAMERAS:
            raise ClipRequestError(400, f"Unknown camera: {c!r}")
    cams = list(dict.fromkeys(cams))
    bitrate = body.get("bitrate_mbps", DEFAULT_BITRATE_MBPS)
    if isinstance(bitrate, bool) or bitrate not in BITRATES_MBPS:
        raise ClipRequestError(400, f"Invalid bitrate; choose one of {list(BITRATES_MBPS)} Mbps")
    name = body.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ClipRequestError(400, "Invalid clip name")
        name = name.strip()
        if name.endswith(".mp4"):
            name = name[:-4]
        try:
            validate_filename(name + ".mp4")
        except ValueError:
            raise ClipRequestError(400, f"Invalid clip name: {name!r}") from None
    return start, end, cams, bitrate, name or None


def build_requests(route: dict, store, body: dict, clip_store: ClipStore, now: float) -> list[dict]:
    """Validate a request body and return one queued sidecar dict per camera.

    Nothing is written; the caller persists the sidecars. Admission is checked
    once against the summed estimate so a multi-camera request is all-or-nothing.
    """
    start, end, cams, bitrate, name = _validate_body(body)
    plans = []
    for cam in cams:
        s, e, clamped = clamp_window(route, store, start, end, cam)
        inputs = collect_inputs(route, store, cam, s, e)
        duration = e - s
        plans.append((cam, s, e, clamped, inputs, duration,
                      int(duration * bitrate * 1_000_000 // 8)))
    check_admission(store, sum(p[6] for p in plans))

    route_date = route["fullname"].split("/")[-1]
    taken: set[str] = set()
    out = []
    for cam, s, e, clamped, inputs, duration, estimate in plans:
        if name:
            base = f"{name}_{cam}" if len(cams) > 1 else name
        else:
            base = default_filename(route_date, cam, s, e)
        filename = clip_store.unique_filename(base)
        n = 2
        while filename in taken:
            filename = clip_store.unique_filename(f"{base}_{n}")
            n += 1
        taken.add(filename)
        out.append({
            "filename": filename, "route": route["fullname"], "camera": cam,
            "start_sec": s, "end_sec": e, "bitrate_mbps": bitrate,
            "status": STATUS_QUEUED, "requested_at": now, "inputs": inputs,
            "encode_start_sec": s - int(s // SEGMENT_SEC) * SEGMENT_SEC,
            "duration_sec": duration, "estimate_bytes": estimate, "clamped": clamped,
            "provenance": provenance(route, store, cam, s, e),
        })
    return out
