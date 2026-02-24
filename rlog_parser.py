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


def _iter_rlog_head(rlog_path: str, max_bytes: int = 5_000_000):
    """Iterate over the first max_bytes of decompressed rlog data.

    Reads only a bounded prefix of the rlog — enough for initData (~msg 1),
    carParams (~msg 100), and often first GPS (~msg 500-5000) — while keeping
    memory usage to ~5MB instead of the full 50-200MB per segment.
    """
    from cereal import log as capnp_log
    import zstandard as zstd

    if rlog_path.endswith(".zst"):
        with open(rlog_path, "rb") as f:
            data = zstd.ZstdDecompressor().stream_reader(f).read(max_bytes)
    else:
        with open(rlog_path, "rb") as f:
            data = f.read(max_bytes)

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
    """Parse initData + first GPS + partial segment distance from an rlog file.

    Uses _iter_rlog_head() to read only the first ~5MB of decompressed data,
    keeping memory usage bounded. This covers initData, carParams, and often
    first GPS fix. Distance is partial (within the 5MB window only).
    """
    meta = {}
    has_init = False
    has_gps = False
    has_gps_time = False
    has_car = False
    last_lat = last_lng = None
    seg_dist = 0.0

    try:
        for ev in _iter_rlog_head(rlog_path):
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

    # gps_time is stored separately — route naming uses GPS time exclusively.
    # wallTimeNanos/create_time kept as-is for reference only.

    if seg_dist > 0:
        meta["segment_distance_m"] = seg_dist

    return meta


def _find_first_gps(rlog_path: str) -> tuple | None:
    """Fast GPS-only scan — returns (lat, lng) or None.
    Accepts unfixed GPS (flags=0) if coordinates are non-zero,
    since that's sufficient for timezone estimation.
    Uses _iter_rlog_head() to cap memory at ~5MB."""
    try:
        for ev in _iter_rlog_head(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.latitude != 0 and gps.longitude != 0:
                    return (gps.latitude, gps.longitude)
    except Exception:
        pass
    return None


def _find_first_gps_time(rlog_path: str) -> float | None:
    """Fast scan for first GPS time (unix seconds) from a fixed position.
    Returns None if no fixed GPS with valid timestamp found."""
    try:
        for ev in _iter_rlog_head(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.flags & 1 and gps.latitude != 0 and gps.longitude != 0:
                    ms = getattr(gps, "unixTimestampMillis", 0)
                    if ms > 0:
                        return ms / 1000.0
    except Exception:
        pass
    return None


def _find_last_gps(rlog_path: str) -> tuple | None:
    """Scan full rlog and return last fixed GPS (lat, lng) or None."""
    last = None
    try:
        for ev in _iter_rlog(rlog_path):
            if ev.which() == "gpsLocationExternal":
                gps = ev.gpsLocationExternal
                if gps.flags & 1 and gps.latitude != 0 and gps.longitude != 0:
                    last = (gps.latitude, gps.longitude)
    except Exception:
        pass
    return last


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


def _extract_bookmarks(rlog_path: str, segment_num: int) -> list:
    """Extract userBookmark timestamps from an rlog file.

    Returns sorted list of route_offset_millis integers for each bookmark
    found in the segment.
    """
    bookmarks = []
    base_mono = None

    try:
        for ev in _iter_rlog(rlog_path):
            if base_mono is None and ev.which() != "initData":
                base_mono = ev.logMonoTime

            if ev.which() == "userBookmark":
                if base_mono is not None:
                    offset_ms = (ev.logMonoTime - base_mono) / 1e6
                else:
                    offset_ms = 0
                route_offset_ms = segment_num * 60000 + offset_ms
                bookmarks.append(round(route_offset_ms))
    except Exception as e:
        logger.debug("bookmark extraction error for %s: %s", rlog_path, e)

    return sorted(bookmarks)


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

            # Bookmark events → user_flag markers on timeline
            if w == "userBookmark":
                if base_mono is not None:
                    offset_ms = (ev.logMonoTime - base_mono) / 1e6
                else:
                    offset_ms = 0
                route_offset_ms = segment_num * 60000 + offset_ms
                events.append({
                    "type": "user_flag",
                    "route_offset_millis": round(route_offset_ms),
                })
                continue

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


def extract_signal_catalog(seg_log_pairs: list) -> dict:
    """Scan rlogs and return inventory of all message types with field names.

    Args:
        seg_log_pairs: list of (seg_num, log_path) tuples.

    Returns:
        {msgType: {count: int, fields: [str], freq_hz: float, kind: "numeric"|"snapshot"}}
    """
    catalog = {}  # {msgType: {count, fields, kind}}
    seg_count = len(seg_log_pairs)

    for _seg_num, log_path in seg_log_pairs:
        try:
            for ev in _iter_rlog(log_path):
                w = ev.which()
                if w not in catalog:
                    # First occurrence — capture field names via to_dict
                    try:
                        msg_obj = getattr(ev, w)
                        d = msg_obj.to_dict(verbose=True) if hasattr(msg_obj, 'to_dict') else {}
                        fields = list(d.keys()) if isinstance(d, dict) else []
                    except Exception:
                        fields = []
                    catalog[w] = {"count": 1, "fields": fields}
                else:
                    catalog[w]["count"] += 1
        except Exception as e:
            logger.debug("signal catalog error for %s: %s", log_path, e)

    # Classify and compute frequency
    duration_sec = max(seg_count * 60, 1)
    result = {}
    for msg_type, info in catalog.items():
        freq_hz = round(info["count"] / duration_sec, 2)
        # Heuristic: <=2 per segment → snapshot, otherwise numeric
        avg_per_seg = info["count"] / max(seg_count, 1)
        kind = "snapshot" if avg_per_seg <= 2 else "numeric"
        result[msg_type] = {
            "count": info["count"],
            "fields": info["fields"],
            "freq_hz": freq_hz,
            "kind": kind,
        }

    return result


def extract_signal_data(seg_log_pairs: list, msg_type: str, max_samples: int = 3000) -> list:
    """Extract all fields for a specific message type from rlogs.

    Args:
        seg_log_pairs: list of (seg_num, log_path) tuples.
        msg_type: cereal message type name (e.g. "carState", "initData").
        max_samples: max samples to return (downsampled if exceeded).

    Returns:
        [{t: float, ...all_fields_from_to_dict...}, ...]
    """
    raw = []

    for seg_num, log_path in seg_log_pairs:
        base_mono = None
        try:
            for ev in _iter_rlog(log_path):
                if base_mono is None and ev.which() != "initData":
                    base_mono = ev.logMonoTime

                if ev.which() != msg_type:
                    continue

                t = seg_num * 60.0
                if base_mono is not None:
                    t += (ev.logMonoTime - base_mono) / 1e9

                try:
                    msg_obj = getattr(ev, msg_type)
                    d = msg_obj.to_dict(verbose=True) if hasattr(msg_obj, 'to_dict') else {}
                    if not isinstance(d, dict):
                        d = {"value": d}
                except Exception:
                    d = {}

                d["t"] = round(t, 3)
                raw.append(d)
        except Exception as e:
            logger.debug("signal data error for %s/%s: %s", log_path, msg_type, e)

    # Downsample if needed (for numeric types with many samples)
    if len(raw) > max_samples:
        stride = len(raw) / max_samples
        raw = [raw[int(i * stride)] for i in range(max_samples)]

    logger.info("Extracted %d signal samples for %s from %d segments", len(raw), msg_type, len(seg_log_pairs))
    return raw


def _sanitize_for_json(obj):
    """Recursively convert non-JSON-serializable types (bytes, etc.) to strings."""
    if isinstance(obj, bytes):
        # Short bytes as hex, long bytes as truncated
        if len(obj) <= 64:
            return obj.hex()
        return f"<{len(obj)} bytes>"
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def extract_all_signals(seg_log_pairs: list, max_samples: int = 3000) -> dict:
    """Single-pass extraction of catalog + data for ALL message types.

    Reads each log file once, collecting to_dict() for every message.
    Returns: {
        catalog: {msgType: {count, fields, freq_hz, kind}},
        data: {msgType: [{t, ...fields...}, ...]}
    }
    """
    catalog_counts = {}   # {msgType: int}
    catalog_fields = {}   # {msgType: [str]}
    all_data = {}         # {msgType: [{t, ...}, ...]}
    seg_count = len(seg_log_pairs)

    for seg_num, log_path in seg_log_pairs:
        base_mono = None
        try:
            for ev in _iter_rlog(log_path):
                w = ev.which()
                if base_mono is None and w != "initData":
                    base_mono = ev.logMonoTime

                t = seg_num * 60.0
                if base_mono is not None:
                    t += (ev.logMonoTime - base_mono) / 1e9

                # Catalog: count + capture fields on first occurrence
                if w not in catalog_counts:
                    catalog_counts[w] = 1
                    try:
                        msg_obj = getattr(ev, w)
                        d = msg_obj.to_dict(verbose=True) if hasattr(msg_obj, 'to_dict') else {}
                        catalog_fields[w] = list(d.keys()) if isinstance(d, dict) else []
                    except Exception:
                        d = {}
                        catalog_fields[w] = []
                else:
                    catalog_counts[w] += 1
                    try:
                        msg_obj = getattr(ev, w)
                        d = msg_obj.to_dict(verbose=True) if hasattr(msg_obj, 'to_dict') else {}
                    except Exception:
                        d = {}

                if not isinstance(d, dict):
                    d = {"value": d}
                d = _sanitize_for_json(d)
                d["t"] = round(t, 3)

                if w not in all_data:
                    all_data[w] = []
                all_data[w].append(d)
        except Exception as e:
            logger.debug("extract_all_signals error for %s: %s", log_path, e)

    # Build catalog with classification
    duration_sec = max(seg_count * 60, 1)
    catalog = {}
    for msg_type, count in catalog_counts.items():
        freq_hz = round(count / duration_sec, 2)
        avg_per_seg = count / max(seg_count, 1)
        kind = "snapshot" if avg_per_seg <= 2 else "numeric"
        catalog[msg_type] = {
            "count": count,
            "fields": catalog_fields.get(msg_type, []),
            "freq_hz": freq_hz,
            "kind": kind,
        }

    # Downsample numeric types that exceed max_samples
    for msg_type, samples in all_data.items():
        if len(samples) > max_samples:
            stride = len(samples) / max_samples
            all_data[msg_type] = [samples[int(i * stride)] for i in range(max_samples)]

    total_samples = sum(len(v) for v in all_data.values())
    logger.info("Extracted %d total signal samples across %d types from %d segments",
                total_samples, len(catalog), seg_count)

    return {"catalog": catalog, "data": all_data}


def extract_dashboard_telemetry(seg_log_pairs: list) -> list:
    """Extract dashboard telemetry from rlog/qlog files at ~5Hz.

    Args:
        seg_log_pairs: list of (seg_num, log_path) tuples. seg_num is the
            actual segment number used for route-relative time offsets.

    Single-pass read of each log, tracking latest state from carState,
    carControl, selfdriveState, and peripheralState. For rlog (~20Hz carState),
    emits every 4th sample (~5Hz). For qlog (~2Hz carState), keeps every sample.

    Returns list of dicts with all dashboard fields.
    """
    samples = []
    cs_count = 0

    # Latest state accumulators
    steer_cmd = 0.0
    accel_cmd = 0.0
    sd_state = ""
    sd_enabled = False
    voltage = 0.0

    for seg_num, log_path in seg_log_pairs:
        base_mono = None
        # qlog is already sparse (~2Hz) — keep every carState
        # rlog is dense (~20Hz) — downsample to ~5Hz
        is_qlog = "qlog" in log_path.rsplit("/", 1)[-1]
        downsample = 1 if is_qlog else 4
        # Track which accumulator types we've seen this segment.
        # Don't emit samples until all are populated — prevents zeros
        # at segment boundaries when loaded via per-segment API calls.
        seen = set()
        try:
            for ev in _iter_rlog(log_path):
                if base_mono is None and ev.which() != "initData":
                    base_mono = ev.logMonoTime

                w = ev.which()

                if w == "carControl":
                    cc = ev.carControl
                    act = cc.actuators
                    steer_cmd = float(getattr(act, "torque", getattr(act, "steer", 0)))
                    accel_cmd = float(act.accel)
                    seen.add("cc")

                elif w == "selfdriveState":
                    sd = ev.selfdriveState
                    sd_state = str(sd.state)
                    sd_enabled = bool(sd.enabled)
                    seen.add("sd")

                elif w == "peripheralState":
                    ps = ev.peripheralState
                    voltage = float(ps.voltage) / 1000.0  # millivolts → volts
                    seen.add("ps")

                elif w == "carState":
                    cs_count += 1
                    if cs_count % downsample != 0:
                        continue
                    if base_mono is None:
                        continue
                    # Skip until accumulators are populated (avoids zero-jumps)
                    if len(seen) < 3:
                        continue

                    cs = ev.carState
                    t = seg_num * 60.0 + (ev.logMonoTime - base_mono) / 1e9
                    cruise = cs.cruiseState

                    samples.append({
                        "t": round(t, 3),
                        "coolantTemp": round(float(getattr(cs, "coolantTemp", 0)), 1),
                        "oilTemp": round(float(getattr(cs, "oilTemp", 0)), 1),
                        "vEgo": round(float(cs.vEgo), 3),
                        "steeringAngleDeg": round(float(cs.steeringAngleDeg), 2),
                        "gasPressed": bool(cs.gasPressed),
                        "brakePressed": bool(cs.brakePressed),
                        "cruiseSpeed": round(float(cruise.speed), 2),
                        "cruiseEnabled": bool(cruise.enabled),
                        "steerCmd": round(steer_cmd, 4),
                        "accelCmd": round(accel_cmd, 4),
                        "sdState": sd_state,
                        "sdEnabled": sd_enabled,
                        "voltage": round(voltage, 2),
                    })
        except Exception as e:
            logger.warning("Dashboard telemetry extraction error for %s: %s", log_path, e)

    logger.info("Extracted %d dashboard telemetry samples from %d segments", len(samples), len(seg_log_pairs))
    return samples


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
