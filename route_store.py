"""Route storage and metadata management for Connect on Device.

Contains the RouteStore class that scans route directories, manages metadata,
and provides comma-compatible route objects. Handles background enrichment
of route metadata from rlog files.
"""

import asyncio
import json
import logging
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rlog_parser import _find_first_gps, _parse_rlog_metadata, _segment_gps_distance
from storage_management import run_cleanup

logger = logging.getLogger("connect")

# Route directory pattern: {count}--{uid}--{segment}
ROUTE_DIR_RE = re.compile(r"^(\w+--\w+)--(\d+)$")

DEFAULT_DATA_DIR = "/data/media/0/realdata"
DEFAULT_PORT = 8082
CACHE_TTL = 120  # seconds — route scan is expensive with metadata
METADATA_FILE = "metadata.json"

# Add openpilot to path for LogReader
for _p in ["/data/openpilot", "/home/oxygen/openpilot"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
        self._agnos_version: str | None = None

        self._load_metadata()
        self._detect_dongle_id()
        self._detect_agnos_version()

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

    def _detect_agnos_version(self):
        """Read AGNOS version from /VERSION."""
        try:
            self._agnos_version = Path("/VERSION").read_text().strip()
        except Exception:
            pass

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

        # creation_time -> wall_time_nanos + create_time
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

        # Git remote
        gr = meta.get("git_remote")
        if gr:
            result["git_remote"] = gr

        # Device type
        dt = meta.get("device_type")
        if dt:
            result["device_type"] = dt

        return result

    def _needs_enrich(self, lid: str) -> bool:
        """Check if a route needs background enrichment."""
        meta = self._metadata.get(lid)
        if not meta:
            return True
        if not meta.get("car_fingerprint"):
            return True
        if not meta.get("device_type"):
            return True
        # Re-enrich routes with known-bad AGNOS build date as creation_time
        ct = meta.get("creation_time", "")
        if isinstance(ct, str) and ct.startswith("2025-07-02"):
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
            "git_remote": internal.get("git_remote"),
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
            "device_type": internal.get("device_type"),
            "agnos_version": self._agnos_version,
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
        entry["git_remote"] = rlog_meta.get("git_remote")
        entry["device_type"] = rlog_meta.get("device_type")
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
