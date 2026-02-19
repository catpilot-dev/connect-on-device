"""HTTP request handlers for Connect on Device.

All aiohttp handler functions and CORS middleware. Implements the comma-compatible
REST API so the asiusai/connect React frontend works unchanged.
"""

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

from aiohttp import web

from bisect import bisect_left
from collections import OrderedDict

from rlog_parser import _generate_coords_json, _generate_events_json, extract_hud_snapshots
from route_helpers import _base_url, _clean_route, _resolve_local_id, _route_engagement, _set_route_url
from route_store import _route_counter
from storage_management import DOWNLOAD_FILES, build_download_tar, get_storage_info

logger = logging.getLogger("connect")

# ─── HUD replay cache ────────────────────────────────────────────────

_HUD_SNAPSHOT_CACHE_MAX = 3  # max segments cached in memory
_hud_snapshot_cache: OrderedDict = OrderedDict()  # (fullname, seg_int) -> snapshot list
_hud_renderer = None  # lazy-initialized HudRenderer singleton


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
    """GET /v1/me/devices/ — list devices"""
    store = request.app["store"]
    store.scan()
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
    if meta and meta.get("start_address") is None and meta.get("gps_coordinates"):
        await loop.run_in_executor(None, store.geocode_route, local_id)
        route = store.get_route(route_name)

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(local_id)
    return web.json_response(cleaned)


async def handle_route_enrich(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/enrich — on-demand enrichment of a single route"""
    store = request.app["store"]
    local_id = _resolve_local_id(store, request)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(store._executor, store.ensure_enriched, local_id)

    route_name = request.match_info["routeName"].replace("|", "/")
    route = store.get_route(route_name)
    if not route:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(local_id)
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


# ─── SPA serving ──────────────────────────────────────────────────────

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
