"""HTTP request handlers for Connect on Device.

All aiohttp handler functions and CORS middleware. Implements the comma-compatible
REST API so the asiusai/connect React frontend works unchanged.
"""

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from math import floor
from pathlib import Path

from aiohttp import web

from bisect import bisect_left
from collections import OrderedDict

from hud_stream import HLS_DIR, HudStreamManager
from rlog_parser import _generate_coords_json, _generate_events_json, extract_hud_snapshots
from route_helpers import _base_url, _clean_route, _resolve_local_id, _route_bookmarks, _route_engagement, _set_route_url
from route_store import _route_counter
from storage_management import DOWNLOAD_FILES, build_download_tar, get_storage_info
import tile_manager

logger = logging.getLogger("connect")

# ─── HUD replay cache ────────────────────────────────────────────────

_HUD_SNAPSHOT_CACHE_MAX = 3  # max segments cached in memory
_hud_snapshot_cache: OrderedDict = OrderedDict()  # (fullname, seg_int) -> snapshot list
_hud_renderer = None  # lazy-initialized HudRenderer singleton

# ─── HUD video rendering state ───────────────────────────────────────
# fullname -> {proc, status_file, output, start, end}
_hud_prerender_tasks: dict = {}

HUD_CACHE_DIR = Path("/data/connect_on_device/hud_cache")
RENDER_SCRIPT = Path(__file__).parent / "render_clip.py"
PYTHON_BIN = "/usr/local/venv/bin/python"
OPENPILOT_DIR = Path("/data/openpilot")
REPLAY_BIN = OPENPILOT_DIR / "tools/replay/replay"


async def _ensure_replay_binary():
    """Check replay binary exists; rebuild from scons cache if missing.

    Returns True if binary is available, False on build failure.
    """
    if REPLAY_BIN.is_file():
        return True

    logger.info("Replay binary missing, rebuilding from scons cache...")
    proc = await asyncio.create_subprocess_exec(
        "scons", "tools/replay/replay", "-j2",
        cwd=str(OPENPILOT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "VIRTUAL_ENV": "/usr/local/venv",
             "PATH": "/usr/local/venv/bin:" + os.environ.get("PATH", "")},
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode == 0 and REPLAY_BIN.is_file():
        logger.info("Replay binary rebuilt successfully")
        return True
    else:
        logger.error("Failed to rebuild replay binary: %s", stdout.decode()[-500:] if stdout else "no output")
        return False

# Quality presets for HUD video rendering
# speed: replay -x flag (lower = more unique frames per second of route time)
# scale: ffmpeg scale filter (None = native 2160x1080)
# fps: output video framerate
# bitrate_mbps: estimated output bitrate for size calculation
QUALITY_PRESETS = {
    "high":   {"speed": 0.2, "scale": None,       "fps": 20, "bitrate_mbps": 3.0},
    "medium": {"speed": 0.2, "scale": "1080:540", "fps": 20, "bitrate_mbps": 1.5},
    "low":    {"speed": 0.5, "scale": "1080:540", "fps": 10, "bitrate_mbps": 0.8},
}


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


# ─── Auth / user ───────────────────────────────────────────────────────

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


async def handle_auth(request: web.Request) -> web.Response:
    """POST /v2/auth/ — return a dummy token"""
    return web.json_response({"access_token": "local-device-token"})


# ─── Device handlers ──────────────────────────────────────────────────

def _device_dict(store) -> dict:
    # Find device_type from most recent enriched route
    device_type = None
    for meta in store._metadata.values():
        dt = meta.get("device_type")
        if dt:
            device_type = dt
            break

    DEVICE_TYPE_MAP = {"tici": "three", "tize": "threex", "mici": "four"}

    return {
        "alias": None,
        "athena_host": None,
        "device_type": DEVICE_TYPE_MAP.get(device_type, "three"),
        "device_type_raw": device_type,
        "agnos_version": store._agnos_version,
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


async def handle_devices(request: web.Request) -> web.Response:
    """GET /v1/me/devices/ — list devices (force rescan on page load/refresh)"""
    store = request.app["store"]
    store.scan(force=True)
    return web.json_response([_device_dict(store)])


async def handle_device_get(request: web.Request) -> web.Response:
    """GET /v1.1/devices/{dongleId}/ — single device"""
    store = request.app["store"]
    store.scan()
    return web.json_response(_device_dict(store))


async def handle_device_stats(request: web.Request) -> web.Response:
    """GET /v1.1/devices/{dongleId}/stats — driving statistics with engagement"""
    store = request.app["store"]
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
    store = request.app["store"]
    routes = store.scan()

    # Find most recent route with GPS (sort by route counter, not create_time)
    for r in sorted(routes.values(), key=lambda x: _route_counter(x.get("_local_id", "")), reverse=True):
        if r.get("start_lat"):
            return web.json_response({
                "dongle_id": store.dongle_id,
                "lat": r["start_lat"],
                "lng": r["start_lng"],
                "time": r["create_time"],
            })

    raise web.HTTPNotFound(text=json.dumps({"error": "No GPS data"}))


# ─── Route list handlers ─────────────────────────────────────────────

async def handle_routes_list(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes — paginated route list"""
    store = request.app["store"]
    routes = store.scan()

    limit = int(request.query.get("limit", 25))
    # Support both counter-based and timestamp-based pagination cursors
    before_counter = int(request.query.get("before_counter", 999999999))
    created_before = float(request.query.get("created_before", time.time() + 86400))

    # Sort by route counter (reliable, monotonically increasing per device)
    sorted_routes = sorted(routes.values(),
                           key=lambda r: _route_counter(r.get("_local_id", "")),
                           reverse=True)

    route_list = []
    for r in sorted_routes:
        counter = _route_counter(r.get("_local_id", ""))
        # Skip routes at or after the cursor
        if counter >= before_counter:
            continue
        r_with_url = _set_route_url(r, request)
        cleaned = _clean_route(r_with_url)
        cleaned["route_counter"] = counter
        cleaned["is_preserved"] = store.is_preserved(r["_local_id"])
        route_list.append(cleaned)
        if len(route_list) >= limit:
            break

    return web.json_response(route_list)


async def handle_routes_segments(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes_segments — route with segment data"""
    store = request.app["store"]
    routes = store.scan()

    route_str = request.query.get("route_str", "")
    limit = int(request.query.get("limit", 100))

    # On-demand enrichment when viewing a specific route
    if route_str:
        local_id = store.get_local_id(route_str)
        if local_id:
            loop = asyncio.get_event_loop()
            enriched = await loop.run_in_executor(None, store.ensure_enriched, local_id)
            if enriched:
                routes = store.scan()

    sorted_routes = sorted(routes.values(),
                           key=lambda r: _route_counter(r.get("_local_id", "")),
                           reverse=True)

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


async def handle_preserved_routes(request: web.Request) -> web.Response:
    """GET /v1/devices/{dongleId}/routes/preserved — return preserved routes"""
    store = request.app["store"]
    routes = store.scan()
    preserved = []
    for r in sorted(routes.values(),
                    key=lambda r: _route_counter(r.get("_local_id", "")),
                    reverse=True):
        if store.is_preserved(r["_local_id"]):
            r_with_url = _set_route_url(r, request)
            cleaned = _clean_route(r_with_url)
            cleaned["is_preserved"] = True
            preserved.append(cleaned)
    return web.json_response(preserved)


# ─── Route detail handlers ───────────────────────────────────────────

async def handle_route_get(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/ — single route detail"""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    # On-demand enrichment: enrich when user selects a route
    local_id = route["_local_id"]
    loop = asyncio.get_event_loop()
    enriched = await loop.run_in_executor(None, store.ensure_enriched, local_id)
    if enriched:
        route = store.get_route(route_name)

    # Compute and persist engagement % from cached events.json
    meta = store._metadata.get(local_id, {})
    if meta and meta.get("engagement_pct") is None:
        engaged_ms, total_ms = _route_engagement(store, route)
        if total_ms > 0 and engaged_ms > 0:
            pct = round(engaged_ms / total_ms * 100)
            meta["engagement_pct"] = pct
            store._rebuild_routes()
            store._save_metadata()
            route = store.get_route(route_name)

    # Reverse-geocode start/end addresses (runs in thread pool, cached)
    needs_geocode = meta.get("start_address") is None or meta.get("end_address") is None
    if meta and needs_geocode and meta.get("gps_coordinates"):
        await loop.run_in_executor(None, store.geocode_route, local_id)
        route = store.get_route(route_name)

    # Collect bookmarks from cached events.json (no rlog parsing needed)
    bookmarks = _route_bookmarks(route)

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(local_id)
    if bookmarks:
        cleaned["bookmarks"] = bookmarks
    return web.json_response(cleaned)


async def handle_route_enrich(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/enrich — re-enrich a route.

    Clears cached events.json/coords.json so they regenerate with latest
    parser code (e.g. new bookmark detection). Also re-runs metadata enrichment.
    """
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)

    loop = asyncio.get_event_loop()
    cleared = await loop.run_in_executor(None, store.clear_derived, local_id)
    await loop.run_in_executor(None, store.ensure_enriched, local_id)

    route_name = request.match_info["routeName"].replace("|", "/")
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(local_id)
    cleaned["cleared_files"] = cleared
    return web.json_response(cleaned)


async def handle_route_files(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/files — list available files per segment"""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]
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


async def handle_route_manifest(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/manifest.m3u8 — HLS manifest for qcamera segments"""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]
    route = store.get_route(route_name)

    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    dongle_id = route["dongle_id"]
    route_date = route["fullname"].split("/")[-1]
    max_seg = route["maxqlog"]
    seg_set = {s["number"]: s for s in route["_segments"]}

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:61",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for i in range(max_seg + 1):
        seg = seg_set.get(i)
        if i > 0:
            # Each segment has independent PTS timestamps
            lines.append("#EXT-X-DISCONTINUITY")
        if seg and "qcamera.ts" in seg["files"]:
            lines.append("#EXTINF:60.0,")
            lines.append(f"/connectdata/{dongle_id}/{route_date}/{i}/qcamera.ts")
        else:
            lines.extend(["#EXT-X-GAP", "#EXTINF:60.0,", "gap"])
    lines.append("#EXT-X-ENDLIST")

    return web.Response(
        text="\n".join(lines),
        content_type="application/vnd.apple.mpegurl",
    )


async def handle_share_signature(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/share_signature — dummy signature"""
    return web.json_response({
        "exp": "9999999999",
        "sig": "local",
    })


# ─── Storage management handlers ─────────────────────────────────────

async def handle_route_delete(request: web.Request) -> web.Response:
    """DELETE /v1/route/{routeName}/ — soft-delete (hide) a route"""
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.hide_route(local_id)
    logger.info("Route hidden: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_note(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/note — set or update a route note"""
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)
    body = await request.json()
    note = body.get("note", "")
    store.set_note(local_id, note)
    return web.json_response({"status": "ok"})


async def handle_route_preserve(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/preserve — mark route as preserved"""
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.preserve_route(local_id)
    logger.info("Route preserved: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_unpreserve(request: web.Request) -> web.Response:
    """DELETE /v1/route/{routeName}/preserve — remove preservation"""
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)
    store.unpreserve_route(local_id)
    logger.info("Route unpreserved: %s", local_id)
    return web.json_response({"success": 1})


async def handle_route_download(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/download?files=rlog,qcamera&segments=0,1,2 — stream tar.gz"""
    store = request.app["store"]
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


def _decimal_to_dms(decimal):
    """Convert decimal degrees to EXIF DMS as IFDRational tuples."""
    from PIL.TiffImagePlugin import IFDRational
    d = int(decimal)
    m = int((decimal - d) * 60)
    s = round((decimal - d - m / 60) * 3600, 2)
    return (IFDRational(d), IFDRational(m), IFDRational(int(s * 100), 100))


def _extract_frame(hevc_path: str, offset: float) -> bytes:
    """Extract a single JPEG frame from fcamera.hevc at the given offset.

    Raw HEVC lacks container timestamps so cv2 seeking is broken.
    Strategy: mux to mp4 (codec copy, ~1.5s one-time) then cv2 seeks in the
    container.  Cached mp4 is stored in /tmp to avoid writing to route data.
    Runs in executor thread.
    """
    import cv2
    import subprocess
    import hashlib

    # Build a stable cache path on /data (tmpfs /tmp is only 150MB, too small for fcamera)
    cache_dir = "/data/connect_on_device/cache"
    os.makedirs(cache_dir, exist_ok=True)
    path_hash = hashlib.md5(hevc_path.encode()).hexdigest()[:12]
    mp4_path = os.path.join(cache_dir, f"fcamera_{path_hash}.mp4")

    if not os.path.exists(mp4_path):
        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4', dir=cache_dir)
        os.close(fd)
        try:
            subprocess.run([
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-framerate', '20', '-i', hevc_path,
                '-c', 'copy', '-movflags', '+faststart', tmp_path,
            ], check=True, timeout=60)
            os.rename(tmp_path, mp4_path)
        except Exception:
            os.unlink(tmp_path)
            raise

    cap = cv2.VideoCapture(mp4_path)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, offset * 1000)
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"cv2 failed to read frame at {offset:.1f}s")
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise RuntimeError("cv2 JPEG encoding failed")
        return buf.tobytes()
    finally:
        cap.release()


def _lookup_gps(coords: list, t: float) -> dict:
    """Find GPS point closest to time t from coords list. Returns dict with lat, lng, speed, bearing."""
    if not coords:
        return {}
    times = [c["t"] for c in coords]
    idx = bisect_left(times, t)
    if idx == 0:
        best_idx = 0
    elif idx >= len(coords):
        best_idx = len(coords) - 1
    elif (t - times[idx - 1]) <= (times[idx] - t):
        best_idx = idx - 1
    else:
        best_idx = idx
    best = coords[best_idx]
    result = {k: best.get(k) for k in ("lat", "lng", "speed")}

    # Compute bearing from consecutive GPS points
    if best_idx + 1 < len(coords):
        nxt = coords[best_idx + 1]
    elif best_idx > 0:
        nxt = best
        best = coords[best_idx - 1]
    else:
        return result

    import math
    lat1, lon1 = math.radians(best["lat"]), math.radians(best["lng"])
    lat2, lon2 = math.radians(nxt["lat"]), math.radians(nxt["lng"])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y)) % 360
    result["bearing"] = bearing
    return result


def _load_calibration(seg_dir: Path) -> dict | None:
    """Load cached calibration.json, or extract from rlog and cache it."""
    calib_file = seg_dir / "calibration.json"
    if calib_file.exists():
        try:
            return json.loads(calib_file.read_text())
        except Exception:
            pass

    # Extract from rlog
    rlog = seg_dir / "rlog.zst"
    if not rlog.exists():
        rlog = seg_dir / "rlog"
    if not rlog.exists():
        return None

    try:
        # Lazy import — only needed on C3 where openpilot is installed
        import sys
        if "/data/openpilot" not in sys.path:
            sys.path.insert(0, "/data/openpilot")
        from tools.lib.logreader import LogReader

        calib = None
        for msg in LogReader(str(rlog)):
            if msg.which() == "liveCalibration":
                c = msg.liveCalibration
                rpy = list(c.rpyCalib)
                try:
                    height = list(c.height)
                except Exception:
                    height = [1.22]
                calib = {"rpyCalib": rpy, "height": height}
                break

        if calib:
            calib_file.write_text(json.dumps(calib))
            return calib
    except Exception:
        pass
    return None


# C3 fcamera (tici AR0231) intrinsics — from openpilot common/transformations/camera.py
_FCAM_WIDTH = 1928
_FCAM_HEIGHT = 1208
_FCAM_FOCAL_LENGTH = 2648.0  # pixels
import math as _math
_FCAM_HFOV = 2 * _math.degrees(_math.atan(_FCAM_WIDTH / 2 / _FCAM_FOCAL_LENGTH))  # ~40°
_FCAM_VFOV = 2 * _math.degrees(_math.atan(_FCAM_HEIGHT / 2 / _FCAM_FOCAL_LENGTH))  # ~25.6°


def _add_exif(frame_bytes: bytes, gps: dict, calibration: dict | None,
              timestamp: float, route_ref: str) -> bytes:
    """Embed rich EXIF metadata into a JPEG frame.

    EXIF = immutable capture-time facts:
    - GPS (lat, lon, heading, speed)
    - Timestamp (UTC)
    - Route reference (dongle/route/segment/frame)
    - Camera intrinsics (focal length, resolution, FOV)
    - Camera pose (height, pitch angle from calibration)
    """
    from PIL import Image
    from PIL.ExifTags import IFD, GPS as GPSTags, Base
    from PIL.TiffImagePlugin import IFDRational

    img = Image.open(io.BytesIO(frame_bytes))
    exif = img.getexif()

    # --- GPS IFD ---
    lat, lng = gps.get("lat"), gps.get("lng")
    if lat is not None and lng is not None:
        gps_ifd = exif.get_ifd(IFD.GPSInfo)
        gps_ifd[GPSTags.GPSLatitudeRef] = 'N' if lat >= 0 else 'S'
        gps_ifd[GPSTags.GPSLatitude] = _decimal_to_dms(abs(lat))
        gps_ifd[GPSTags.GPSLongitudeRef] = 'E' if lng >= 0 else 'W'
        gps_ifd[GPSTags.GPSLongitude] = _decimal_to_dms(abs(lng))
        if gps.get("bearing") is not None:
            gps_ifd[GPSTags.GPSImgDirectionRef] = 'T'  # True north
            gps_ifd[GPSTags.GPSImgDirection] = IFDRational(round(gps["bearing"] * 100), 100)
        if gps.get("speed") is not None:
            gps_ifd[GPSTags.GPSSpeedRef] = 'K'  # km/h
            gps_ifd[GPSTags.GPSSpeed] = IFDRational(round(gps["speed"] * 3.6 * 100), 100)

    # --- Timestamp ---
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    exif[Base.DateTimeOriginal] = dt.strftime("%Y:%m:%d %H:%M:%S")

    # --- UserComment: structured JSON with all metadata ---
    meta = {
        "route": route_ref,
        "camera": {
            "model": "AR0231",
            "width": _FCAM_WIDTH,
            "height": _FCAM_HEIGHT,
            "focal_length_px": _FCAM_FOCAL_LENGTH,
            "hfov_deg": round(_FCAM_HFOV, 1),
            "vfov_deg": round(_FCAM_VFOV, 1),
        },
    }
    if calibration:
        rpy = calibration.get("rpyCalib", [0, 0, 0])
        height = calibration.get("height", [1.22])
        meta["pose"] = {
            "height_m": round(height[0], 4),
            "pitch_deg": round(_math.degrees(rpy[1]), 3),
            "yaw_deg": round(_math.degrees(rpy[2]), 3),
            "roll_deg": round(_math.degrees(rpy[0]), 3),
        }
    if gps.get("speed") is not None:
        meta["speed_ms"] = round(gps["speed"], 2)
    if gps.get("bearing") is not None:
        meta["bearing_deg"] = round(gps["bearing"], 1)

    # EXIF UserComment: JSON-encoded metadata for programmatic access
    exif[Base.UserComment] = json.dumps(meta)
    # ImageDescription: human-readable summary
    exif[Base.ImageDescription] = route_ref

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95, exif=exif.tobytes())
    return buf.getvalue()


async def handle_screenshot(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/screenshot — extract fcamera frame with EXIF metadata."""
    store = request.app["store"]
    route_name = request.match_info["routeName"].replace("|", "/")
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON body"}))

    t = body.get("time", 0)
    segment = int(t // 60)
    offset = t % 60

    fullname = route["fullname"]
    local_id = route["_local_id"]

    # Find fcamera.hevc (full resolution 1928x1208)
    fcamera = store.resolve_segment_path(fullname, segment, "fcamera.hevc")
    if not fcamera:
        raise web.HTTPNotFound(text=json.dumps({"error": f"No fcamera.hevc for segment {segment}"}))

    # Extract frame via cached mp4 mux + cv2 seek in thread executor
    loop = asyncio.get_event_loop()
    try:
        frame_bytes = await loop.run_in_executor(
            store._executor, _extract_frame, str(fcamera), offset
        )
    except Exception as e:
        raise web.HTTPInternalServerError(text=json.dumps({"error": f"Frame extraction failed: {e}"}))

    # Look up GPS (lat, lng, speed, bearing) from coords.json
    seg_dir = store.data_dir / f"{local_id}--{segment}"
    gps = {}
    coords_file = seg_dir / "coords.json"
    if coords_file.exists():
        try:
            coords = json.loads(coords_file.read_text())
            gps = _lookup_gps(coords, t)
        except Exception:
            pass

    # Load calibration (camera pose: height, pitch, yaw, roll)
    calibration = await loop.run_in_executor(store._executor, _load_calibration, seg_dir)

    # Build EXIF metadata
    create_time = route.get("create_time", 0)
    timestamp = create_time + t
    route_ref = f"{fullname}/{local_id}/{segment}/{offset:.2f}"

    try:
        jpeg_bytes = await loop.run_in_executor(
            None, _add_exif, frame_bytes, gps, calibration, timestamp, route_ref
        )
    except Exception as e:
        logging.getLogger("connect").warning("EXIF embedding failed: %s", e)
        jpeg_bytes = frame_bytes

    # Build filename: {route_date}_{MM}m{SS}s.jpg
    route_date = fullname.split("/")[-1]  # e.g. "2026-02-20--10-47-46"
    mm = int(t // 60)
    ss = int(t % 60)
    filename = f"{route_date}_{mm:02d}m{ss:02d}s.jpg"

    return web.Response(
        body=jpeg_bytes,
        content_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def handle_frame(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/frame?t=123.45 — return fcamera JPEG for the given time.

    URL-friendly: open in browser or use in <img> tags.
    """
    store = request.app["store"]
    route_name = request.match_info["routeName"].replace("|", "/")
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    try:
        t = float(request.query.get("t", 0))
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid t parameter"}))

    segment = int(t // 60)
    offset = t % 60

    fcamera = store.resolve_segment_path(route["fullname"], segment, "fcamera.hevc")
    if not fcamera:
        raise web.HTTPNotFound(text=json.dumps({"error": f"No fcamera.hevc for segment {segment}"}))

    fullname = route["fullname"]
    local_id = route["_local_id"]

    loop = asyncio.get_event_loop()
    try:
        frame_bytes = await loop.run_in_executor(
            store._executor, _extract_frame, str(fcamera), offset
        )
    except Exception as e:
        raise web.HTTPInternalServerError(text=json.dumps({"error": f"Frame extraction failed: {e}"}))

    # Enrich with EXIF (GPS, calibration, camera intrinsics)
    seg_dir = store.data_dir / f"{local_id}--{segment}"
    gps = {}
    coords_file = seg_dir / "coords.json"
    if coords_file.exists():
        try:
            gps = _lookup_gps(json.loads(coords_file.read_text()), t)
        except Exception:
            pass

    calibration = await loop.run_in_executor(store._executor, _load_calibration, seg_dir)

    create_time = route.get("create_time", 0)
    timestamp = create_time + t
    route_ref = f"{fullname}/{local_id}/{segment}/{offset:.2f}"

    try:
        jpeg_bytes = await loop.run_in_executor(
            None, _add_exif, frame_bytes, gps, calibration, timestamp, route_ref
        )
    except Exception as e:
        logging.getLogger("connect").warning("EXIF embedding failed: %s", e)
        jpeg_bytes = frame_bytes

    return web.Response(
        body=jpeg_bytes,
        content_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def handle_storage(request: web.Request) -> web.Response:
    """GET /v1/storage — disk usage and route management stats"""
    store = request.app["store"]
    return web.json_response(get_storage_info(store))


# ─── HUD replay rendering ────────────────────────────────────────────

def _find_closest_snapshot(snapshots: list, t_ms: int):
    """Binary search for the snapshot closest to t_ms."""
    if not snapshots:
        return None
    offsets = [s["offset_ms"] for s in snapshots]
    idx = bisect_left(offsets, t_ms)
    # Pick the closer of idx-1 and idx
    if idx == 0:
        return snapshots[0]
    if idx >= len(snapshots):
        return snapshots[-1]
    if (t_ms - offsets[idx - 1]) <= (offsets[idx] - t_ms):
        return snapshots[idx - 1]
    return snapshots[idx]


def _render_hud_frame(rlog_path: str, fullname: str, seg_int: int, t_ms: int) -> bytes | None:
    """Parse snapshots (if not cached) and render a single HUD frame. Runs in executor."""
    global _hud_renderer, _hud_snapshot_cache

    cache_key = (fullname, seg_int)

    # Memory cache check
    if cache_key in _hud_snapshot_cache:
        _hud_snapshot_cache.move_to_end(cache_key)
        snapshots = _hud_snapshot_cache[cache_key]
    else:
        snapshots = extract_hud_snapshots(rlog_path)
        if not snapshots:
            return None
        _hud_snapshot_cache[cache_key] = snapshots
        # Evict oldest if over limit
        while len(_hud_snapshot_cache) > _HUD_SNAPSHOT_CACHE_MAX:
            _hud_snapshot_cache.popitem(last=False)

    snapshot = _find_closest_snapshot(snapshots, t_ms)
    if snapshot is None:
        return None

    # Lazy-init renderer
    if _hud_renderer is None:
        from hud_renderer import HudRenderer
        _hud_renderer = HudRenderer()

    return _hud_renderer.render_from_snapshot(snapshot)


async def _handle_hud_frame(request, store, fullname: str, seg_int: int, t_ms: int) -> web.Response:
    """Serve a rendered HUD overlay frame for replay mode."""
    local_id = store.get_local_id(fullname)
    if not local_id:
        raise web.HTTPNotFound()

    seg_dir = store.data_dir / f"{local_id}--{seg_int}"

    # Disk cache check
    cache_dir = seg_dir / "hud_cache"
    cache_file = cache_dir / f"{t_ms}.webp"
    if cache_file.exists():
        return web.Response(
            body=cache_file.read_bytes(),
            content_type="image/webp",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Find rlog
    rlog_path = store.resolve_segment_path(fullname, seg_int, "rlog.zst")
    if not rlog_path:
        rlog_path = store.resolve_segment_path(fullname, seg_int, "rlog")
    if not rlog_path:
        raise web.HTTPNotFound(text="No rlog for HUD rendering")

    loop = asyncio.get_event_loop()
    frame_bytes = await loop.run_in_executor(
        store._executor, _render_hud_frame, str(rlog_path), fullname, seg_int, t_ms
    )

    if not frame_bytes:
        raise web.HTTPNotFound(text="No HUD data at this offset")

    # Cache to disk
    try:
        cache_dir.mkdir(exist_ok=True)
        cache_file.write_bytes(frame_bytes)
    except Exception:
        pass

    return web.Response(
        body=frame_bytes,
        content_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ─── HUD pre-render endpoints ────────────────────────────────────────

def _hud_cache_path(fullname: str, start: float, end: float, quality: str = "high") -> Path:
    """Build the cache path for a rendered HUD video."""
    safe = fullname.replace("/", "_")
    return HUD_CACHE_DIR / f"{safe}_{int(start)}_{int(end)}_{quality}.mp4"


def _read_status_file(path: str) -> dict | None:
    """Read a render_clip.py status JSON file."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


async def handle_hud_prerender(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/hud/prerender — start HUD video rendering via openpilot UI."""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    fullname = route["fullname"]
    max_seg = route["maxqlog"]

    # Parse range from body
    try:
        body = await request.json()
    except Exception:
        body = {}

    start_sec = body.get("start", 0)
    end_sec = body.get("end", (max_seg + 1) * 60)
    duration = end_sec - start_sec
    if duration <= 0:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid time range"}))

    # Accept explicit speed/scale/fps, or fall back to quality preset
    quality = body.get("quality")
    if quality and quality in QUALITY_PRESETS:
        preset = QUALITY_PRESETS[quality]
        render_speed = preset["speed"]
        render_scale = preset["scale"]
        render_fps = preset["fps"]
        render_bitrate = preset["bitrate_mbps"]
    else:
        # Two-pass render: speed is fixed at 0.2 (set in render_clip.py)
        render_speed = 0.2
        render_scale = body.get("scale")  # None or "1080:540"
        render_fps = int(body.get("fps", 20))
        # At 0.2x speed, 5fps capture = 25fps unique route content → ~3 Mbps full res
        base_bitrate = 3.0
        render_bitrate = base_bitrate * (0.5 if render_scale else 1.0)

    estimated_mb = round(duration * render_bitrate / 8)
    # Single-pass render at 0.2x speed + setup overhead
    wall_duration = round(duration / render_speed + 30)

    # Build a cache key from the actual render params
    cache_tag = f"s{render_speed:.2f}_f{render_fps}"
    if render_scale:
        cache_tag += f"_{render_scale.replace(':', 'x')}"

    # Check cache — already rendered?
    cache_mp4 = _hud_cache_path(fullname, start_sec, end_sec, cache_tag)
    if cache_mp4.exists() and cache_mp4.stat().st_size > 1000:
        return web.json_response({
            "status": "complete",
            "elapsed_sec": duration,
            "total_sec": duration,
            "estimated_mb": estimated_mb,
            "wall_duration": wall_duration,
        })

    # Check running task for same route+range
    existing = _hud_prerender_tasks.get(fullname)
    if existing:
        ex_start = existing.get("start", 0)
        ex_end = existing.get("end", 0)
        proc = existing.get("proc")
        if ex_start == start_sec and ex_end == end_sec and proc and proc.returncode is None:
            # Same render in progress — read status file for progress
            status_data = _read_status_file(existing["status_file"])
            if status_data:
                return web.json_response(status_data)
            return web.json_response({"status": "rendering", "elapsed_sec": 0, "total_sec": duration})

        # Different range or finished process — kill old one
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass

    # Ensure replay binary is available (auto-rebuild from scons cache if needed)
    if not await _ensure_replay_binary():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Replay binary not available and rebuild failed"}))

    # Prepare paths
    HUD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_id = route["_local_id"]
    dongle_id = route["dongle_id"]
    status_file = str(HUD_CACHE_DIR / f"{local_id}_{int(start_sec)}_{int(end_sec)}.status.json")
    output = str(cache_mp4)

    # Determine python and script paths
    python_bin = PYTHON_BIN if os.path.isfile(PYTHON_BIN) else sys.executable
    script = str(RENDER_SCRIPT)

    # Launch render_clip.py as subprocess (speed is fixed at 0.2 in render_clip.py)
    cmd = [
        python_bin, script,
        "--route-name", fullname.replace("/", "|"),
        "--local-id", local_id,
        "--dongle-id", dongle_id,
        "--data-dir", str(store.data_dir),
        "--start", str(start_sec),
        "--end", str(end_sec),
        "--output-fps", str(render_fps),
        "--output", output,
        "--status-file", status_file,
    ]
    if render_scale:
        cmd.extend(["--scale", render_scale])

    logger.info("Launching HUD render: %s (%.0fs-%.0fs, speed=%.2f, fps=%d, scale=%s)",
                fullname, start_sec, end_sec, render_speed, render_fps, render_scale)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _hud_prerender_tasks[fullname] = {
        "proc": proc,
        "status_file": status_file,
        "output": output,
        "start": start_sec,
        "end": end_sec,
    }

    return web.json_response({
        "status": "rendering",
        "elapsed_sec": 0,
        "total_sec": duration,
        "estimated_mb": estimated_mb,
        "wall_duration": wall_duration,
    })


async def handle_hud_progress(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/hud/progress — check HUD video render progress."""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]

    # First check if cache exists (completed previously)
    route = store.get_route(route_name)
    task = _hud_prerender_tasks.get(route_name)

    if not task:
        return web.json_response({"status": "idle", "elapsed_sec": 0, "total_sec": 0})

    # Read status from the status file written by render_clip.py
    status_data = _read_status_file(task["status_file"])
    if status_data:
        # If subprocess says complete, verify the file exists
        if status_data.get("status") == "complete":
            output_path = Path(task["output"])
            if output_path.exists() and output_path.stat().st_size > 1000:
                return web.json_response(status_data)
            else:
                status_data["status"] = "error"
                status_data["error"] = "Output file missing after render"
        return web.json_response(status_data)

    # No status file yet — check if process is still running
    proc = task.get("proc")
    if proc and proc.returncode is not None:
        return web.json_response({"status": "error", "error": "Render process exited unexpectedly"})

    duration = task["end"] - task["start"]
    return web.json_response({"status": "rendering", "elapsed_sec": 0, "total_sec": duration})


async def handle_hud_cancel(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/hud/cancel — abort a running HUD video render."""
    route_name = request.match_info["routeName"].replace("|", "/")
    task = _hud_prerender_tasks.get(route_name)
    if not task:
        return web.json_response({"status": "idle"})

    proc = task.get("proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
        logger.info("HUD render cancelled for %s", route_name)

    # Clean up status file and cached output
    try:
        sf = Path(task["status_file"])
        if sf.exists():
            sf.unlink()
        out = Path(task["output"])
        if out.exists():
            out.unlink()
    except Exception:
        pass

    del _hud_prerender_tasks[route_name]
    return web.json_response({"status": "cancelled"})


async def handle_hud_video(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/hud/video — serve the rendered HUD MP4."""
    route_name = request.match_info["routeName"].replace("|", "/")
    store = request.app["store"]

    task = _hud_prerender_tasks.get(route_name)
    if not task:
        raise web.HTTPNotFound(text=json.dumps({"error": "No HUD render for this route"}))

    output_path = Path(task["output"])
    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise web.HTTPNotFound(text=json.dumps({"error": "HUD video not ready"}))

    return web.FileResponse(
        output_path,
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ─── HUD live streaming ──────────────────────────────────────────────

async def handle_hud_stream_start(request: web.Request) -> web.Response:
    """POST /v1/hud/stream/start — start HUD live streaming pipeline."""
    mgr: HudStreamManager = request.app.get("stream_manager")
    if not mgr:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Streaming not available on this device"}))

    try:
        body = await request.json()
    except Exception:
        body = {}

    route_name = body.get("route", "")
    if not route_name:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Missing route name"}))

    route_name = route_name.replace("|", "/")
    store = request.app["store"]
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(
            text=json.dumps({"error": f"Route {route_name} not found"}))

    start_sec = body.get("start", 0)

    # Ensure replay binary is available (auto-rebuild from scons cache if needed)
    if not await _ensure_replay_binary():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Replay binary not available and rebuild failed"}))

    await mgr.start(
        route_name=route["fullname"],
        local_id=route["_local_id"],
        dongle_id=route["dongle_id"],
        data_dir=str(store.data_dir),
        start_sec=start_sec,
        max_seg=route.get("maxqlog", -1),
    )

    return web.json_response(mgr.status)


async def handle_hud_stream_stop(request: web.Request) -> web.Response:
    """POST /v1/hud/stream/stop — stop HUD live streaming, restore compositor."""
    mgr: HudStreamManager = request.app.get("stream_manager")
    if mgr:
        await mgr.stop()
    return web.json_response({"status": "idle"})


async def handle_hud_stream_status(request: web.Request) -> web.Response:
    """GET /v1/hud/stream/status — check streaming pipeline status."""
    mgr: HudStreamManager = request.app.get("stream_manager")
    if not mgr:
        return web.json_response({"status": "idle"})
    return web.json_response(mgr.status)


async def handle_hud_stream_serve(request: web.Request) -> web.Response:
    """GET /v1/hud/stream/{filename} — serve HLS .m3u8 and .ts files."""
    filename = request.match_info["filename"]

    # Security: only allow HLS files
    if not (filename.endswith(".m3u8") or filename.endswith(".ts")):
        raise web.HTTPForbidden(text="Only .m3u8 and .ts files allowed")

    filepath = HLS_DIR / filename
    if not filepath.exists():
        raise web.HTTPNotFound()

    if filename.endswith(".m3u8"):
        # Playlist — never cache (live stream, contents change)
        return web.Response(
            body=filepath.read_bytes(),
            content_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache, no-store"},
        )
    else:
        # Segment — immutable once written, safe to cache
        return web.FileResponse(
            filepath,
            headers={
                "Content-Type": "video/mp2t",
                "Cache-Control": "public, max-age=60",
            },
        )


# ─── connectdata file serving ─────────────────────────────────────────

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
    if filename not in allowed and filename not in derived and filename != "hud":
        raise web.HTTPForbidden(text="File not allowed")

    fullname = f"{dongle_id}/{route_date}"
    store = request.app["store"]
    seg_int = int(segment)

    # HUD overlay frame: render from rlog snapshots
    if filename == "hud":
        t_ms = int(request.query.get("t", "0"))
        return await _handle_hud_frame(request, store, fullname, seg_int, t_ms)

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
        # Use store's single-worker executor to serialize rlog decompression.
        # Prevents parallel requests from decompressing 30 rlogs simultaneously
        # (each ~200MB), keeping peak memory bounded to 1 rlog at a time.
        data = await loop.run_in_executor(store._executor, gen_fn, str(rlog), seg_int)

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


# ─── Stub handlers ────────────────────────────────────────────────────

async def handle_stub_empty_array(request: web.Request) -> web.Response:
    return web.json_response([])

async def handle_stub_error(request: web.Request) -> web.Response:
    return web.json_response({"error": "Not available on local device"}, status=501)


# ─── WebRTC proxy ─────────────────────────────────────────────────────

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


# ─── HUD WebSocket ────────────────────────────────────────────────────

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


# ─── OSM tile management ──────────────────────────────────────────────

_tile_download_thread = None


# ─── BMW params ──────────────────────────────────────────────────────

PARAMS_DIR = "/data/params/d"

# ─── Software update management ──────────────────────────────────────

SOFTWARE_PARAMS = [
    "GitBranch", "GitCommit", "GitCommitDate",
    "UpdaterState", "UpdaterTargetBranch",
    "UpdaterCurrentDescription", "UpdaterNewDescription",
    "UpdaterCurrentReleaseNotes", "UpdaterNewReleaseNotes",
    "UpdaterAvailableBranches",
    "UpdateAvailable", "UpdaterFetchAvailable",
    "LastUpdateTime", "UpdateFailedCount",
    "IsTestedBranch",
]

_SOFTWARE_BOOL_PARAMS = {"UpdateAvailable", "UpdaterFetchAvailable", "IsTestedBranch"}
_SOFTWARE_INT_PARAMS = {"UpdateFailedCount"}


async def handle_software_get(request: web.Request) -> web.Response:
    """GET /v1/software — read all software-related params."""
    result = {}
    for key in SOFTWARE_PARAMS:
        path = f"{PARAMS_DIR}/{key}"
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
        except FileNotFoundError:
            raw = ""

        if key in _SOFTWARE_BOOL_PARAMS:
            result[key] = raw == "1"
        elif key in _SOFTWARE_INT_PARAMS:
            try:
                result[key] = int(raw) if raw else 0
            except ValueError:
                result[key] = 0
        elif key == "UpdaterAvailableBranches":
            result[key] = [b for b in raw.split(",") if b] if raw else []
        elif key == "GitCommitDate":
            # Raw: "'1770870385 2026-02-12 12:26:25 +0800'" → "2026-02-12 12:26:25"
            import re
            m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', raw)
            result[key] = m.group(1) if m else raw.strip("'\" ")
        else:
            result[key] = raw
    return web.json_response(result)


async def handle_software_check(request: web.Request) -> web.Response:
    """POST /v1/software/check — send SIGUSR1 to updater to trigger check."""
    try:
        subprocess.run(["pkill", "-SIGUSR1", "-f", "system.updated.updated"],
                       capture_output=True, timeout=5)
    except Exception as e:
        logger.warning("Failed to signal updater for check: %s", e)
    return web.json_response({"status": "checking"})


async def handle_software_download(request: web.Request) -> web.Response:
    """POST /v1/software/download — send SIGHUP to updater to trigger download."""
    try:
        subprocess.run(["pkill", "-SIGHUP", "-f", "system.updated.updated"],
                       capture_output=True, timeout=5)
    except Exception as e:
        logger.warning("Failed to signal updater for download: %s", e)
    return web.json_response({"status": "downloading"})


async def handle_software_install(request: web.Request) -> web.Response:
    """POST /v1/software/install — write DoReboot param to trigger reboot."""
    try:
        with open(f"{PARAMS_DIR}/DoReboot", "w") as f:
            f.write("1")
    except Exception as e:
        logger.error("Failed to set DoReboot: %s", e)
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"status": "rebooting"})


async def handle_software_branch(request: web.Request) -> web.Response:
    """POST /v1/software/branch — set UpdaterTargetBranch and trigger check."""
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON body"}))

    branch = body.get("branch", "").strip()
    if not branch:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Missing branch name"}))

    try:
        with open(f"{PARAMS_DIR}/UpdaterTargetBranch", "w") as f:
            f.write(branch)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    # Trigger a check for the new branch
    try:
        subprocess.run(["pkill", "-SIGUSR1", "-f", "system.updated.updated"],
                       capture_output=True, timeout=5)
    except Exception:
        pass

    return web.json_response({"status": "ok", "branch": branch})


async def handle_software_uninstall(request: web.Request) -> web.Response:
    """POST /v1/software/uninstall — write DoUninstall param."""
    try:
        with open(f"{PARAMS_DIR}/DoUninstall", "w") as f:
            f.write("1")
    except Exception as e:
        logger.error("Failed to set DoUninstall: %s", e)
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"status": "uninstalling"})


async def handle_lateral_delay(request: web.Request) -> web.Response:
    """GET /v1/lateral-delay — read LiveDelay capnp param."""
    path = f"{PARAMS_DIR}/LiveDelay"
    try:
        raw = open(path, "rb").read()
    except FileNotFoundError:
        return web.json_response({"status": "no data"})

    try:
        sys.path.insert(0, "/data/openpilot") if "/data/openpilot" not in sys.path else None
        from cereal import log
        with log.Event.from_bytes(raw) as msg:
            ld = msg.liveDelay
            return web.json_response({
                "lateralDelay": round(ld.lateralDelay, 4),
                "lateralDelayEstimate": round(ld.lateralDelayEstimate, 4),
                "lateralDelayEstimateStd": round(ld.lateralDelayEstimateStd, 6),
                "validBlocks": ld.validBlocks,
                "status": str(ld.status),
                "calPerc": ld.calPerc,
            })
    except Exception as e:
        logger.warning("Failed to decode LiveDelay: %s", e)
        return web.json_response({"error": str(e)}, status=500)


BMW_PARAMS = {
    "DccCalibrationMode": {"type": "bool", "label": "DCC Calibration Mode"},
    "LaneCenteringCorrection": {"type": "bool", "label": "Lane Centering Correction"},
    "MapdSpeedLimitControlEnabled": {"type": "bool", "label": "Map Speed Limit Control"},
    "MapdSpeedLimitOffsetPercent": {"type": "int", "label": "Speed Limit Offset %"},
}


async def handle_params_get(request: web.Request) -> web.Response:
    """GET /v1/params — read all BMW params from /data/params/d/"""
    result = {}
    for key, meta in BMW_PARAMS.items():
        path = f"{PARAMS_DIR}/{key}"
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            if meta["type"] == "bool":
                result[key] = raw == "1"
            elif meta["type"] == "int":
                result[key] = int(raw) if raw else 0
            else:
                result[key] = raw
        except FileNotFoundError:
            result[key] = False if meta["type"] == "bool" else 0
    return web.json_response(result)


async def handle_params_set(request: web.Request) -> web.Response:
    """POST /v1/params — set a single param {key, value}"""
    body = await request.json()
    key = body.get("key")
    value = body.get("value")
    if key not in BMW_PARAMS:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"Unknown param: {key}"}))
    meta = BMW_PARAMS[key]
    if meta["type"] == "bool":
        raw = "1" if value else "0"
    else:
        raw = str(int(value))
    with open(f"{PARAMS_DIR}/{key}", "w") as f:
        f.write(raw)
    return web.json_response({"status": "ok", "key": key, "value": value})


async def handle_tile_list(request: web.Request) -> web.Response:
    """GET /v1/mapd/tiles — list downloaded tiles with storage info."""
    loop = asyncio.get_event_loop()
    tiles = await loop.run_in_executor(None, tile_manager.get_downloaded_tiles)
    storage = await loop.run_in_executor(None, tile_manager.get_storage_info)
    return web.json_response({"tiles": tiles, "storage": storage})


async def handle_tile_download(request: web.Request) -> web.Response:
    """POST /v1/mapd/tiles/download — start downloading tiles in background."""
    global _tile_download_thread

    progress = tile_manager.get_progress()
    if progress["active"]:
        return web.json_response({"error": "Download already in progress"}, status=409)

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON body"}))

    tiles = body.get("tiles", [])
    if not tiles:
        raise web.HTTPBadRequest(text=json.dumps({"error": "No tiles specified"}))

    # Validate tile coordinates
    for t in tiles:
        if "lat" not in t or "lon" not in t:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Each tile must have lat and lon"}))

    import threading
    _tile_download_thread = threading.Thread(
        target=tile_manager.download_tiles,
        args=(tiles,),
        daemon=True,
    )
    _tile_download_thread.start()

    return web.json_response({"status": "started", "total": len(tiles)})


async def handle_tile_progress(request: web.Request) -> web.Response:
    """GET /v1/mapd/tiles/progress — poll download progress."""
    return web.json_response(tile_manager.get_progress())


async def handle_tile_cancel(request: web.Request) -> web.Response:
    """POST /v1/mapd/tiles/cancel — cancel active download."""
    tile_manager.cancel_download()
    return web.json_response({"status": "cancelling"})


async def handle_tile_delete(request: web.Request) -> web.Response:
    """DELETE /v1/mapd/tiles/{lat}/{lon} — delete a downloaded tile."""
    try:
        lat = int(request.match_info["lat"])
        lon = int(request.match_info["lon"])
    except (ValueError, KeyError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid lat/lon"}))

    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, tile_manager.delete_tile, lat, lon)
    if not deleted:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Tile {lat},{lon} not found"}))

    return web.json_response({"status": "deleted", "lat": lat, "lon": lon})


# ─── SPA serving ──────────────────────────────────────────────────────

# ─── Model management ─────────────────────────────────────────────────

MODELS_BASE = Path("/data/models")
SWAPPER_SCRIPT = Path(__file__).parent / "model_swapper.py"
DOWNLOAD_SCRIPT = Path(__file__).parent / "download_openpilot_models.py"

_model_download_task = None  # track background download {proc, model_id, type, status}


def _read_model_info(model_dir: Path) -> dict | None:
    """Read model_info.json from a model directory."""
    info_file = model_dir / "model_info.json"
    if info_file.exists():
        try:
            return json.loads(info_file.read_text())
        except Exception:
            return None
    return None


def _list_installed_models(model_type: str) -> list[dict]:
    """List installed models of given type by reading filesystem directly."""
    type_dir = MODELS_BASE / model_type
    if not type_dir.is_dir():
        return []

    models = []
    for d in sorted(type_dir.iterdir()):
        if not d.is_dir():
            continue
        info = _read_model_info(d)
        name = info.get("name", d.name) if info else d.name
        date = info.get("date", "") if info else ""

        # Check for ONNX and PKL files
        onnx_files = list(d.glob("*.onnx"))
        pkl_files = list(d.glob("*.pkl"))

        models.append({
            "id": d.name,
            "name": name,
            "date": date,
            "has_onnx": len(onnx_files) > 0,
            "has_pkl": len(pkl_files) > 0,
            "onnx_count": len(onnx_files),
            "pkl_count": len(pkl_files),
        })

    return models


async def handle_models_list(request: web.Request) -> web.Response:
    """GET /v1/models — list installed models and active model IDs."""
    loop = asyncio.get_event_loop()
    driving = await loop.run_in_executor(None, _list_installed_models, "driving")
    dm = await loop.run_in_executor(None, _list_installed_models, "dm")

    # Read active model IDs
    active_driving = ""
    active_dm = ""
    try:
        active_driving = (MODELS_BASE / "active_driving_model").read_text().strip()
    except Exception:
        pass
    try:
        active_dm = (MODELS_BASE / "active_dm_model").read_text().strip()
    except Exception:
        pass

    # Include download task status if active
    download_status = None
    global _model_download_task
    if _model_download_task:
        proc = _model_download_task.get("proc")
        if proc and proc.poll() is None:
            download_status = {
                "model_id": _model_download_task["model_id"],
                "type": _model_download_task["type"],
                "status": "downloading",
            }
        elif proc:
            download_status = {
                "model_id": _model_download_task["model_id"],
                "type": _model_download_task["type"],
                "status": "complete" if proc.returncode == 0 else "error",
            }

    return web.json_response({
        "driving": driving,
        "dm": dm,
        "active_driving": active_driving,
        "active_dm": active_dm,
        "download": download_status,
    })


async def handle_models_swap(request: web.Request) -> web.Response:
    """POST /v1/models/swap — swap active model via model_swapper.py."""
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON"}))

    model_type = body.get("type")
    model_id = body.get("model_id")
    if model_type not in ("driving", "dm") or not model_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Need type (driving|dm) and model_id"}))

    if not SWAPPER_SCRIPT.exists():
        raise web.HTTPServiceUnavailable(text=json.dumps({"error": "model_swapper.py not found"}))

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: subprocess.run(
            [PYTHON_BIN, str(SWAPPER_SCRIPT), "--type", model_type, "swap", model_id],
            capture_output=True, text=True, timeout=60,
        ))
    except subprocess.TimeoutExpired:
        raise web.HTTPGatewayTimeout(text=json.dumps({"error": "Swap timed out"}))

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "Swap failed"
        return web.json_response({"error": error_msg}, status=500)

    # Parse JSON output from swap command
    try:
        swap_result = json.loads(result.stdout)
    except json.JSONDecodeError:
        swap_result = {"output": result.stdout.strip(), "status": "ok"}

    return web.json_response(swap_result)


async def handle_models_check_updates(request: web.Request) -> web.Response:
    """POST /v1/models/check-updates — update registry then check for new models."""
    if not DOWNLOAD_SCRIPT.exists():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "download_openpilot_models.py not found"}))

    loop = asyncio.get_event_loop()

    # Step 1: update registry from GitHub
    try:
        await loop.run_in_executor(None, lambda: subprocess.run(
            [PYTHON_BIN, str(DOWNLOAD_SCRIPT), "update-registry"],
            capture_output=True, text=True, timeout=120,
        ))
    except subprocess.TimeoutExpired:
        logger.warning("Registry update timed out, continuing with existing registry")

    # Step 2: check for available (not installed) models
    try:
        result = await loop.run_in_executor(None, lambda: subprocess.run(
            [PYTHON_BIN, str(DOWNLOAD_SCRIPT), "check-updates"],
            capture_output=True, text=True, timeout=30,
        ))
    except subprocess.TimeoutExpired:
        raise web.HTTPGatewayTimeout(text=json.dumps({"error": "Check updates timed out"}))

    if result.returncode != 0:
        return web.json_response({"error": result.stderr.strip() or "Check failed"}, status=500)

    try:
        updates = json.loads(result.stdout)
    except json.JSONDecodeError:
        updates = {"driving": [], "dm": [], "total": 0}

    return web.json_response(updates)


async def handle_models_download(request: web.Request) -> web.Response:
    """POST /v1/models/download — start downloading a model in background."""
    global _model_download_task

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON"}))

    model_type = body.get("type")
    model_id = body.get("model_id")
    if model_type not in ("driving", "dm") or not model_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Need type (driving|dm) and model_id"}))

    if not DOWNLOAD_SCRIPT.exists():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "download_openpilot_models.py not found"}))

    # Check if already downloading
    if _model_download_task:
        proc = _model_download_task.get("proc")
        if proc and proc.poll() is None:
            return web.json_response({
                "error": f"Already downloading {_model_download_task['model_id']}",
            }, status=409)

    # Launch download as background subprocess
    proc = subprocess.Popen(
        [PYTHON_BIN, str(DOWNLOAD_SCRIPT), "download", "--type", model_type, model_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    _model_download_task = {
        "proc": proc,
        "model_id": model_id,
        "type": model_type,
    }

    return web.json_response({"status": "downloading", "model_id": model_id, "type": model_type})


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
