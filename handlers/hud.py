import asyncio
import json
import logging
import os
import subprocess
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import web

from handler_helpers import error_response, get_route_or_404, resolve_route_name
from hud_stream import HLS_DIR, HudStreamManager
from rlog_parser import extract_hud_snapshots

logger = logging.getLogger("connect")

# ─── HUD replay cache ────────────────────────────────────────────────

_HUD_SNAPSHOT_CACHE_MAX = 3  # max segments cached in memory
_hud_snapshot_cache: OrderedDict = OrderedDict()  # (fullname, seg_int) -> snapshot list
_hud_renderer = None  # lazy-initialized HudRenderer singleton

# ─── HUD video rendering state ───────────────────────────────────────
# fullname -> {proc, status_file, output, start, end}
_hud_prerender_tasks: dict = {}

HUD_CACHE_DIR = Path("/data/connect_on_device/hud_cache")
RENDER_SCRIPT = Path(__file__).parent.parent / "render_clip.py"
RENDER_SCRIPT_DRM = Path(__file__).parent.parent / "render_clip_drm.py"
PYTHON_BIN = "/usr/local/venv/bin/python"
OPENPILOT_DIR = Path("/data/openpilot")
REPLAY_BIN = OPENPILOT_DIR / "tools/replay/replay"
DRM_RAYLIB_PATH = Path("/data/pip_packages/raylib")


DRM_RAYLIB_PATH_STR = "/data/pip_packages"


def _is_ui_running() -> bool:
    """Check if the production openpilot UI process is running."""
    result = subprocess.run(["pgrep", "-f", "selfdrive.ui.ui"],
                            capture_output=True)
    return result.returncode == 0


def _start_production_ui():
    """Start the production openpilot UI process."""
    ui_env = os.environ.copy()
    ui_env["PYTHONPATH"] = f"{OPENPILOT_DIR}:{DRM_RAYLIB_PATH_STR}"
    ui_env["PATH"] = "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin"
    ui_env["HOME"] = os.environ.get("HOME", "/root")
    try:
        subprocess.Popen(
            [str(PYTHON_BIN), "-m", "selfdrive.ui.ui"],
            cwd=str(OPENPILOT_DIR),
            env=ui_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Production UI started")
    except Exception as e:
        logger.error("Failed to start production UI: %s", e)


async def _ensure_production_ui(max_retries: int = 3, delay: float = 2.0):
    """Verify production UI is running after HUD cleanup; retry if not.

    Called after cancel/stop to guarantee the C3 display is restored.
    """
    loop = asyncio.get_event_loop()
    for attempt in range(max_retries):
        await asyncio.sleep(delay)
        running = await loop.run_in_executor(None, _is_ui_running)
        if running:
            logger.info("Production UI verified running (attempt %d)", attempt + 1)
            return
        logger.warning("Production UI not running (attempt %d/%d), restarting...",
                        attempt + 1, max_retries)
        await loop.run_in_executor(None, _start_production_ui)
    # Final check
    await asyncio.sleep(delay)
    running = await loop.run_in_executor(None, _is_ui_running)
    if running:
        logger.info("Production UI restored after retries")
    else:
        logger.error("Failed to restore production UI after %d retries", max_retries)


async def _verify_replay_binary():
    """Test-run replay binary to verify it's functional.

    Returns True if healthy, False if broken (crash, missing libs, ABI mismatch).
    Checks that the binary can run without crashing — a normal exit (code >= 0)
    is OK; only signal kills (code < 0, e.g. SIGABRT=-6) indicate breakage.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            str(REPLAY_BIN), "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode >= 0:
            return True
        # Negative return code = killed by signal (e.g. -6 = SIGABRT)
        logger.warning("Replay binary crashed (signal %d): %s",
                       -proc.returncode, (stdout.decode()[-200:] if stdout else ""))
        return False
    except asyncio.TimeoutError:
        logger.warning("Replay binary health check timed out")
        return False
    except Exception as e:
        logger.warning("Replay binary health check error: %s", e)
        return False


async def _rebuild_replay_binary():
    """Rebuild replay binary from scons cache.

    Returns True on success, False on failure.
    """
    logger.info("Rebuilding replay binary from scons cache...")
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
        logger.error("Failed to rebuild replay binary: %s",
                     stdout.decode()[-500:] if stdout else "no output")
        return False


async def _ensure_replay_binary():
    """Check replay binary exists and is functional; rebuild if needed.

    Returns True if binary is available, False on build failure.
    """
    if not REPLAY_BIN.is_file():
        logger.info("Replay binary missing, rebuilding...")
        return await _rebuild_replay_binary()

    if not await _verify_replay_binary():
        logger.info("Replay binary broken, rebuilding...")
        return await _rebuild_replay_binary()

    return True

# Quality presets for HUD video rendering (Weston/screenshooter mode)
# speed: replay -x flag (lower = more unique frames per second of route time)
# scale: ffmpeg scale filter (None = native 2160x1080)
# fps: output video framerate
# bitrate_mbps: estimated output bitrate for size calculation
QUALITY_PRESETS = {
    "high":   {"speed": 0.2, "scale": None,       "fps": 20, "bitrate_mbps": 3.0},
    "medium": {"speed": 0.2, "scale": "1080:540", "fps": 20, "bitrate_mbps": 1.5},
    "low":    {"speed": 0.5, "scale": "1080:540", "fps": 10, "bitrate_mbps": 0.8},
}

# Quality presets for DRM mode — 0.2x replay, 2fps capture → 10 unique frames/route-sec
# Post-processed: 5x speedup + frame-dup to target fps. wall_duration ≈ 5x duration
QUALITY_PRESETS_DRM = {
    "high":   {"fps": 20, "bitrate_mbps": 3.0},
    "medium": {"fps": 20, "bitrate_mbps": 1.5},
    "low":    {"fps": 10, "bitrate_mbps": 0.8},
}


def _is_drm_available() -> bool:
    """Check if DRM backend is available for recording.

    DRM mode requires:
    1. DRM raylib package installed (/data/pip_packages/raylib)
    2. The render_clip_drm.py script exists
    3. The raylib UI script exists (selfdrive/ui/ui.py)
    """
    if not DRM_RAYLIB_PATH.is_dir():
        return False
    if not RENDER_SCRIPT_DRM.exists():
        return False
    if not (OPENPILOT_DIR / "selfdrive/ui/ui.py").is_file():
        return False
    return True


def _find_closest_snapshot(snapshots: list, t_ms: int):
    """Binary search for the snapshot closest to t_ms."""
    from bisect import bisect_left
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
    import sys

    route_name, route, store = get_route_or_404(request)

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

    # Detect DRM mode availability
    use_drm = _is_drm_available()

    # Accept explicit speed/scale/fps, or fall back to quality preset
    quality = body.get("quality")

    if use_drm:
        # DRM mode: 0.2x replay, 2fps capture, 10 unique frames/route-sec, no scale/speed params
        if quality and quality in QUALITY_PRESETS_DRM:
            preset = QUALITY_PRESETS_DRM[quality]
        else:
            preset = QUALITY_PRESETS_DRM["high"]
        render_fps = preset["fps"]
        render_bitrate = preset["bitrate_mbps"]
        render_speed = 0.2  # DRM renders at 0.2x, sped up in post
        render_scale = None
    elif quality and quality in QUALITY_PRESETS:
        preset = QUALITY_PRESETS[quality]
        render_speed = preset["speed"]
        render_scale = preset["scale"]
        render_fps = preset["fps"]
        render_bitrate = preset["bitrate_mbps"]
    else:
        # Weston fallback: speed is fixed at 0.2 (set in render_clip.py)
        render_speed = 0.2
        render_scale = body.get("scale")  # None or "1080:540"
        render_fps = int(body.get("fps", 20))
        base_bitrate = 3.0
        render_bitrate = base_bitrate * (0.5 if render_scale else 1.0)

    estimated_mb = round(duration * render_bitrate / 8)
    # DRM: 0.2x replay (5x wall-time) + 15s setup; Weston: 0.2x speed + 30s setup
    if use_drm:
        wall_duration = round(duration / 0.2 + 15)
    else:
        wall_duration = round(duration / render_speed + 30)

    # Build a cache key from the actual render params
    if use_drm:
        cache_tag = f"drm_f{render_fps}"
    else:
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

    if use_drm:
        # DRM mode: use render_clip_drm.py with simpler args
        script = str(RENDER_SCRIPT_DRM)
        # Extract route metadata for MP4 embedding
        route_date = ""
        parts = fullname.split("/")
        if len(parts) == 2:
            route_date = parts[1]  # e.g. "2026-02-23--12-38-40"
        op_version = route.get("version", "")
        op_branch = route.get("git_branch", "")
        op_commit = route.get("git_commit", "")

        cmd = [
            python_bin, script,
            "--route-name", fullname.replace("/", "|"),
            "--local-id", local_id,
            "--dongle-id", dongle_id,
            "--data-dir", str(store.data_dir),
            "--start", str(start_sec),
            "--end", str(end_sec),
            "--fps", str(render_fps),
            "--route-date", route_date,
            "--op-version", op_version,
            "--op-branch", op_branch,
            "--op-commit", op_commit,
            "--output", output,
            "--status-file", status_file,
        ]
        logger.info("Launching HUD render (DRM): %s (%.0fs-%.0fs, fps=%d)",
                    fullname, start_sec, end_sec, render_fps)
    else:
        # Weston mode: use render_clip.py with speed/scale params
        script = str(RENDER_SCRIPT)
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
        logger.info("Launching HUD render (Weston): %s (%.0fs-%.0fs, speed=%.2f, fps=%d, scale=%s)",
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
        "mode": "drm" if use_drm else "weston",
    }

    return web.json_response({
        "status": "rendering",
        "elapsed_sec": 0,
        "total_sec": duration,
        "estimated_mb": estimated_mb,
        "wall_duration": wall_duration,
        "mode": "drm" if use_drm else "weston",
    })


async def handle_hud_progress(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/hud/progress — check HUD video render progress."""
    route_name = resolve_route_name(request)
    store = request.app["store"]

    # Resolve to fullname (tasks are keyed by fullname)
    route = store.get_route(route_name)
    fullname = route["fullname"] if route else route_name
    task = _hud_prerender_tasks.get(fullname)

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
    route_name = resolve_route_name(request)
    store = request.app["store"]
    route = store.get_route(route_name)
    fullname = route["fullname"] if route else route_name
    task = _hud_prerender_tasks.get(fullname)
    if not task:
        # No active task — still verify UI is running (may have been orphaned)
        asyncio.create_task(_ensure_production_ui(max_retries=1, delay=1.0))
        return web.json_response({"status": "idle"})

    was_drm = task.get("mode") == "drm"
    proc = task.get("proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
        # Wait for process to exit so its finally block can run
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("HUD render cancelled for %s", fullname)

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

    del _hud_prerender_tasks[fullname]

    # Verify production UI is restored (non-blocking — runs in background)
    if was_drm:
        asyncio.create_task(_ensure_production_ui())

    return web.json_response({"status": "cancelled"})


async def handle_hud_video(request: web.Request) -> web.Response:
    """GET /v1/route/{routeName}/hud/video — serve the rendered HUD MP4."""
    route_name = resolve_route_name(request)
    store = request.app["store"]
    route = store.get_route(route_name)
    fullname = route["fullname"] if route else route_name

    task = _hud_prerender_tasks.get(fullname)
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
        # Verify production UI is restored after stream cleanup
        asyncio.create_task(_ensure_production_ui())
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
