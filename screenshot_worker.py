"""Background HUD-screenshot extraction for onroad bookmark taps.

The screen_capture plugin no longer saves a PNG while driving — a tap only
publishes bookmarkButton (→ userBookmark in the logs), so the UI process never
does a GPU readback or disk write onroad. This worker produces the screenshot
afterwards: it scans the latest route's qlogs for userBookmark events
(decimation 1, so every tap is present in the small qlog) and renders the
exact HUD frame offline via render_clip_headless.py --screenshot-at. Only the
newest route is ever scanned — captures belong to the current drive; there is
no retroactive backfill of older routes.

Safety: the worker runs only while the device is offroad, re-checks IsOnroad
before every job, and yields to user-triggered HUD prerenders. It never runs
while the car is driving.

Output PNGs are named capture_YYYYMMDD_HHMMSS.png from the tap's exact epoch,
converted mono→wall against the log's own GPS fixes. That epoch is recorded
alongside the filename so bookmark rows and /v1/screenshots/at match on it
directly. A tap with no GPS time anywhere in its drive is not rendered at all
(status "no_gps_time"): the only other clock is the device RTC, which lags the
drive's true start by the GPS-fix wait — 21-45 s measured — so a capture named
from it would be wrong data and would never match its own bookmark row.

State lives in route metadata under "hud_capture_state":
    {"scanned_segs": [0, 1, ...],
     "bookmarks": {"<offset_ms>":
         {"status": "pending|done|failed|no_gps_time", "attempts": n}}}

Routes are skipped until enrich_new_routes() has run on them (first route-list
view): start_lng sets the filename's timezone, and create_time orders routes
so "the latest drive" means what it says.
"""
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import COD_HUD_CACHE_DIR, PYTHON_BIN
from handlers.middleware import is_onroad
from handlers.screenshots import SCREENSHOTS_DIR, _parse_capture_epoch
from log_parser import _extract_bookmark_epochs, _gps_anchor
from route_store import RouteStore

logger = logging.getLogger("connect")

RENDER_SCRIPT = Path(__file__).parent / "render_clip_headless.py"

CYCLE_SEC = 300          # scan interval
SCAN_BUDGET = 40         # qlog parses per cycle — safety cap for very long routes
MAX_ATTEMPTS = 3         # renders per bookmark before giving up
RENDER_TIMEOUT = 300     # seconds per single-frame render
MATCH_TOLERANCE = 2.0    # same window handle_screenshot_by_time uses
MIN_REAL_EPOCH = 1_500_000_000  # create_time below this is a counter, not a clock


def _capture_filename(epoch: float, tz_off_hours: int | None) -> str:
    """Build the capture PNG name from the exact tap epoch.

    The timestamp is the drive location's local wall time (route GPS
    longitude → round(lng/15), same convention as route dates) — the C3
    system clock runs UTC, so device-local formatting would force the user
    to translate timezones by hand. The name is purely for humans; the
    absolute epoch lives in the route's hud_capture_state, which the
    screenshot handlers consult for exact matching. Without a longitude,
    fall back to device-local time (same as the plugin's offroad captures).
    """
    if tz_off_hours is not None:
        tz = timezone(timedelta(hours=tz_off_hours))
        ts = datetime.fromtimestamp(epoch, tz).strftime("%Y%m%d_%H%M%S")
    else:
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(epoch))
    return f"capture_{ts}.png"


def _work_signature(store) -> tuple | None:
    """Cheap fingerprint of possible work: the newest eligible route + its size.

    While this is unchanged after a cycle that produced nothing, there is
    nothing new to scan or render — the worker skips cycles entirely until a
    new drive appears.
    """
    best = None
    for route in store._routes.values():
        ct = route.get("create_time") or 0
        if ct < MIN_REAL_EPOCH:
            continue
        if best is None or ct > best[0]:
            best = (ct, route.get("_local_id"), len(route.get("_segments", [])))
    return best


def _user_render_busy() -> bool:
    """True while a user-triggered HUD prerender subprocess is running."""
    from handlers.hud import _hud_prerender_tasks
    return any(t.get("proc") and t["proc"].returncode is None
               for t in _hud_prerender_tasks.values())


def _existing_captures() -> list[tuple[float, str]]:
    """(epoch, filename) for every capture already on disk."""
    out = []
    if os.path.isdir(SCREENSHOTS_DIR):
        for name in os.listdir(SCREENSHOTS_DIR):
            if name.lower().endswith(".png"):
                e = _parse_capture_epoch(name)
                if e is not None:
                    out.append((e, name))
    return out


def _find_capture(captures: list[tuple[float, str]], target: float) -> str | None:
    """Filename of an existing capture within the match window, or None."""
    for epoch, name in captures:
        if abs(epoch - target) <= MATCH_TOLERANCE:
            return name
    return None


def _sync_epochs_to_meta(meta: dict, bms: dict) -> bool:
    """Copy exact tap epochs onto the route's visible bookmarks.

    The frontend matches a bookmark row to its capture PNG by epoch; a row
    without one skips the lookup entirely and exports a plain frame instead,
    since no other clock in the log dates the tap correctly. Bookmarks are
    matched by offset (drive bookmarks are imported as round(ms/1000, 1)).
    """
    changed = False
    for bm in meta.get("bookmarks") or []:
        if not isinstance(bm, dict) or "epoch" in bm:
            continue
        for ms_key, entry in bms.items():
            epoch = entry.get("epoch")
            if epoch is not None and abs(bm.get("time_sec", -1) - int(ms_key) / 1000.0) <= 0.5:
                bm["epoch"] = epoch
                changed = True
                break
    return changed


def _collect_pending(store) -> tuple[list[dict], bool]:
    """Scan the latest route's qlogs for tap events; return renderable jobs.

    Only the newest route is scanned for new userBookmark events — screen
    captures are taken on the current drive, not discovered retroactively.
    Older routes are still walked for previously found bookmarks (retries,
    done-marking), but their qlogs are never parsed.

    Mutates route metadata (scanned_segs, done marks) — caller saves when
    the returned changed flag is set. Aborts early if the device goes onroad.
    """
    jobs = []
    changed = False
    scan_budget = SCAN_BUDGET
    captures = _existing_captures()

    routes = sorted(store._routes.values(),
                    key=lambda r: r.get("create_time", 0), reverse=True)
    latest_scanned = False
    for route in routes:
        if is_onroad():
            break
        lid = route.get("_local_id")
        if not lid or lid in store._hidden:
            continue
        create_time = route.get("create_time") or 0
        # Wait for enrichment: without start_lng the PNG would be named in
        # device time (UTC) — which collides, to the second, with the old
        # plugin's live-capture names — and without create_time the route
        # sort above cannot tell which drive is the latest. Both fields
        # appear together on the first route-list view.
        if create_time < MIN_REAL_EPOCH or route.get("start_lng") is None:
            continue
        meta = store._metadata.get(lid)
        if meta is None:
            continue

        state = meta.get("hud_capture_state")
        if state is None:
            if latest_scanned:
                continue  # older route never scanned — nothing to render either
            state = {"scanned_segs": [], "bookmarks": {}}
        scanned = set(state.get("scanned_segs", []))
        bms = state.setdefault("bookmarks", {})

        route_scanned = False
        if not latest_scanned:
            latest_scanned = True
            segs = route.get("_segments", [])
            # One GPS anchor for the whole route. logMonoTime is boot-relative
            # and continuous across segments, so a single fix dates every tap
            # exactly — including taps in a segment that never got a fix, and
            # taps before the drive's first fix. The first fix normally lands
            # in segment 0; three segments is a generous cap.
            anchor = None
            if any(s.get("number") not in scanned for s in segs):
                for seg in segs[:3]:
                    qlog = RouteStore._find_qlog(seg["path"])
                    if qlog:
                        anchor = _gps_anchor(qlog)
                        if anchor:
                            break
            for seg in segs:
                num = seg.get("number")
                if num is None or num in scanned or scan_budget <= 0:
                    continue
                qlog = RouteStore._find_qlog(seg["path"])
                if qlog:
                    scan_budget -= 1
                    for ms, epoch in _extract_bookmark_epochs(qlog, num, anchor):
                        entry = bms.setdefault(str(ms), {"status": "pending", "attempts": 0})
                        if epoch is not None and "epoch" not in entry:
                            # Exact tap wall-time — names the PNG so the user
                            # can identify the moment; create_time + offset is
                            # only a fallback (it carries the GPS-fix lag).
                            entry["epoch"] = round(epoch, 3)
                scanned.add(num)
                route_scanned = True

        if route_scanned:
            state["scanned_segs"] = sorted(scanned)
            meta["hud_capture_state"] = state
            changed = True

        changed |= _sync_epochs_to_meta(meta, bms)

        for ms_key, entry in bms.items():
            if entry.get("status") != "pending":
                continue
            epoch = entry.get("epoch")
            if epoch is None:
                # No GPS fix anywhere in the drive. The device RTC is the only
                # clock left and it dates the route from its first fix, not
                # its start — 21-45 s late on real drives. A PNG named from
                # that is wrong data that would never match its bookmark row,
                # so don't produce one.
                entry["status"] = "no_gps_time"
                changed = True
                logger.warning("Capture at %sms in %s has no GPS time — skipped",
                               ms_key, lid)
                continue
            filename = _capture_filename(epoch, round(route["start_lng"] / 15))
            # Already captured: exact filename from a previous run, or another
            # capture within the match window. Record the file that actually
            # exists — never the computed name it might have had.
            existing = filename if os.path.isfile(os.path.join(SCREENSHOTS_DIR, filename)) \
                else _find_capture(captures, epoch)
            if existing:
                entry["status"] = "done"
                entry["file"] = existing
                changed = True
                continue
            if entry.get("attempts", 0) >= MAX_ATTEMPTS:
                entry["status"] = "failed"
                changed = True
                continue
            jobs.append({
                "lid": lid,
                "dongle_id": route.get("dongle_id", ""),
                "fullname": route.get("fullname", ""),
                "offset_ms": int(ms_key),
                "epoch": epoch,
                "filename": filename,
            })

    return jobs, changed


async def _render_one(store, job: dict) -> tuple[bool, str | None]:
    """Render one HUD frame to a capture PNG. Returns (ok, error)."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    output = os.path.join(SCREENSHOTS_DIR, job["filename"])

    cache_dir = Path(COD_HUD_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    status_file = str(cache_dir / f"{job['lid']}_shot_{job['offset_ms']}.status.json")

    python_bin = PYTHON_BIN if os.path.isfile(PYTHON_BIN) else sys.executable
    cmd = [
        python_bin, str(RENDER_SCRIPT),
        "--route-name", job["fullname"].replace("/", "|"),
        "--local-id", job["lid"],
        "--dongle-id", job["dongle_id"],
        "--data-dir", str(store.data_dir),
        "--screenshot-at", f"{job['offset_ms'] / 1000.0:.2f}",
        "--output", output,
        "--status-file", status_file,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, "render timeout"
    except asyncio.CancelledError:
        proc.kill()
        raise

    if os.path.isfile(output) and os.path.getsize(output) > 1000:
        return True, None
    err = stderr.decode(errors="replace")[-300:] if stderr else f"exit {proc.returncode}"
    return False, err


def _record_result(store, job: dict, ok: bool, err: str | None):
    state = (store._metadata.get(job["lid"]) or {}).get("hud_capture_state")
    entry = state["bookmarks"].get(str(job["offset_ms"])) if state else None
    if entry is None:
        return
    if ok:
        entry["status"] = "done"
        entry["file"] = job.get("filename")
        entry.pop("error", None)
        logger.info("Extracted HUD screenshot: %s @ %.1fs",
                    job["lid"], job["offset_ms"] / 1000.0)
    else:
        entry["attempts"] = entry.get("attempts", 0) + 1
        if err:
            entry["error"] = err[:200]
        if entry["attempts"] >= MAX_ATTEMPTS:
            entry["status"] = "failed"
        logger.warning("HUD screenshot failed (%d/%d): %s @ %.1fs — %s",
                       entry["attempts"], MAX_ATTEMPTS, job["lid"],
                       job["offset_ms"] / 1000.0, err)


async def screenshot_worker(app):
    """Periodic offroad-only extraction loop. Started from server _startup."""
    store = app["store"]
    loop = asyncio.get_event_loop()
    idle_sig = None  # work signature of the last do-nothing cycle
    while True:
        await asyncio.sleep(CYCLE_SEC)
        try:
            if is_onroad() or _user_render_busy():
                continue
            sig = _work_signature(store)
            if sig is not None and sig == idle_sig:
                continue  # nothing new since the last empty cycle
            jobs, changed = await loop.run_in_executor(None, _collect_pending, store)
            for job in jobs:
                # Never render while driving, and let user renders win.
                if is_onroad() or _user_render_busy():
                    break
                ok, err = await _render_one(store, job)
                _record_result(store, job, ok, err)
                changed = True
            if changed:
                await loop.run_in_executor(None, store._save_metadata)
            idle_sig = sig if (not jobs and not changed) else None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Screenshot worker error")
