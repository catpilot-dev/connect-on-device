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
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rlog_parser import _find_first_gps, _find_first_gps_time, _find_last_gps, _parse_rlog_metadata
from storage_management import run_cleanup

logger = logging.getLogger("connect")

# Route directory pattern: {count}--{uid}--{segment}
ROUTE_DIR_RE = re.compile(r"^(\w+--\w+)--(\d+)$")

DEFAULT_DATA_DIR = "/data/media/0/realdata"
DEFAULT_PORT = 8082
CACHE_TTL = 120  # seconds — route scan is expensive with metadata
METADATA_FILE = "metadata.json"

# Nominatim reverse geocoding (OSM)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_HEADERS = {"User-Agent": "connect-on-device/1.0"}
_last_geocode_time = 0.0


def _reverse_geocode(lat: float, lng: float) -> str | None:
    """Reverse geocode lat/lng to a short road/place name via Nominatim.

    Returns a string like "Zhongshan Rd" or "Huaihai Middle Rd".
    Respects Nominatim rate limit (1 req/sec). Returns None on failure.
    """
    global _last_geocode_time
    # Rate limit: 1 request per second
    elapsed = time.time() - _last_geocode_time
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    url = f"{_NOMINATIM_URL}?lat={lat}&lon={lng}&format=json&zoom=16&accept-language=zh,en"
    req = urllib.request.Request(url, headers=_NOMINATIM_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _last_geocode_time = time.time()
            data = json.loads(resp.read())
            addr = data.get("address", {})
            # Prefer road name, fall back to neighbourhood/suburb/town
            return (addr.get("road")
                    or addr.get("neighbourhood")
                    or addr.get("suburb")
                    or addr.get("town")
                    or addr.get("city"))
    except Exception as e:
        _last_geocode_time = time.time()
        logger.debug("Reverse geocode failed for %.5f,%.5f: %s", lat, lng, e)
        return None


def _route_counter(local_id: str) -> int:
    """Extract monotonic counter from route local_id (e.g. '00000114--abc' -> 114).

    The counter is always monotonically increasing per-device, making it a
    reliable proxy for route recency — unlike directory mtime which may
    reflect the AGNOS build date instead of actual drive time.
    """
    try:
        return int(local_id.split("--")[0])
    except (ValueError, IndexError):
        return 0

# Add openpilot to path for LogReader
for _p in ["/data/openpilot", "/home/oxygen/openpilot"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


class RouteStore:
    """Scans route directories and builds comma-compatible route objects.

    Uses metadata.json (compatible with route_metadata.py) for persistent
    route metadata. Two-phase approach for fast startup:
    1. Fast scan: directory listing only (instant) — uses cached metadata or route counter
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

        # GPS time → wall_time_nanos (used for route_date in fullname)
        # Only GPS time is used for naming — wallTimeNanos can be AGNOS build date
        gps_t = meta.get("gps_time")
        if gps_t:
            result["wall_time_nanos"] = int(gps_t * 1e9)
            result["create_time"] = gps_t

        # Fall back to creation_time for create_time ordering (but NOT for route naming)
        if "create_time" not in result:
            ct = meta.get("creation_time")
            if ct and isinstance(ct, str) and not ct.startswith("GPS"):
                try:
                    dt = datetime.fromisoformat(ct)
                    result["create_time"] = dt.timestamp()
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

        # Engagement
        ep = meta.get("engagement_pct")
        if ep is not None:
            result["engagement_pct"] = ep

        # Addresses (reverse geocoded)
        sa = meta.get("start_address")
        if sa:
            result["start_address"] = sa
        ea = meta.get("end_address")
        if ea:
            result["end_address"] = ea

        return result

    def _needs_enrich(self, lid: str) -> bool:
        """Check if a route needs background enrichment."""
        meta = self._metadata.get(lid)
        if not meta:
            return True
        # Re-enrich routes missing gps_time (enriched before GPS time extraction)
        if meta.get("enriched") and meta.get("gps_time") is None:
            meta.pop("enriched", None)
            return True
        # Once enrichment has run, don't re-run — GPS/distance are best-effort
        # and come from the 5MB head read. Re-enriching won't find more data.
        if meta.get("enriched"):
            return False
        if not meta.get("car_fingerprint"):
            return True
        if not meta.get("device_type"):
            return True
        return False

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
            # No wallTimeNanos yet (unenriched) — use local_id as route_date
            # placeholder. Avoids using inaccurate directory mtime.
            route_date = local_id

        fullname = f"{dongle_id}/{route_date}"
        # Use route counter for ordering when enriched create_time unavailable.
        # Counter is monotonically increasing and always reliable.
        create_time = internal.get("create_time", _route_counter(local_id))

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
            "engagement_pct": internal.get("engagement_pct"),
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
            "start_address": internal.get("start_address"),
            "end_address": internal.get("end_address"),
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

            # Hide routes with no GPS time (would show "Invalid Date" in UI)
            if not internal.get("wall_time_nanos"):
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

        Tries segment 0 first for initData + GPS + partial distance (from
        the 5MB head read), then checks later segments for GPS only (cold
        starts can delay GPS lock).

        Per-segment distance accumulation is intentionally omitted — the UI
        computes accurate distance from coords.json on first route view.
        The partial seg-0 distance serves as a non-zero fallback estimate.
        """
        sorted_segs = sorted(segments, key=lambda s: s["number"])

        # Parse segment 0 for full metadata (initData + GPS + partial distance)
        result = None
        seg0_rlog = self._find_rlog(sorted_segs[0]["path"]) if sorted_segs else None
        if seg0_rlog:
            result = _parse_rlog_metadata(seg0_rlog)

        if not result:
            result = {}

        # If no GPS coords or time, try later segments (GPS lock can take 5+ min)
        need_coords = not result.get("start_lat")
        need_time = not result.get("gps_time")
        if need_coords or need_time:
            for seg in sorted_segs[1:]:
                rlog = self._find_rlog(seg["path"])
                if not rlog:
                    continue
                if need_coords:
                    gps = _find_first_gps(rlog)
                    if gps:
                        result["start_lat"] = gps[0]
                        result["start_lng"] = gps[1]
                        need_coords = False
                if need_time:
                    gps_t = _find_first_gps_time(rlog)
                    if gps_t:
                        # Adjust back to segment 0 start time
                        result["gps_time"] = gps_t - seg["number"] * 60
                        need_time = False
                if not need_coords and not need_time:
                    break

        # Use seg 0's partial distance as fallback estimate
        seg_dist = result.pop("segment_distance_m", 0.0)
        if seg_dist > 0:
            result["total_distance_m"] = seg_dist

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
        entry["gps_time"] = rlog_meta.get("gps_time")
        entry["source"] = "connect_server"
        entry["enriched"] = True

        return entry

    def geocode_route(self, local_id: str) -> bool:
        """Reverse-geocode start and end locations for a route.

        Gets end GPS from last segment's rlog (last fixed GPS position).
        Returns True if metadata was updated.
        """
        meta = self._metadata.get(local_id)
        if not meta:
            return False

        # Skip if fully geocoded (both start and end)
        if meta.get("start_address") is not None and meta.get("end_address") is not None:
            return False

        updated = False
        if meta.get("start_address") is None:
            gps = meta.get("gps_coordinates")
            if gps and len(gps) == 2 and gps[0] and gps[1]:
                addr = _reverse_geocode(gps[0], gps[1])
                if addr:
                    meta["start_address"] = addr
                    updated = True

        # Find end GPS from last segment's rlog
        info = self._raw.get(local_id)
        if info and meta.get("end_address") is None:
            sorted_segs = sorted(info["segments"], key=lambda s: s["number"], reverse=True)
            for seg in sorted_segs[:3]:
                rlog = self._find_rlog(seg["path"])
                if not rlog:
                    continue
                end_gps = _find_last_gps(rlog)
                if end_gps:
                    addr = _reverse_geocode(end_gps[0], end_gps[1])
                    if addr:
                        meta["end_address"] = addr
                        updated = True
                    break

        if updated:
            self._rebuild_routes()
            self._save_metadata()
            logger.info("Geocoded route %s: %s → %s",
                        local_id, meta.get("start_address"), meta.get("end_address"))
        return updated

    @staticmethod
    def _is_onroad() -> bool:
        """Check if openpilot is currently driving. Yields CPU to controls when True."""
        try:
            return Path("/data/params/d/IsOnroad").read_bytes().strip() == b"1"
        except Exception:
            return False

    async def _enrichment_loop(self):
        """Persistent background loop that enriches route metadata.

        Polls every 90s, re-scans directories, enriches newest routes first.
        Runs as a single asyncio task — no re-entrancy guard needed.
        Pauses while openpilot is driving. Caps at 5 routes per cycle.
        """
        await asyncio.sleep(5)  # let server bind first
        loop = asyncio.get_event_loop()

        while True:
            try:
                # Pause enrichment while driving — zero CPU competition with openpilot
                if self._is_onroad():
                    logger.debug("Onroad — skipping enrichment cycle")
                    await asyncio.sleep(300)
                    continue

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

                # Sort newest-first by route counter (more reliable than mtime
                # which can reflect AGNOS build date instead of actual drive time)
                uncached.sort(key=lambda x: _route_counter(x[0]), reverse=True)
                total_pending = len(uncached)

                # Only enrich the 1 newest route per cycle — remaining routes
                # are enriched on-demand when the user selects them
                uncached = uncached[:1]
                logger.info("Enrichment cycle: processing newest of %d pending routes",
                            total_pending)

                local_id, info = uncached[0]
                try:
                    rlog_meta = await loop.run_in_executor(
                        self._executor, self._enrich_one, local_id, info["segments"])
                    if rlog_meta:
                        entry = self._rlog_to_metadata_entry(local_id, rlog_meta)
                        self._metadata[local_id] = entry
                        self._rebuild_routes()
                        self._save_metadata()
                        logger.info("Enriched newest route %s (%d still pending)",
                                    local_id, total_pending - 1)
                except Exception as e:
                    logger.debug("enrich error for %s: %s", local_id, e)

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

    def ensure_enriched(self, local_id: str) -> bool:
        """On-demand enrichment for a single route. Returns True if newly enriched.

        Called when the user selects a route in the UI. Synchronous — blocks the
        request briefly (~1-2s for 5MB head read) but avoids background CPU churn
        for routes the user never looks at.
        Skips enrichment while driving to avoid competing with openpilot.
        """
        if self._is_onroad():
            return False
        if not self._needs_enrich(local_id):
            return False
        info = self._raw.get(local_id)
        if not info:
            return False
        rlog_meta = self._enrich_one(local_id, info["segments"])
        if rlog_meta:
            entry = self._rlog_to_metadata_entry(local_id, rlog_meta)
            self._metadata[local_id] = entry
            self._rebuild_routes()
            self._save_metadata()
            logger.info("On-demand enriched route %s", local_id)
            return True
        return False
