"""Dashboard telemetry handlers — REST replay + WebSocket live streaming."""

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from handler_helpers import error_response, resolve_route_name
from rlog_parser import extract_dashboard_telemetry

logger = logging.getLogger("connect.dashboard")


async def handle_dashboard_telemetry(request):
    """GET /v1/dashboard/telemetry/{routeName}/{segments}

    Extract dashboard telemetry from rlog files for the given route and segments.
    Segments can be a single number "3", a range "0-5", or comma-separated "0,1,3".
    Returns JSON array of telemetry samples at ~5Hz.
    """
    route_name = resolve_route_name(request)
    seg_str = request.match_info["segments"]
    store = request.app["store"]

    route = store.get_route(route_name)
    if not route:
        return error_response(f"Route {route_name} not found", 404)

    # Parse segment specification
    seg_nums = []
    for part in seg_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            seg_nums.extend(range(int(lo), int(hi) + 1))
        else:
            seg_nums.append(int(part))

    # Resolve local_id for filesystem paths (dirs are {local_id}--{seg})
    local_id = store.get_local_id(route_name)
    if not local_id:
        return error_response(f"Cannot resolve local_id for {route_name}", 404)

    # Find log paths for requested segments (prefer qlog for speed)
    data_dir = store.data_dir
    seg_log_pairs = []  # [(seg_num, path), ...]
    for seg in sorted(seg_nums):
        seg_dir = data_dir / f"{local_id}--{seg}"
        for name in ("qlog.zst", "qlog", "rlog.zst", "rlog"):
            p = seg_dir / name
            if p.is_file():
                seg_log_pairs.append((seg, str(p)))
                break

    if not seg_log_pairs:
        return error_response("No log files found for requested segments", 404)

    # Run extraction in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    samples = await loop.run_in_executor(None, extract_dashboard_telemetry, seg_log_pairs)

    return web.json_response(samples)


async def handle_dashboard_ws(request):
    """GET /ws/dashboard — WebSocket live telemetry at 5Hz.

    Subscribes to cereal SubMaster on C3 and streams dashboard telemetry
    as JSON messages. Falls back gracefully if cereal is unavailable.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logger.info("Dashboard WebSocket connected")

    try:
        import cereal.messaging as messaging

        sm = messaging.SubMaster([
            "carState", "carControl", "selfdriveState", "peripheralState",
        ])

        while not ws.closed:
            sm.update(200)  # 200ms timeout

            if not sm.updated["carState"]:
                await asyncio.sleep(0.05)
                continue

            cs = sm["carState"]
            cc = sm["carControl"]
            sd = sm["selfdriveState"]
            ps = sm["peripheralState"]

            msg = {
                "t": round(sm.logMonoTime["carState"] / 1e9, 3),
                "coolantTemp": round(float(getattr(cs, "coolantTemp", 0)), 1),
                "oilTemp": round(float(getattr(cs, "oilTemp", 0)), 1),
                "vEgo": round(float(cs.vEgo), 3),
                "steeringAngleDeg": round(float(cs.steeringAngleDeg), 2),
                "gasPressed": bool(cs.gasPressed),
                "brakePressed": bool(cs.brakePressed),
                "cruiseSpeed": round(float(cs.cruiseState.speed), 2),
                "cruiseEnabled": bool(cs.cruiseState.enabled),
                "steerCmd": round(float(cc.actuators.steer), 4),
                "accelCmd": round(float(cc.actuators.accel), 4),
                "sdState": str(sd.state),
                "sdEnabled": bool(sd.enabled),
                "voltage": round(float(ps.voltage), 2),
            }

            await ws.send_str(json.dumps(msg))
            await asyncio.sleep(0.2)  # 5Hz

    except ImportError:
        logger.warning("cereal not available — sending empty telemetry on WebSocket")
        await ws.send_str(json.dumps({"error": "cereal not available (not running on C3)"}))
    except Exception as e:
        if not ws.closed:
            logger.warning("Dashboard WebSocket error: %s", e)
    finally:
        logger.info("Dashboard WebSocket disconnected")

    return ws
