#!/usr/bin/env python3
"""
Connect on Device - comma-compatible API server for local route browsing.

Implements the same REST API as api.comma.ai so the asiusai/connect React
frontend works unchanged. Serves route data from /data/media/0/realdata/.

Usage:
  On C3:  /usr/local/venv/bin/python /data/connect_on_device/server.py
  Local:  python server.py --data-dir ~/driving_data/data
"""

import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
import shutil
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

from storage_management import get_storage_info, run_cleanup, build_download_tar, DOWNLOAD_FILES

logger = logging.getLogger("connect")

# Route directory pattern: {count}--{uid}--{segment}
ROUTE_DIR_RE = re.compile(r"^(\w+--\w+)--(\d+)$")

DEFAULT_DATA_DIR = "/data/media/0/realdata"
DEFAULT_PORT = 8082
CACHE_TTL = 120  # seconds — route scan is expensive with metadata
METADATA_FILE = "metadata.json"

# Add openpilot to path for LogReader
for p in ["/data/openpilot", "/home/oxygen/openpilot"]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ─── rlog reading ─────────────────────────────────────────────────────

def _iter_rlog(rlog_path: str):
    """Iterate over rlog messages using cereal directly (avoids LogReader path validation)."""
    from cereal import log as capnp_log
    import zstandard as zstd

    if rlog_path.endswith(".zst"):
        with open(rlog_path, "rb") as f:
            data = zstd.ZstdDecompressor().stream_reader(f).read()
    else:
        data = open(rlog_path, "rb").read()

    return capnp_log.Event.read_multiple_bytes(data)


# ─── Route metadata parsing ───────────────────────────────────────────

def _parse_rlog_metadata(rlog_path: str) -> dict:
    """Parse initData + first GPS + segment distance from an rlog file.

    GPS lock can take 30-60+ seconds (24,000+ messages), so we scan the
    entire rlog — the file is already in memory from _iter_rlog anyway.
    Also accumulates GPS distance for the segment.
    """
    meta = {}
    has_init = False
    has_gps = False
    has_car = False
    last_lat = last_lng = None
    seg_dist = 0.0

    try:
        for ev in _iter_rlog(rlog_path):
            w = ev.which()
            if w == "initData" and not has_init:
                init = ev.initData
                meta["dongle_id"] = init.dongleId
                meta["git_commit"] = init.gitCommit
                meta["git_branch"] = init.gitBranch
                meta["version"] = init.version
                wtn = getattr(init, "wallTimeNanos", 0)
                if wtn:
                    meta["wall_time_nanos"] = wtn
                    meta["create_time"] = wtn / 1e9
                has_init = True
            elif w == "carParams" and not has_car:
                try:
                    meta["car_fingerprint"] = ev.carParams.carFingerprint
                except Exception:
                    pass
                has_car = True
            elif w == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                lat, lng = gps.latitude, gps.longitude
                has_fix = bool(gps.flags & 1)
                has_coords = lat != 0 and lng != 0
                # Accept unfixed GPS for location (good enough for timezone)
                if has_coords and not has_gps:
                    meta["start_lat"] = lat
                    meta["start_lng"] = lng
                    has_gps = True
                # Only use fixed GPS for distance calculation (unfixed can jitter)
                if has_fix and has_coords:
                    if last_lat is not None:
                        seg_dist += _haversine_dist(last_lat, last_lng, lat, lng)
                    last_lat, last_lng = lat, lng
    except Exception as e:
        logger.debug("rlog parse error: %s", e)

    if seg_dist > 0:
        meta["segment_distance_m"] = seg_dist

    return meta


def _find_first_gps(rlog_path: str) -> tuple | None:
    """Fast GPS-only scan — returns (lat, lng) or None.
    Accepts unfixed GPS (flags=0) if coordinates are non-zero,
    since that's sufficient for timezone estimation."""
    try:
        for ev in _iter_rlog(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.latitude != 0 and gps.longitude != 0:
                    return (gps.latitude, gps.longitude)
    except Exception:
        pass
    return None


def _segment_gps_distance(rlog_path: str) -> float:
    """Calculate GPS distance in meters for a single segment's rlog."""
    last_lat = last_lng = None
    dist = 0.0
    try:
        for ev in _iter_rlog(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.flags & 1:
                    lat, lng = gps.latitude, gps.longitude
                    if last_lat is not None:
                        dist += _haversine_dist(last_lat, last_lng, lat, lng)
                    last_lat, last_lng = lat, lng
    except Exception:
        pass
    return dist


def _haversine_dist(lat1, lon1, lat2, lon2):
    """Distance in meters between two GPS points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _generate_coords_json(rlog_path: str, segment_num: int) -> list:
    """Generate GPS path data (coords.json) from an rlog file."""
    coords = []
    base_mono = None
    last_lat = last_lng = None
    total_dist = 0.0

    try:
        for ev in _iter_rlog(rlog_path):
            # Skip initData for base_mono — it has openpilot start time, not segment start
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if not (gps.flags & 1):
                    continue

                lat, lng = gps.latitude, gps.longitude
                if base_mono is not None:
                    t = segment_num * 60.0 + (ev.logMonoTime - base_mono) / 1e9
                else:
                    t = segment_num * 60.0

                if last_lat is not None:
                    total_dist += _haversine_dist(last_lat, last_lng, lat, lng)

                last_lat, last_lng = lat, lng
                coords.append({
                    "t": round(t, 3),
                    "lng": round(lng, 7),
                    "lat": round(lat, 7),
                    "speed": round(gps.speed, 2),
                    "dist": round(total_dist, 1),
                })
    except Exception as e:
        logger.debug("coords generation error for %s: %s", rlog_path, e)

    return coords


def _generate_events_json(rlog_path: str, segment_num: int) -> list:
    """Generate drive events (events.json) from an rlog file."""
    events = []
    base_mono = None
    last_state = None
    last_enabled = None
    last_alert = None

    try:
        for ev in _iter_rlog(rlog_path):
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            w = ev.which()
            state = enabled = alert_status = None

            # Support both controlsState (older) and selfdriveState (0.10.x+)
            if w == "selfdriveState":
                sd = ev.selfdriveState
                state = str(sd.state)
                enabled = bool(sd.enabled)
                alert_status = sd.alertStatus.raw if hasattr(sd, "alertStatus") else 0
            elif w == "controlsState":
                cs = ev.controlsState
                if hasattr(cs, "state"):
                    state = str(cs.state)
                    enabled = bool(cs.enabled)
                    alert_status = cs.alertStatus.raw if hasattr(cs, "alertStatus") else 0

            if state is not None and (state != last_state or enabled != last_enabled or alert_status != last_alert):
                if base_mono is not None:
                    offset_ms = (ev.logMonoTime - base_mono) / 1e6
                else:
                    offset_ms = 0
                route_offset_ms = segment_num * 60000 + offset_ms
                events.append({
                    "type": "state",
                    "time": ev.logMonoTime / 1e9,
                    "offset_millis": round(offset_ms),
                    "route_offset_millis": round(route_offset_ms),
                    "data": {
                        "state": state,
                        "enabled": enabled,
                        "alertStatus": alert_status,
                    },
                })
                last_state = state
                last_enabled = enabled
                last_alert = alert_status
    except Exception as e:
        logger.debug("events generation error for %s: %s", rlog_path, e)

    return events


# ─── Route cache ───────────────────────────────────────────────────────

class RouteStore:
    """Scans route directories and builds comma-compatible route objects.

    Uses metadata.json (compatible with route_metadata.py) for persistent
    route metadata. Two-phase approach for fast startup:
    1. Fast scan: directory listing only (instant) — uses cached metadata or mtime
    2. Background enrichment: parse rlog for uncached routes (non-blocking)

    metadata.json format (shared with route_metadata.py):
    {
      "version": "1.0",
      "last_updated": "ISO 8601",
      "routes": {
        "00000100--c9f14ae705": {
          "route_id": "...",
          "creation_time": "ISO 8601",       # from wallTimeNanos
          "gps_coordinates": [lat, lng],
          "dongle_id": "...",
          "git_commit": "...",
          "git_branch": "...",
          "openpilot_version": "...",
          ...
        }
      }
    }
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self._routes: dict = {}           # fullname -> route dict
        self._fullname_map: dict = {}     # fullname -> local_id
        self._local_id_map: dict = {}     # local_id -> fullname
        self._raw: dict = {}              # local_id -> raw scan data
        self._dongle_id: str = ""
        self._last_scan: float = 0
        self._metadata: dict = {}         # route_id -> route_metadata.py format
        self._hidden: set = set()         # local_ids soft-deleted (hidden from list)
        self._preserved: set = set()      # local_ids protected from cleanup
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._metadata_path = Path(data_dir) / METADATA_FILE

        self._load_metadata()
        self._detect_dongle_id()

    def _detect_dongle_id(self):
        """Read dongle_id from params or metadata."""
        for p in ["/data/params/d/DongleId", "/home/oxygen/driving_data/data/DongleId"]:
            try:
                self._dongle_id = Path(p).read_text().strip()
                return
            except Exception:
                pass
        for meta in self._metadata.values():
            did = meta.get("dongle_id")
            if did and did != "Unknown":
                self._dongle_id = did
                return
        self._dongle_id = "local"

    def _load_metadata(self):
        """Load metadata.json (route_metadata.py format) including hidden/preserved sets."""
        if self._metadata_path.exists():
            try:
                data = json.loads(self._metadata_path.read_text())
                self._metadata = data.get("routes", {})
                self._hidden = set(data.get("hidden_routes", []))
                self._preserved = set(data.get("preserved_routes", []))
                logger.info("Loaded metadata.json: %d routes, %d hidden, %d preserved",
                            len(self._metadata), len(self._hidden), len(self._preserved))
            except Exception as e:
                logger.warning("Failed to load metadata.json: %s", e)
                self._metadata = {}

    def _save_metadata(self):
        """Save metadata.json in route_metadata.py-compatible format."""
        data = {
            "version": "1.0",
            "last_updated": datetime.now(tz=timezone.utc).isoformat(),
            "hidden_routes": sorted(self._hidden),
            "preserved_routes": sorted(self._preserved),
            "routes": self._metadata,
        }
        try:
            tmp = self._metadata_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.rename(self._metadata_path)
        except Exception as e:
            logger.debug("Failed to save metadata.json: %s", e)

    def _wall_time_to_route_date(self, wall_time_nanos: int, lng: float | None = None) -> str:
        """Convert wallTimeNanos to comma route date format: YYYY-MM-DD--HH-MM-SS in local time."""
        dt = datetime.fromtimestamp(wall_time_nanos / 1e9, tz=timezone.utc)
        if lng is not None:
            offset_hours = round(lng / 15)
            dt = dt.astimezone(timezone(timedelta(hours=offset_hours)))
        return dt.strftime("%Y-%m-%d--%H-%M-%S")

    def _meta_to_internal(self, route_id: str) -> dict:
        """Convert route_metadata.py format to internal fields for route building."""
        meta = self._metadata.get(route_id, {})
        result = {}

        # dongle_id
        did = meta.get("dongle_id")
        if did and did != "Unknown":
            result["dongle_id"] = did

        # creation_time → wall_time_nanos + create_time
        ct = meta.get("creation_time")
        if ct and isinstance(ct, str) and not ct.startswith("GPS"):
            try:
                dt = datetime.fromisoformat(ct)
                result["create_time"] = dt.timestamp()
                result["wall_time_nanos"] = int(dt.timestamp() * 1e9)
            except (ValueError, OSError):
                pass

        # GPS
        gps = meta.get("gps_coordinates")
        if gps and isinstance(gps, list) and len(gps) == 2:
            result["start_lat"] = gps[0]
            result["start_lng"] = gps[1]

        # Git info
        gc = meta.get("git_commit")
        if gc and gc != "Unknown":
            result["git_commit"] = gc
        gb = meta.get("git_branch")
        if gb:
            result["git_branch"] = gb
        elif meta.get("software_environment", {}).get("openpilot_branch"):
            result["git_branch"] = meta["software_environment"]["openpilot_branch"]

        # Version
        ver = meta.get("openpilot_version")
        if ver and ver != "Unknown":
            result["version"] = ver

        # Car fingerprint
        cf = meta.get("car_fingerprint")
        if cf:
            result["car_fingerprint"] = cf

        return result

    def _needs_enrich(self, lid: str) -> bool:
        """Check if a route needs background enrichment."""
        meta = self._metadata.get(lid)
        if not meta:
            return True
        if not meta.get("car_fingerprint"):
            return True
        return not meta.get("gps_coordinates") or not meta.get("total_distance_m")

    @staticmethod
    def _find_rlog(seg_path: str) -> str | None:
        """Find rlog file in a segment directory, preferring .zst."""
        rlog = Path(seg_path) / "rlog.zst"
        if rlog.exists():
            return str(rlog)
        rlog = Path(seg_path) / "rlog"
        if rlog.exists():
            return str(rlog)
        return None

    def _calc_route_distance(self, local_id: str, segments: list) -> float | None:
        """Calculate total route distance in miles.

        Tier 1: Sum cached coords.json last-point dist (accurate, from visited routes)
        Tier 2: Use total_distance_m from metadata (computed during enrichment)
        """
        # Tier 1: cached coords.json
        total_m = 0.0
        found = 0
        for seg in segments:
            coords_path = Path(seg["path"]) / "coords.json"
            if not coords_path.exists():
                continue
            try:
                coords = json.loads(coords_path.read_text())
                if coords:
                    total_m += coords[-1].get("dist", 0)
                    found += 1
            except Exception:
                pass
        if found > 0:
            return round(total_m / 1609.344, 1)

        # Tier 2: enrichment-computed total distance
        meta = self._metadata.get(local_id, {})
        total_dist = meta.get("total_distance_m")
        if total_dist and total_dist > 0:
            return round(total_dist / 1609.344, 1)

        return None

    def _build_route(self, local_id: str, info: dict, internal: dict) -> dict:
        """Build a comma-compatible route dict from raw scan data + metadata."""
        dongle_id = internal.get("dongle_id", self._dongle_id)

        lng = internal.get("start_lng")
        wtn = internal.get("wall_time_nanos", 0)
        if wtn:
            route_date = self._wall_time_to_route_date(wtn, lng)
        else:
            dt = datetime.fromtimestamp(info["mtime"], tz=timezone.utc)
            if lng is not None:
                dt = dt.astimezone(timezone(timedelta(hours=round(lng / 15))))
            route_date = dt.strftime("%Y-%m-%d--%H-%M-%S")

        fullname = f"{dongle_id}/{route_date}"
        create_time = internal.get("create_time", info["mtime"])

        seg_numbers = [s["number"] for s in info["segments"]]
        max_seg = max(seg_numbers) if seg_numbers else 0

        seg_start_times = []
        seg_end_times = []
        for s in info["segments"]:
            t = create_time + s["number"] * 60
            seg_start_times.append(t)
            seg_end_times.append(t + 60)

        tz_info = timezone(timedelta(hours=round(lng / 15))) if lng is not None else timezone.utc
        start_time_iso = datetime.fromtimestamp(create_time, tz=tz_info).isoformat()
        end_time_epoch = create_time + (max_seg + 1) * 60
        end_time_iso = datetime.fromtimestamp(end_time_epoch, tz=tz_info).isoformat()

        return {
            "create_time": create_time,
            "dongle_id": dongle_id,
            "end_lat": internal.get("start_lat"),
            "end_lng": internal.get("start_lng"),
            "end_time": end_time_iso,
            "fullname": fullname,
            "git_branch": internal.get("git_branch"),
            "git_commit": internal.get("git_commit"),
            "git_commit_date": None,
            "git_dirty": None,
            "git_remote": None,
            "is_public": True,
            "distance": self._calc_route_distance(local_id, info["segments"]),
            "maxqlog": max_seg,
            "platform": internal.get("car_fingerprint"),
            "procqlog": len(seg_numbers),
            "start_lat": internal.get("start_lat"),
            "start_lng": internal.get("start_lng"),
            "start_time": start_time_iso,
            "url": None,
            "user_id": "local",
            "version": internal.get("version"),
            "vin": None,
            "make": None,
            "id": None,
            "car_id": None,
            "version_id": None,
            "local_id": local_id,
            "_local_id": local_id,
            "_segments": info["segments"],
            "_seg_numbers": seg_numbers,
            "_seg_start_times": seg_start_times,
            "_seg_end_times": seg_end_times,
        }

    def _rebuild_routes(self):
        """Rebuild route dicts from raw data + current metadata. Skips hidden routes."""
        routes = {}
        fullname_map = {}
        local_id_map = {}

        for local_id, info in self._raw.items():
            if local_id in self._hidden:
                continue
            internal = self._meta_to_internal(local_id)
            dongle_id = internal.get("dongle_id", self._dongle_id)
            if not self._dongle_id or self._dongle_id == "local":
                self._dongle_id = dongle_id

            route = self._build_route(local_id, info, internal)

            # Hide stub routes: < 2 minutes (maxqlog < 1) and no distance
            if route["maxqlog"] < 1 and not route.get("distance"):
                continue

            fullname = route["fullname"]
            routes[fullname] = route
            fullname_map[fullname] = local_id
            local_id_map[local_id] = fullname

        self._routes = routes
        self._fullname_map = fullname_map
        self._local_id_map = local_id_map

    def _enrich_one(self, local_id: str, segments: list) -> dict | None:
        """Parse rlog metadata for a single route. Runs in thread pool.

        Tries segment 0 first for initData + GPS + distance, then checks later
        segments for GPS only (cold starts can delay GPS lock).
        Computes total distance across ALL segments, reusing seg 0's distance
        from _parse_rlog_metadata to avoid decompressing it twice.
        """
        sorted_segs = sorted(segments, key=lambda s: s["number"])

        # Parse segment 0 for full metadata (initData + GPS + seg0 distance)
        result = None
        seg0_rlog = self._find_rlog(sorted_segs[0]["path"]) if sorted_segs else None
        if seg0_rlog:
            result = _parse_rlog_metadata(seg0_rlog)

        if not result:
            result = {}

        # If no GPS yet, try later segments (GPS lock can take 5+ minutes)
        if not result.get("start_lat"):
            for seg in sorted_segs[1:10]:
                rlog = self._find_rlog(seg["path"])
                if not rlog:
                    continue
                gps = _find_first_gps(rlog)
                if gps:
                    result["start_lat"] = gps[0]
                    result["start_lng"] = gps[1]
                    break

        # Compute total distance: start from seg 0's already-computed distance,
        # then add remaining segments (avoids re-decompressing seg 0)
        total_dist = result.pop("segment_distance_m", 0.0)
        for seg in sorted_segs[1:]:
            rlog = self._find_rlog(seg["path"])
            if not rlog:
                continue
            total_dist += _segment_gps_distance(rlog)

        if total_dist > 0:
            result["total_distance_m"] = total_dist

        return result if result else None

    def _rlog_to_metadata_entry(self, local_id: str, rlog_meta: dict) -> dict:
        """Convert rlog parse result to route_metadata.py-compatible entry."""
        entry = {"route_id": local_id}

        wtn = rlog_meta.get("wall_time_nanos", 0)
        if wtn:
            dt = datetime.fromtimestamp(wtn / 1e9, tz=timezone.utc)
            entry["creation_time"] = dt.isoformat()
        else:
            entry["creation_time"] = None

        lat = rlog_meta.get("start_lat")
        lng = rlog_meta.get("start_lng")
        entry["gps_coordinates"] = [lat, lng] if lat and lng else None

        entry["dongle_id"] = rlog_meta.get("dongle_id", "Unknown")
        entry["git_commit"] = rlog_meta.get("git_commit", "Unknown")
        entry["git_branch"] = rlog_meta.get("git_branch")
        entry["openpilot_version"] = rlog_meta.get("version", "Unknown")
        entry["total_distance_m"] = rlog_meta.get("total_distance_m")
        entry["car_fingerprint"] = rlog_meta.get("car_fingerprint")
        entry["source"] = "connect_server"

        return entry

    async def _enrichment_loop(self):
        """Persistent background loop that enriches route metadata.

        Polls every 90s, re-scans directories, enriches newest routes first.
        Runs as a single asyncio task — no re-entrancy guard needed.
        """
        await asyncio.sleep(5)  # let server bind first
        loop = asyncio.get_event_loop()

        while True:
            try:
                # Re-scan directories to pick up new routes/segments
                self.scan(force=True)

                # Find routes needing enrichment, newest first
                uncached = [
                    (lid, info) for lid, info in self._raw.items()
                    if self._needs_enrich(lid)
                ]
                if not uncached:
                    await asyncio.sleep(90)
                    continue

                # Sort newest-first by mtime so latest drive is enriched first
                uncached.sort(key=lambda x: x[1]["mtime"], reverse=True)
                logger.info("Enrichment cycle: %d routes to process", len(uncached))

                count = 0
                for local_id, info in uncached:
                    try:
                        rlog_meta = await loop.run_in_executor(
                            self._executor, self._enrich_one, local_id, info["segments"])
                        if rlog_meta:
                            entry = self._rlog_to_metadata_entry(local_id, rlog_meta)
                            self._metadata[local_id] = entry
                            count += 1
                            if count % 5 == 0:
                                self._rebuild_routes()
                                self._save_metadata()
                                logger.info("  enriched %d/%d routes...", count, len(uncached))
                    except Exception as e:
                        logger.debug("enrich error for %s: %s", local_id, e)

                if count > 0:
                    self._rebuild_routes()
                    self._save_metadata()
                    logger.info("Enrichment cycle complete: %d routes enriched", count)

            except asyncio.CancelledError:
                logger.info("Enrichment loop cancelled")
                return
            except Exception as e:
                logger.warning("Enrichment cycle error: %s", e)

            await asyncio.sleep(90)

    def scan(self, force: bool = False) -> dict:
        """Fast directory scan — no rlog parsing. Returns immediately."""
        if not force and (time.time() - self._last_scan) < CACHE_TTL:
            return self._routes

        raw: dict = defaultdict(lambda: {"segments": [], "total_size": 0, "mtime": 0})

        if not self.data_dir.exists():
            logger.warning("Data directory does not exist: %s", self.data_dir)
            self._routes = {}
            self._last_scan = time.time()
            return self._routes

        for entry in self.data_dir.iterdir():
            if not entry.is_dir():
                continue
            m = ROUTE_DIR_RE.match(entry.name)
            if not m:
                continue
            local_id = m.group(1)
            seg_num = int(m.group(2))

            try:
                seg_size = sum(f.stat().st_size for f in entry.iterdir() if f.is_file())
                seg_mtime = entry.stat().st_mtime
            except OSError:
                continue

            raw[local_id]["segments"].append({
                "number": seg_num,
                "path": str(entry),
                "size": seg_size,
                "files": {f.name for f in entry.iterdir() if f.is_file()},
            })
            raw[local_id]["total_size"] += seg_size
            raw[local_id]["mtime"] = max(raw[local_id]["mtime"], seg_mtime)

        for info in raw.values():
            info["segments"].sort(key=lambda s: s["number"])

        self._raw = dict(raw)
        self._rebuild_routes()
        self._last_scan = time.time()

        logger.info("Scanned %d routes, %d total segments (%d in metadata.json)",
                     len(self._routes),
                     sum(len(r["_segments"]) for r in self._routes.values()),
                     len(self._metadata))

        # Run storage cleanup if space is low
        cleanup_result = run_cleanup(self)
        if cleanup_result["deleted"]:
            logger.info("Storage cleanup: freed %d routes, now %.1f%% free",
                        len(cleanup_result["deleted"]), cleanup_result["free_pct"])
            self._rebuild_routes()

        return self._routes

    @property
    def dongle_id(self):
        return self._dongle_id

    def get_route(self, fullname: str) -> dict | None:
        self.scan()
        return self._routes.get(fullname)

    def get_local_id(self, fullname: str) -> str | None:
        self.scan()
        return self._fullname_map.get(fullname)

    def hide_route(self, local_id: str):
        """Mark a route as hidden (soft-delete). Hidden from listings, deleted on low space."""
        self._hidden.add(local_id)
        self._preserved.discard(local_id)
        self._rebuild_routes()
        self._save_metadata()

    def preserve_route(self, local_id: str):
        """Mark a route as preserved (protected from cleanup)."""
        self._preserved.add(local_id)
        self._save_metadata()

    def unpreserve_route(self, local_id: str):
        """Remove preservation from a route."""
        self._preserved.discard(local_id)
        self._save_metadata()

    def is_preserved(self, local_id: str) -> bool:
        return local_id in self._preserved

    def resolve_segment_path(self, fullname: str, segment: int, filename: str) -> Path | None:
        """Resolve a comma-style path to a local file."""
        local_id = self.get_local_id(fullname)
        if not local_id:
            return None
        path = self.data_dir / f"{local_id}--{segment}" / filename
        return path if path.exists() else None


def _clean_route(route: dict) -> dict:
    """Remove internal fields from route for API response."""
    return {k: v for k, v in route.items() if not k.startswith("_")}


def _base_url(request: web.Request) -> str:
    """Get base URL for constructing file URLs."""
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"


def _set_route_url(route: dict, request: web.Request) -> dict:
    """Set the url field dynamically based on request host."""
    r = dict(route)
    base = _base_url(request)
    r["url"] = f"{base}/connectdata/{route['dongle_id']}/{route['fullname'].split('/')[-1]}"
    return r


# ─── CORS middleware ───────────────────────────────────────────────────

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return resp


# ─── comma API handlers ───────────────────────────────────────────────

async def handle_me(request: web.Request) -> web.Response:
    """GET /v1/me/ — user profile (static)"""
    return web.json_response({
        "id": "local",
        "email": "local@device",
        "prime": False,
        "regdate": 0,
        "superuser": False,
        "user_id": "local",
        "username": "local",
    })


async def handle_devices(request: web.Request) -> web.Response:
    """GET /v1/me/devices/ — list devices"""
    store: RouteStore = request.app["store"]
    store.scan()
    return web.json_response([_device_dict(store)])


async def handle_device_get(request: web.Request) -> web.Response:
    """GET /v1.1/devices/{dongleId}/ — single device"""
    store: RouteStore = request.app["store"]
    store.scan()
    return web.json_response(_device_dict(store))


def _device_dict(store: RouteStore) -> dict:
    return {
        "alias": None,
        "athena_host": None,
        "device_type": "three",
        "dongle_id": store.dongle_id,
        "eligible_features": {"prime": False, "prime_data": False},
        "ignore_uploads": None,
        "is_paired": True,
        "is_owner": True,
        "last_athena_ping": 0,
        "openpilot_version": None,
        "prime": False,
        "prime_type": 0,
        "public_key": "",
        "serial": "local",
        "sim_id": None,
        "sim_type": None,
        "trial_claimed": False,
    }


async def handle_routes_list(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes — paginated route list"""
    store: RouteStore = request.app["store"]
    routes = store.scan()

    limit = int(request.query.get("limit", 25))
    created_before = float(request.query.get("created_before", time.time() + 86400))

    route_list = []
    for r in routes.values():
        r_with_url = _set_route_url(r, request)
        cleaned = _clean_route(r_with_url)
        if cleaned["create_time"] < created_before:
            route_list.append(cleaned)

    route_list.sort(key=lambda r: r["create_time"], reverse=True)
    return web.json_response(route_list[:limit])


async def handle_routes_segments(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes_segments — route with segment data"""
    store: RouteStore = request.app["store"]
    routes = store.scan()

    route_str = request.query.get("route_str", "")
    limit = int(request.query.get("limit", 100))

    sorted_routes = sorted(routes.values(), key=lambda r: r["create_time"], reverse=True)

    results = []
    for r in sorted_routes:
        fullname = r["fullname"]
        if route_str and fullname != route_str:
            continue
        r_with_url = _set_route_url(r, request)
        seg = _clean_route(r_with_url)
        seg.update({
            "end_time_utc_millis": int((r["create_time"] + (r["maxqlog"] + 1) * 60) * 1000),
            "is_preserved": store.is_preserved(r["_local_id"]),
            "segment_end_times": [int(t) for t in r["_seg_end_times"]],
            "segment_numbers": r["_seg_numbers"],
            "segment_start_times": [int(t) for t in r["_seg_start_times"]],
            "share_exp": "9999999999",
            "share_sig": "local",
            "start_time_utc_millis": int(r["create_time"] * 1000),
        })
        results.append(seg)
        if len(results) >= limit:
            break

    return web.json_response(results)


async def handle_route_get(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/ — single route detail"""
    route_name = request.match_info["routeName"].replace("|", "/")
    store: RouteStore = request.app["store"]
    route = store.get_route(route_name)

    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(route["_local_id"])
    return web.json_response(cleaned)


async def handle_route_files(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/files — list available files per segment"""
    route_name = request.match_info["routeName"].replace("|", "/")
    store: RouteStore = request.app["store"]
    route = store.get_route(route_name)

    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    base = _base_url(request)
    dongle_id = route["dongle_id"]
    route_date = route["fullname"].split("/")[-1]
    local_id = route["_local_id"]

    # Build file lists — one entry per segment index (0 to maxqlog)
    max_seg = route["maxqlog"]
    cameras = []
    dcameras = []
    ecameras = []
    logs = []
    qcameras = []
    qlogs = []

    seg_set = {s["number"]: s for s in route["_segments"]}

    for i in range(max_seg + 1):
        seg = seg_set.get(i)
        if not seg:
            cameras.append("")
            dcameras.append("")
            ecameras.append("")
            logs.append("")
            qcameras.append("")
            qlogs.append("")
            continue

        files = seg["files"]
        prefix = f"{base}/connectdata/{dongle_id}/{route_date}/{i}"

        cameras.append(f"{prefix}/fcamera.hevc" if "fcamera.hevc" in files else "")
        ecameras.append(f"{prefix}/ecamera.hevc" if "ecamera.hevc" in files else "")
        dcameras.append(f"{prefix}/dcamera.hevc" if "dcamera.hevc" in files else "")
        logs.append(f"{prefix}/rlog.zst" if "rlog.zst" in files else "")
        qcameras.append(f"{prefix}/qcamera.ts" if "qcamera.ts" in files else "")
        qlogs.append(f"{prefix}/qlog.zst" if "qlog.zst" in files else "")

    return web.json_response({
        "cameras": cameras,
        "dcameras": dcameras,
        "ecameras": ecameras,
        "logs": logs,
        "qcameras": qcameras,
        "qlogs": qlogs,
    })


async def handle_share_signature(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/share_signature — dummy signature"""
    return web.json_response({
        "exp": "9999999999",
        "sig": "local",
    })


def _route_engagement(store: "RouteStore", route: dict) -> tuple[float, float]:
    """Compute engaged_ms and total_ms for a route from cached events.json files.

    Returns (engaged_ms, total_duration_ms). Uses cached events.json files
    (generated when a route is viewed in the UI). Returns (0, 0) if no cache.
    """
    local_id = route.get("_local_id")
    if not local_id:
        return (0, 0)

    total_duration_ms = (route.get("maxqlog", 0) + 1) * 60_000
    engaged_ms = 0.0

    for seg in route.get("_segments", []):
        events_path = Path(seg["path"]) / "events.json"
        if not events_path.exists():
            continue
        try:
            events = json.loads(events_path.read_text())
            last_enabled_offset = None
            for ev in events:
                if ev.get("type") != "state":
                    continue
                enabled = ev.get("data", {}).get("enabled", False)
                offset = ev.get("route_offset_millis", 0)
                if enabled and last_enabled_offset is None:
                    last_enabled_offset = offset
                elif not enabled and last_enabled_offset is not None:
                    engaged_ms += offset - last_enabled_offset
                    last_enabled_offset = None
            # Close open engagement at segment end
            if last_enabled_offset is not None:
                seg_end_ms = (seg["number"] + 1) * 60_000
                engaged_ms += seg_end_ms - last_enabled_offset
        except Exception:
            pass

    return (engaged_ms, total_duration_ms)


async def handle_device_stats(request: web.Request) -> web.Response:
    """GET /v1.1/devices/{dongleId}/stats — driving statistics with engagement"""
    store: RouteStore = request.app["store"]
    routes = store.scan()

    week_ago = time.time() - 7 * 86400

    all_stats = {"distance": 0.0, "minutes": 0, "routes": 0, "engaged_minutes": 0.0, "total_minutes_with_events": 0}
    week_stats = {"distance": 0.0, "minutes": 0, "routes": 0, "engaged_minutes": 0.0, "total_minutes_with_events": 0}

    for r in routes.values():
        minutes = len(r["_segments"])  # ~1 min per segment
        distance = r.get("distance") or 0
        engaged_ms, total_ms = _route_engagement(store, r)

        all_stats["routes"] += 1
        all_stats["minutes"] += minutes
        all_stats["distance"] += distance
        if total_ms > 0 and engaged_ms > 0:
            all_stats["engaged_minutes"] += engaged_ms / 60_000
            all_stats["total_minutes_with_events"] += total_ms / 60_000

        if r.get("create_time", 0) >= week_ago:
            week_stats["routes"] += 1
            week_stats["minutes"] += minutes
            week_stats["distance"] += distance
            if total_ms > 0 and engaged_ms > 0:
                week_stats["engaged_minutes"] += engaged_ms / 60_000
                week_stats["total_minutes_with_events"] += total_ms / 60_000

    for s in (all_stats, week_stats):
        s["distance"] = round(s["distance"], 1)
        s["engaged_minutes"] = round(s["engaged_minutes"], 1)
        s["total_minutes_with_events"] = round(s["total_minutes_with_events"], 1)

    return web.json_response({"all": all_stats, "week": week_stats})


async def handle_device_location(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/location — last known GPS"""
    store: RouteStore = request.app["store"]
    routes = store.scan()

    # Find most recent route with GPS
    for r in sorted(routes.values(), key=lambda x: x["create_time"], reverse=True):
        if r.get("start_lat"):
            return web.json_response({
                "dongle_id": store.dongle_id,
                "lat": r["start_lat"],
                "lng": r["start_lng"],
                "time": r["create_time"],
            })

    raise web.HTTPNotFound(text=json.dumps({"error": "No GPS data"}))


async def handle_preserved_routes(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes/preserved — return preserved routes"""
    store: RouteStore = request.app["store"]
    routes = store.scan()
    preserved = []
    for r in routes.values():
        if store.is_preserved(r["_local_id"]):
            r_with_url = _set_route_url(r, request)
            cleaned = _clean_route(r_with_url)
            cleaned["is_preserved"] = True
            preserved.append(cleaned)
    preserved.sort(key=lambda r: r["create_time"], reverse=True)
    return web.json_response(preserved)


# ─── Storage management handlers ──────────────────────────────────────

def _resolve_local_id(store: "RouteStore", request: web.Request) -> str:
    """Resolve routeName from URL to local_id. Raises 404 if not found."""
    route_name = request.match_info["routeName"].replace("|", "/")
    # Try direct lookup first
    local_id = store.get_local_id(route_name)
    if not local_id:
        # Try matching by local_id directly (for hidden routes not in _routes)
        if route_name in store._raw:
            local_id = route_name
    if not local_id:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))
    return local_id


async def handle_route_delete(request: web.Request) -> web.Response:
    """DELETE /v1/route/{routeName}/ — soft-delete (hide) a route"""
    store: RouteStore = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.hide_route(local_id)
    logger.info("Route hidden: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_preserve(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/preserve — mark route as preserved"""
    store: RouteStore = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.preserve_route(local_id)
    logger.info("Route preserved: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_unpreserve(request: web.Request) -> web.Response:
    """DELETE /v1/route/{routeName}/preserve — remove preservation"""
    store: RouteStore = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.unpreserve_route(local_id)
    logger.info("Route unpreserved: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_download(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/download?files=rlog,qcamera&segments=0,1,2 — stream tar.gz"""
    store: RouteStore = request.app["store"]
    local_id = _resolve_local_id(store, request)

    files_param = request.query.get("files", "rlog")
    file_types = [f.strip() for f in files_param.split(",") if f.strip() in DOWNLOAD_FILES]
    if not file_types:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"No valid file types. Choose from: {', '.join(DOWNLOAD_FILES)}"}))

    # Optional segment filter: ?segments=0,1,2 (default: all)
    seg_param = request.query.get("segments")
    segments = None
    if seg_param:
        try:
            segments = [int(s.strip()) for s in seg_param.split(",")]
        except ValueError:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid segments parameter"}))

    loop = asyncio.get_event_loop()
    buf = await loop.run_in_executor(None, build_download_tar, store, local_id, file_types, segments)

    if not buf:
        raise web.HTTPNotFound(text=json.dumps({"error": "No matching files found"}))

    safe_name = local_id.replace("/", "_")
    return web.Response(
        body=buf.read(),
        content_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.tar.gz"'},
    )


async def handle_storage(request: web.Request) -> web.Response:
    """GET /v1/storage — disk usage and route management stats"""
    store: RouteStore = request.app["store"]
    return web.json_response(get_storage_info(store))


# ─── connectdata file serving ──────────────────────────────────────────

async def handle_connectdata(request: web.Request) -> web.Response:
    """GET /connectdata/{dongleId}/{routeDate}/{segment}/{filename}
    Serves actual media files from /data/media/0/realdata/."""
    path_parts = request.match_info["path"].split("/")

    # Expected: dongleId/routeDate/segment/filename
    if len(path_parts) < 4:
        raise web.HTTPNotFound()

    dongle_id = path_parts[0]
    route_date = path_parts[1]
    segment = path_parts[2]
    filename = path_parts[3]

    # Security: only allow known filenames
    allowed = {"qcamera.ts", "fcamera.hevc", "ecamera.hevc", "dcamera.hevc",
               "rlog.zst", "rlog", "qlog.zst", "qlog", "sprite.jpg"}
    derived = {"coords.json", "events.json"}
    if filename not in allowed and filename not in derived:
        raise web.HTTPForbidden(text="File not allowed")

    fullname = f"{dongle_id}/{route_date}"
    store: RouteStore = request.app["store"]
    seg_int = int(segment)

    # Derived files: generate from rlog on demand, cache to disk
    if filename in derived:
        local_id = store.get_local_id(fullname)
        if not local_id:
            raise web.HTTPNotFound()

        cache_path = store.data_dir / f"{local_id}--{seg_int}" / filename
        if cache_path.exists():
            try:
                return web.json_response(json.loads(cache_path.read_text()))
            except Exception:
                pass

        rlog = store.resolve_segment_path(fullname, seg_int, "rlog.zst")
        if not rlog:
            rlog = store.resolve_segment_path(fullname, seg_int, "rlog")
        if not rlog:
            return web.json_response([])

        gen_fn = _generate_coords_json if filename == "coords.json" else _generate_events_json
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, gen_fn, str(rlog), seg_int)

        try:
            cache_path.write_text(json.dumps(data))
        except Exception:
            pass

        return web.json_response(data)

    file_path = store.resolve_segment_path(fullname, seg_int, filename)

    # Generate sprite.jpg on demand from qcamera.ts
    if not file_path and filename == "sprite.jpg":
        qcam = store.resolve_segment_path(fullname, seg_int, "qcamera.ts")
        if qcam:
            sprite_path = qcam.parent / "sprite.jpg"
            try:
                proc = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["ffmpeg", "-y", "-i", str(qcam), "-vframes", "1",
                         "-q:v", "5", "-vf", "scale=480:-1",
                         "-f", "image2", str(sprite_path)],
                        capture_output=True, timeout=10,
                    ),
                )
                if proc.returncode == 0 and sprite_path.exists():
                    file_path = sprite_path
            except Exception:
                pass

    if not file_path:
        raise web.HTTPNotFound()

    content_types = {
        ".ts": "video/mp2t",
        ".hevc": "video/hevc",
        ".zst": "application/zstd",
        ".jpg": "image/jpeg",
    }
    ct = content_types.get(file_path.suffix, "application/octet-stream")
    return web.FileResponse(file_path, headers={"Content-Type": ct})


# ─── Stub handlers for endpoints the frontend may call ────────────────

async def handle_stub_empty_array(request: web.Request) -> web.Response:
    return web.json_response([])

async def handle_stub_error(request: web.Request) -> web.Response:
    return web.json_response({"error": "Not available on local device"}, status=501)

async def handle_auth(request: web.Request) -> web.Response:
    """POST /v2/auth/ — return a dummy token"""
    return web.json_response({"access_token": "local-device-token"})


async def handle_webrtc(request: web.Request) -> web.Response:
    """POST /api/webrtc — proxy WebRTC signaling to local webrtcd."""
    import aiohttp
    body = await request.json()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("http://localhost:5001/stream", json=body) as resp:
                data = await resp.json()
                return web.json_response(data)
    except Exception as e:
        logger.warning("WebRTC proxy error: %s", e)
        return web.json_response({"error": f"webrtcd unavailable: {e}"}, status=502)


async def handle_hud_ws(request: web.Request) -> web.StreamResponse:
    """WebSocket /ws/hud — stream full HUD overlay images at 20Hz."""
    from hud_renderer import HudRenderer

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logger.info("HUD WebSocket connected")

    renderer = HudRenderer()
    try:
        while not ws.closed:
            frame_bytes = renderer.render_frame()
            if frame_bytes:
                await ws.send_bytes(frame_bytes)
            await asyncio.sleep(0.05)  # 20Hz
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("HUD WebSocket error: %s", e)
    finally:
        renderer.close()
        logger.info("HUD WebSocket disconnected")
    return ws


# ─── SPA serving ───────────────────────────────────────────────────────

async def handle_spa(request: web.Request) -> web.Response:
    """Serve static files if they exist, otherwise the React SPA index.html."""
    static_dir: Path = request.app["static_dir"]
    req_path = request.match_info.get("path", "")

    # Try to serve the actual static file first
    if req_path:
        # Security: resolve and ensure it's within static_dir
        file_path = (static_dir / req_path).resolve()
        if file_path.is_relative_to(static_dir) and file_path.is_file():
            return web.FileResponse(file_path)

    # Fall back to index.html for SPA client-side routing — no-cache so updates are immediate
    index = static_dir / "index.html"
    if index.exists():
        resp = web.FileResponse(index)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    return web.Response(
        text="<html><body><h1>Connect on Device</h1>"
             "<p>Frontend not built yet.</p>"
             "<p>API: <a href='/v1/me/'>/v1/me/</a></p>"
             "</body></html>",
        content_type="text/html",
    )


# ─── App setup ─────────────────────────────────────────────────────────

async def _start_enrichment(app: web.Application):
    """Start the persistent background enrichment loop on server startup."""
    store: RouteStore = app["store"]
    store.scan(force=True)
    app["enrichment_task"] = asyncio.create_task(store._enrichment_loop())
    logger.info("Background enrichment task started")


async def _stop_enrichment(app: web.Application):
    """Cancel the background enrichment loop on server shutdown."""
    task = app.get("enrichment_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Background enrichment task stopped")


def create_app(data_dir: str, static_dir: str) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    store = RouteStore(data_dir)
    app["store"] = store
    app["static_dir"] = Path(static_dir)

    # Background enrichment lifecycle
    app.on_startup.append(_start_enrichment)
    app.on_cleanup.append(_stop_enrichment)

    # ── comma-compatible API ──
    # Auth
    app.router.add_get("/v1/me/", handle_me)
    app.router.add_post("/v2/auth/", handle_auth)

    # Devices
    app.router.add_get("/v1/me/devices/", handle_devices)
    app.router.add_get("/v1.1/devices/{dongleId}/", handle_device_get)
    app.router.add_get("/v1.1/devices/{dongleId}/stats", handle_device_stats)
    app.router.add_get("/v1/devices/{dongleId}/location", handle_device_location)
    app.router.add_get("/v1/devices/{dongleId}/routes/preserved", handle_preserved_routes)

    # Routes
    app.router.add_get("/v1/devices/{dongleId}/routes", handle_routes_list)
    app.router.add_get("/v1/devices/{dongleId}/routes_segments", handle_routes_segments)

    # Route detail
    app.router.add_get("/v1/route/{routeName}/", handle_route_get)
    app.router.add_delete("/v1/route/{routeName}/", handle_route_delete)
    app.router.add_get("/v1/route/{routeName}/files", handle_route_files)
    app.router.add_get("/v1/route/{routeName}/share_signature", handle_share_signature)
    app.router.add_post("/v1/route/{routeName}/preserve", handle_route_preserve)
    app.router.add_delete("/v1/route/{routeName}/preserve", handle_route_unpreserve)
    app.router.add_get("/v1/route/{routeName}/download", handle_route_download)

    # Stubs for endpoints the frontend may query
    app.router.add_get("/v1/devices/{dongleId}/athena_offline_queue", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/bootlogs", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/crashlogs", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/users", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/firehose_stats", handle_stub_error)
    app.router.add_post("/v1/devices/{dongleId}/unpair", handle_stub_error)
    app.router.add_get("/v1/prime/subscription", handle_stub_error)
    app.router.add_get("/v1/prime/subscribe_info", handle_stub_error)
    app.router.add_get("/v1/storage", handle_storage)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    # WebRTC signaling proxy (to local webrtcd on port 5001)
    app.router.add_post("/api/webrtc", handle_webrtc)

    # HUD overlay WebSocket (server-side rendered overlay at 20Hz)
    app.router.add_get("/ws/hud", handle_hud_ws)

    # Media file serving
    app.router.add_get("/connectdata/{path:.*}", handle_connectdata)

    # SPA fallback — serves static files if they exist, otherwise index.html
    app.router.add_get("/{path:.*}", handle_spa)

    return app


def main():
    parser = argparse.ArgumentParser(description="Connect on Device - comma-compatible local server")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help=f"Route data directory (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Server port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--static-dir", default=None,
                        help="Static files directory (default: ./static next to server.py)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    static_dir = args.static_dir or str(Path(__file__).parent / "static")

    logger.info("Starting Connect on Device (comma-compatible API)")
    logger.info("  Data dir:   %s", args.data_dir)
    logger.info("  Static dir: %s", static_dir)
    logger.info("  Listening:  http://%s:%d", args.host, args.port)

    app = create_app(args.data_dir, static_dir)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
