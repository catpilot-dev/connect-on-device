import json
import logging
import sys

from aiohttp import web

from handler_helpers import PARAMS_DIR, error_response, parse_json, read_param, write_param

logger = logging.getLogger("connect")


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
        return error_response(str(e), 500)


# ── Toggles panel ─────────────────────────────────────────────

TOGGLE_PARAMS = [
    "OpenpilotEnabledToggle", "ExperimentalMode",
    "DisengageOnAccelerator", "IsLdwEnabled", "AlwaysOnDM",
    "RecordFront", "RecordAudio", "IsMetric", "LongitudinalPersonality",
    # Developer toggles
    "AdbEnabled", "SshEnabled",
    "JoystickDebugMode", "LongitudinalManeuverMode",
    "AlphaLongitudinalEnabled",
]

# Mutual exclusion pairs — toggling one ON turns the other OFF
_TOGGLE_MUTEX = {
    "JoystickDebugMode": "LongitudinalManeuverMode",
    "LongitudinalManeuverMode": "JoystickDebugMode",
}

async def handle_toggles_get(request: web.Request) -> web.Response:
    """GET /v1/toggles — read all toggle params."""
    result = {}
    for key in TOGGLE_PARAMS:
        raw = read_param(key)
        if key == "LongitudinalPersonality":
            result[key] = int(raw) if raw else 2  # default: Relaxed
        else:
            result[key] = raw == "1"
    return web.json_response(result)


async def handle_toggles_set(request: web.Request) -> web.Response:
    """POST /v1/toggles — set a toggle param."""
    body = await parse_json(request)
    key = body.get("key", "")
    value = body.get("value")
    if key not in TOGGLE_PARAMS:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"Unknown toggle: {key}"}))
    try:
        if key == "LongitudinalPersonality":
            write_param(key, str(int(value)))
        else:
            write_param(key, "1" if value else "0")
        # Mutual exclusion: if turning one on, turn the other off
        if value and key in _TOGGLE_MUTEX:
            write_param(_TOGGLE_MUTEX[key], "0")
    except Exception as e:
        return error_response(str(e), 500)
    return web.json_response({"status": "ok", "key": key, "value": value})


BMW_PARAMS = {
    "DccCalibrationMode": {"type": "bool", "label": "DCC Calibration Mode"},
    "LaneCenteringCorrection": {"type": "bool", "label": "Lane Centering Correction"},
    "MapdSpeedLimitControlEnabled": {"type": "bool", "label": "Map Speed Limit Control"},
    "MapdSpeedLimitOffsetPercent": {"type": "int", "label": "Speed Limit Offset %"},
    "MapdCurveTargetLatAccel": {"type": "int", "label": "Curve Target Lat Accel"},
    "MapdVersion": {"type": "str", "label": "Mapd Version"},
}

MAPD_PARAM_KEYS = {"MapdSpeedLimitControlEnabled", "MapdSpeedLimitOffsetPercent", "MapdCurveTargetLatAccel"}


def update_mapd_settings():
    """Regenerate MapdSettings JSON from individual params (snake_case keys for mapd Go daemon)."""
    # Master toggle
    enabled = read_param("MapdSpeedLimitControlEnabled") == "1"

    # Offset: raw percentage value (0, 5, 10, 15) -> decimal
    try:
        offset_pct = int(read_param("MapdSpeedLimitOffsetPercent", "10"))
    except ValueError:
        offset_pct = 10  # default +10%
    offset = offset_pct / 100.0

    # Curve comfort: button index -> lat accel value
    try:
        lat_idx = int(read_param("MapdCurveTargetLatAccel", "1"))
    except ValueError:
        lat_idx = 1  # default 2.0
    lat_vals = [1.5, 2.0, 2.5, 3.0]
    lat_accel = lat_vals[lat_idx] if 0 <= lat_idx < 4 else 2.0

    settings = {
        "speed_limit_control_enabled": enabled,
        "map_curve_speed_control_enabled": enabled,
        "vision_curve_speed_control_enabled": enabled,
        "speed_limit_offset": offset,
        "map_curve_target_lat_a": lat_accel,
        "vision_curve_target_lat_a": lat_accel,
    }

    write_param("MapdSettings", json.dumps(settings))


def _read_mapd_settings() -> dict:
    """Read MapdSettings JSON, returning {} on missing/invalid."""
    raw = read_param("MapdSettings")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


# Mapping from individual param keys to MapdSettings JSON fields
_MAPD_SETTINGS_MAP = {
    "MapdSpeedLimitControlEnabled": ("speed_limit_control_enabled", "bool"),
    "MapdSpeedLimitOffsetPercent": ("speed_limit_offset", "offset_pct"),  # decimal → percentage
    "MapdCurveTargetLatAccel": ("map_curve_target_lat_a", "lat_idx"),     # value → button index
}


async def handle_params_get(request: web.Request) -> web.Response:
    """GET /v1/params — read all BMW params from /data/params/d/

    For mapd params, falls back to MapdSettings JSON if individual param
    files don't exist (user may have configured mapd directly).
    """
    mapd_settings = None  # lazy-loaded

    result = {}
    for key, meta in BMW_PARAMS.items():
        raw = read_param(key)

        # Fallback: if individual param missing, read from MapdSettings JSON
        if not raw and key in _MAPD_SETTINGS_MAP:
            if mapd_settings is None:
                mapd_settings = _read_mapd_settings()
            json_key, conv = _MAPD_SETTINGS_MAP[key]
            json_val = mapd_settings.get(json_key)
            if json_val is not None:
                if conv == "bool":
                    result[key] = bool(json_val)
                    continue
                elif conv == "offset_pct":
                    # MapdSettings stores decimal (0.1) → UI wants percentage (10)
                    result[key] = round(float(json_val) * 100)
                    continue
                elif conv == "lat_idx":
                    # MapdSettings stores value (2.0) → UI wants button index (1)
                    lat_vals = [1.5, 2.0, 2.5, 3.0]
                    try:
                        result[key] = lat_vals.index(float(json_val))
                    except ValueError:
                        result[key] = 1  # default 2.0
                    continue

        if meta["type"] == "bool":
            result[key] = raw == "1"
        elif meta["type"] == "int":
            result[key] = int(raw) if raw else 0
        else:
            result[key] = raw
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
    write_param(key, raw)
    if key in MAPD_PARAM_KEYS:
        update_mapd_settings()
    return web.json_response({"status": "ok", "key": key, "value": value})
