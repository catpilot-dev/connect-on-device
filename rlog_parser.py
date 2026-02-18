"""rlog parsing utilities for Connect on Device.

Reads and parses rlog files for route metadata extraction, GPS coordinates,
segment distances, drive event generation, and HUD snapshot extraction.
Self-contained module using only stdlib + lazy cereal/zstandard imports.
"""

import logging
import math

logger = logging.getLogger("connect.rlog")


class AttributeDict(dict):
    """Dict wrapper supporting attribute access for nested data.

    Allows snapshot dicts to be accessed like cereal objects:
        msg.position.x, msg.laneLines[0].y, msg.cruiseState.speed
    so HudRenderer drawing methods work unchanged.
    """

    def __getattr__(self, key):
        try:
            val = self[key]
        except KeyError:
            raise AttributeError(f"AttributeDict has no key {key!r}")
        return val

    def __setattr__(self, key, value):
        self[key] = value

    @classmethod
    def wrap(cls, obj):
        """Recursively wrap dicts/lists into AttributeDict."""
        if isinstance(obj, dict) and not isinstance(obj, cls):
            return cls({k: cls.wrap(v) for k, v in obj.items()})
        if isinstance(obj, (list, tuple)):
            return [cls.wrap(item) for item in obj]
        return obj


def _iter_rlog(rlog_path: str):
    """Iterate over rlog messages using cereal directly (avoids LogReader path validation)."""
    from cereal import log as capnp_log
    import zstandard as zstd

    if rlog_path.endswith(".zst"):
        with open(rlog_path, "rb") as f:
            data = zstd.ZstdDecompressor().stream_reader(f).read()
    else:
        data = open(rlog_path, "rb").read()

    return capnp_log.Event.read_multiple_bytes(data)


def _haversine_dist(lat1, lon1, lat2, lon2):
    """Distance in meters between two GPS points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _parse_rlog_metadata(rlog_path: str) -> dict:
    """Parse initData + first GPS + segment distance from an rlog file.

    GPS lock can take 30-60+ seconds (24,000+ messages), so we scan the
    entire rlog — the file is already in memory from _iter_rlog anyway.
    Also accumulates GPS distance for the segment.
    """
    meta = {}
    has_init = False
    has_gps = False
    has_gps_time = False
    has_car = False
    last_lat = last_lng = None
    seg_dist = 0.0

    try:
        for ev in _iter_rlog(rlog_path):
            w = ev.which()
            if w == "initData" and not has_init:
                init = ev.initData
                meta["dongle_id"] = init.dongleId
                meta["git_commit"] = init.gitCommit
                meta["git_branch"] = init.gitBranch
                meta["git_remote"] = init.gitRemote
                meta["version"] = init.version
                meta["device_type"] = str(init.deviceType)
                wtn = getattr(init, "wallTimeNanos", 0)
                if wtn:
                    meta["wall_time_nanos"] = wtn
                    meta["create_time"] = wtn / 1e9
                has_init = True
            elif w == "carParams" and not has_car:
                try:
                    meta["car_fingerprint"] = ev.carParams.carFingerprint
                except Exception:
                    pass
                has_car = True
            elif w == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                lat, lng = gps.latitude, gps.longitude
                has_fix = bool(gps.flags & 1)
                has_coords = lat != 0 and lng != 0
                # Accept unfixed GPS for location (good enough for timezone)
                if has_coords and not has_gps:
                    meta["start_lat"] = lat
                    meta["start_lng"] = lng
                    has_gps = True
                # Capture GPS time from first fixed position
                if has_fix and has_coords and not has_gps_time:
                    gps_ms = getattr(gps, "unixTimestampMillis", 0)
                    if gps_ms > 0:
                        meta["gps_time"] = gps_ms / 1000.0
                        has_gps_time = True
                # Only use fixed GPS for distance calculation (unfixed can jitter)
                if has_fix and has_coords:
                    if last_lat is not None:
                        seg_dist += _haversine_dist(last_lat, last_lng, lat, lng)
                    last_lat, last_lng = lat, lng
    except Exception as e:
        logger.debug("rlog parse error: %s", e)

    # Prefer GPS time over wallTimeNanos when they differ by >24h
    # (wallTimeNanos often reflects AGNOS build date, not recording time)
    gps_t = meta.get("gps_time")
    wall_t = meta.get("create_time")
    if gps_t and wall_t and abs(gps_t - wall_t) > 86400:
        meta["create_time"] = gps_t
        meta["wall_time_nanos"] = int(gps_t * 1e9)

    if seg_dist > 0:
        meta["segment_distance_m"] = seg_dist

    return meta


def _find_first_gps(rlog_path: str) -> tuple | None:
    """Fast GPS-only scan — returns (lat, lng) or None.
    Accepts unfixed GPS (flags=0) if coordinates are non-zero,
    since that's sufficient for timezone estimation."""
    try:
        for ev in _iter_rlog(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.latitude != 0 and gps.longitude != 0:
                    return (gps.latitude, gps.longitude)
    except Exception:
        pass
    return None


def _segment_gps_distance(rlog_path: str) -> float:
    """Calculate GPS distance in meters for a single segment's rlog."""
    last_lat = last_lng = None
    dist = 0.0
    try:
        for ev in _iter_rlog(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.flags & 1:
                    lat, lng = gps.latitude, gps.longitude
                    if last_lat is not None:
                        dist += _haversine_dist(last_lat, last_lng, lat, lng)
                    last_lat, last_lng = lat, lng
    except Exception:
        pass
    return dist


def _generate_coords_json(rlog_path: str, segment_num: int) -> list:
    """Generate GPS path data (coords.json) from an rlog file."""
    coords = []
    base_mono = None
    last_lat = last_lng = None
    total_dist = 0.0

    try:
        for ev in _iter_rlog(rlog_path):
            # Skip initData for base_mono — it has openpilot start time, not segment start
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if not (gps.flags & 1):
                    continue

                lat, lng = gps.latitude, gps.longitude
                if base_mono is not None:
                    t = segment_num * 60.0 + (ev.logMonoTime - base_mono) / 1e9
                else:
                    t = segment_num * 60.0

                if last_lat is not None:
                    total_dist += _haversine_dist(last_lat, last_lng, lat, lng)

                last_lat, last_lng = lat, lng
                coords.append({
                    "t": round(t, 3),
                    "lng": round(lng, 7),
                    "lat": round(lat, 7),
                    "speed": round(gps.speed, 2),
                    "dist": round(total_dist, 1),
                })
    except Exception as e:
        logger.debug("coords generation error for %s: %s", rlog_path, e)

    return coords


def _generate_events_json(rlog_path: str, segment_num: int) -> list:
    """Generate drive events (events.json) from an rlog file."""
    events = []
    base_mono = None
    last_state = None
    last_enabled = None
    last_alert = None

    try:
        for ev in _iter_rlog(rlog_path):
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            w = ev.which()
            state = enabled = alert_status = None

            # Support both controlsState (older) and selfdriveState (0.10.x+)
            if w == "selfdriveState":
                sd = ev.selfdriveState
                state = str(sd.state)
                enabled = bool(sd.enabled)
                alert_status = sd.alertStatus.raw if hasattr(sd, "alertStatus") else 0
            elif w == "controlsState":
                cs = ev.controlsState
                if hasattr(cs, "state"):
                    state = str(cs.state)
                    enabled = bool(cs.enabled)
                    alert_status = cs.alertStatus.raw if hasattr(cs, "alertStatus") else 0

            if state is not None and (state != last_state or enabled != last_enabled or alert_status != last_alert):
                if base_mono is not None:
                    offset_ms = (ev.logMonoTime - base_mono) / 1e6
                else:
                    offset_ms = 0
                route_offset_ms = segment_num * 60000 + offset_ms
                events.append({
                    "type": "state",
                    "time": ev.logMonoTime / 1e9,
                    "offset_millis": round(offset_ms),
                    "route_offset_millis": round(route_offset_ms),
                    "data": {
                        "state": state,
                        "enabled": enabled,
                        "alertStatus": alert_status,
                    },
                })
                last_state = state
                last_enabled = enabled
                last_alert = alert_status
    except Exception as e:
        logger.debug("events generation error for %s: %s", rlog_path, e)

    return events


def _extract_xyz(obj):
    """Extract x/y/z lists from a cereal XYZ object into plain lists."""
    return {"x": list(obj.x), "y": list(obj.y), "z": list(obj.z)}


def extract_hud_snapshots(rlog_path: str) -> list:
    """Extract HUD snapshots from an rlog file for replay rendering.

    Iterates the rlog, tracking latest state for carState, selfdriveState,
    radarState, and liveCalibration. At each modelV2 event (~20Hz), captures
    a snapshot of all current state wrapped in AttributeDict for attribute access.

    Returns list of dicts: [{offset_ms, carState, selfdriveState, modelV2, radarState, liveCalibration}, ...]
    """
    snapshots = []
    base_mono = None

    # Latest state accumulators (plain dicts, not capnp refs)
    latest_car = None
    latest_sd = None
    latest_radar = None
    latest_calib = None

    try:
        for ev in _iter_rlog(rlog_path):
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            w = ev.which()

            if w == "carState":
                cs = ev.carState
                cruise = cs.cruiseState
                latest_car = {
                    "vEgo": float(cs.vEgo),
                    "vEgoCluster": float(getattr(cs, "vEgoCluster", 0.0)),
                    "vCruiseCluster": float(getattr(cs, "vCruiseCluster", 0.0)),
                    "leftBlinker": bool(cs.leftBlinker),
                    "rightBlinker": bool(cs.rightBlinker),
                    "cruiseState": {
                        "speed": float(cruise.speed),
                        "enabled": bool(cruise.enabled),
                    },
                }

            elif w == "selfdriveState":
                sd = ev.selfdriveState
                latest_sd = {
                    "state": str(sd.state),
                    "enabled": bool(sd.enabled),
                    "alertSize": sd.alertSize.raw if hasattr(sd.alertSize, "raw") else int(sd.alertSize),
                    "alertText1": str(sd.alertText1) if sd.alertText1 else "",
                    "alertText2": str(sd.alertText2) if sd.alertText2 else "",
                    "alertStatus": sd.alertStatus.raw if hasattr(sd.alertStatus, "raw") else int(sd.alertStatus),
                }

            elif w == "radarState":
                rs = ev.radarState
                lead = rs.leadOne
                latest_radar = {
                    "leadOne": {
                        "status": bool(lead.status),
                        "dRel": float(lead.dRel),
                        "yRel": float(lead.yRel),
                        "vRel": float(lead.vRel),
                    },
                }

            elif w == "liveCalibration":
                lc = ev.liveCalibration
                latest_calib = {
                    "rpyCalib": list(lc.rpyCalib),
                    "height": list(lc.height) if len(lc.height) > 0 else [],
                }

            elif w == "modelV2":
                if base_mono is None:
                    continue

                model = ev.modelV2
                pos = model.position
                # Skip empty model frames
                if len(pos.x) == 0:
                    continue

                model_dict = {
                    "position": _extract_xyz(pos),
                    "laneLines": [_extract_xyz(ll) for ll in model.laneLines],
                    "laneLineProbs": list(model.laneLineProbs),
                    "roadEdges": [_extract_xyz(re) for re in model.roadEdges],
                    "roadEdgeStds": list(model.roadEdgeStds),
                    "acceleration": {"x": list(model.acceleration.x)},
                }

                offset_ms = (ev.logMonoTime - base_mono) / 1e6
                snapshots.append(AttributeDict.wrap({
                    "offset_ms": offset_ms,
                    "carState": latest_car,
                    "selfdriveState": latest_sd,
                    "modelV2": model_dict,
                    "radarState": latest_radar,
                    "liveCalibration": latest_calib,
                }))

    except Exception as e:
        logger.warning("HUD snapshot extraction error for %s: %s", rlog_path, e)

    logger.info("Extracted %d HUD snapshots from %s", len(snapshots), rlog_path)
    return snapshots
