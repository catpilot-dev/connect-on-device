"""Dashcam clip handlers: create, list, serve, delete.

HTTP concerns only; validation, storage and encoding live in clips.py.
`clips` is imported as a module and referenced at call time because clips.py
itself imports the handlers package (a `from clips import ...` here would be
an import cycle).
"""
import asyncio
import logging
import math
import os
import time
from urllib.parse import quote

from aiohttp import web

import clips
from handler_helpers import get_route_or_404, parse_json

logger = logging.getLogger("connect")


def _public(clip: dict) -> dict:
    """Clip as the browser sees it: no device paths, plus an encode-time estimate."""
    out = {k: v for k, v in clip.items() if k != "inputs"}
    duration = clip.get("duration_sec") or 0
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
        duration = 0  # a damaged sidecar must not turn every listing into a 500
    out["eta_sec"] = duration / clips.ENCODE_RATE_SEC_PER_SEC
    return out


def _content_disposition(name: str) -> str:
    """attachment with an ASCII fallback filename plus the exact UTF-8 name (RFC 6266).

    name has passed validate_filename, so it holds no quote, backslash or
    control character that could break out of the quoted fallback.
    """
    fallback = "".join(ch if ch.isascii() else "_" for ch in name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _supported(request: web.Request) -> bool:
    return request.app["clips_encoder"] != "none"


def _bad_filename(request: web.Request):
    """Return (name, None) for a valid filename, else (None, 400 response)."""
    try:
        return clips.validate_filename(request.match_info["filename"]), None
    except ValueError:
        return None, web.json_response({"error": "invalid filename"}, status=400)


def _preserve_segments(sidecars: list[dict]) -> None:
    """Mark every segment dir feeding these clips so cleanup keeps the footage."""
    seg_dirs = {os.path.dirname(p) for c in sidecars for p in c.get("inputs", [])}
    for seg_dir in seg_dirs:
        try:
            os.setxattr(seg_dir, "user.preserve", b"1")
        except OSError:
            pass


async def handle_clips_create(request: web.Request) -> web.Response:
    """POST /v1/route/{routeName}/clips — queue one clip per requested camera."""
    if not _supported(request):
        return web.json_response({"error": "Clip encoder not available on this device"}, status=503)
    route_name, route, store = get_route_or_404(request)
    body = await parse_json(request)
    clip_store = request.app["clip_store"]
    loop = asyncio.get_running_loop()
    try:
        requests = await loop.run_in_executor(
            store._executor, clips.build_requests, route, store, body, clip_store, time.time())
    except clips.ClipRequestError as e:
        return web.json_response({"error": str(e)}, status=e.status)

    # build_requests chose each name in the executor; a concurrent POST may
    # have claimed it since. Re-check on the loop with no await before the
    # write, so two requests can never write the same sidecar.
    for sidecar in requests:
        name = sidecar["filename"]
        if clip_store.sidecar_path(name).exists() or clip_store.mp4_path(name).exists():
            sidecar["filename"] = clip_store.unique_filename(name[:-len(".mp4")])
        clip_store.write(sidecar)
    store.preserve_route(route["_local_id"])
    _preserve_segments(requests)
    return web.json_response(
        {"clips": [_public(c) for c in requests],
         "clamped": any(r["clamped"] for r in requests)},
        status=202)


async def handle_clips_for_route(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/clips — this route's clips and available footage."""
    route_name, route, store = get_route_or_404(request)
    mine = [_public(c) for c in request.app["clip_store"].list()
            if c.get("local_id") == route["_local_id"]]
    return web.json_response({
        "clips": mine,
        "cameras": clips.available_ranges(route),
        "supported": _supported(request),
        "free_bytes": clips._free_bytes(store),
    })


async def handle_clips_list(request: web.Request) -> web.Response:
    """GET /v1/clips — all clips, newest first."""
    clip_store = request.app["clip_store"]
    return web.json_response({
        "clips": [_public(c) for c in clip_store.list()],
        "total_bytes": clip_store.total_bytes(),
        "supported": _supported(request),
        "free_bytes": clips._free_bytes(request.app["store"]),
    })


async def handle_clip_serve(request: web.Request) -> web.StreamResponse:
    """GET /v1/clips/{filename} — download a finished clip (Range supported)."""
    name, err = _bad_filename(request)
    if err:
        return err
    clip_store = request.app["clip_store"]
    clip = clip_store.read(name)
    path = clip_store.mp4_path(name)
    if not clip or clip.get("status") != clips.STATUS_READY or not path.is_file():
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(path, headers={
        "Content-Type": "video/mp4",
        "Content-Disposition": _content_disposition(name),
    })


async def handle_clip_delete(request: web.Request) -> web.Response:
    """DELETE /v1/clips/{filename} — remove a clip.

    Runs synchronously on the event loop on purpose: the worker relies on
    nothing awaiting between its deleted-check and its ready-write.
    """
    name, err = _bad_filename(request)
    if err:
        return err
    if not request.app["clip_store"].delete(name):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"status": "ok", "filename": name})
