import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

from aiohttp import web

from handler_helpers import get_route_or_404
from handlers.hud import _handle_hud_frame
from rlog_parser import _generate_coords_json, _generate_events_json
from route_helpers import _base_url, _clean_route, _resolve_local_id, _route_bookmarks, _route_engagement, _set_route_url
from route_store import _route_counter
from storage_management import DOWNLOAD_FILES, build_download_tar

logger = logging.getLogger("connect")


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
    route_name, route, store = get_route_or_404(request)

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

    route_name, route, store = get_route_or_404(request)

    r_with_url = _set_route_url(route, request)
    cleaned = _clean_route(r_with_url)
    cleaned["is_preserved"] = store.is_preserved(local_id)
    cleaned["cleared_files"] = cleared
    return web.json_response(cleaned)


async def handle_route_files(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/files — list available files per segment"""
    route_name, route, store = get_route_or_404(request)

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
    route_name, route, store = get_route_or_404(request)

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
