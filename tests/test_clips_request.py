# tests/test_clips_request.py
import pytest
from pathlib import Path
import clips
from clips import (ClipStore, ClipRequestError, available_ranges, clamp_window,
                   collect_inputs, check_admission, build_requests)

def _route(tmp_path, segs=(0, 1, 2), cams=("fcamera.hevc", "ecamera.hevc")):
    lid = "0000011d--258ef95d84"
    segments = []
    for n in segs:
        d = tmp_path / f"{lid}--{n}"; d.mkdir(parents=True, exist_ok=True)
        for c in cams: (d / c).write_bytes(b"\x00")
        segments.append({"number": n, "path": str(d), "files": set(cams)})
    return {"fullname": "d/2026-10-27--08-14-33", "_local_id": lid, "local_id": lid,
            "maxqlog": max(segs), "_segments": segments, "version": "0.11.2",
            "git_branch": "v0.11.2", "git_commit": "abc", "platform": "BMW_E90"}

class FakeStore:
    def __init__(self, data_dir): self.data_dir = Path(data_dir)
    def resolve_segment_path(self, fullname, segment, filename):
        p = self.data_dir / f"0000011d--258ef95d84--{segment}" / filename
        return p if p.exists() else None

@pytest.fixture
def world(tmp_path, monkeypatch):
    route = _route(tmp_path)
    store = FakeStore(tmp_path)
    cs = ClipStore(str(tmp_path / "cod_clips"))
    monkeypatch.setattr(clips, "segment_frame_count", lambda path: 1200)     # full 60 s segments
    monkeypatch.setattr(clips, "_free_bytes", lambda store: 100 * 1024**3)
    monkeypatch.setattr(clips, "provenance", lambda *a, **k: {"wall_start_epoch": None, "gps": None})
    return route, store, cs

def test_available_ranges_merges_consecutive_segments(world):
    route, *_ = world
    assert available_ranges(route) == {"road": [[0, 180]], "wide": [[0, 180]]}

def test_available_ranges_splits_on_gap(tmp_path):
    route = _route(tmp_path, segs=(0, 2))
    assert available_ranges(route)["road"] == [[0, 60], [120, 180]]

def test_window_is_clamped_to_real_frame_count(world, monkeypatch):
    route, store, _ = world
    monkeypatch.setattr(clips, "segment_frame_count", lambda path: 400)      # last seg has 20 s
    start, end, clamped = clamp_window(route, store, 100, 175, "road")
    assert (start, end, clamped) == (100, 140, True)

def test_collect_inputs_spans_segments(world):
    route, store, _ = world
    paths = collect_inputs(route, store, "road", 50, 130)
    assert [Path(p).parent.name[-1] for p in paths] == ["0", "1", "2"]

def test_collect_inputs_refuses_missing_camera(tmp_path):
    route = _route(tmp_path, cams=("fcamera.hevc",))
    with pytest.raises(ClipRequestError) as e:
        collect_inputs(route, FakeStore(tmp_path), "wide", 0, 30)
    assert e.value.status == 409

def test_collect_inputs_refuses_gap(tmp_path):
    route = _route(tmp_path, segs=(0, 2))
    with pytest.raises(ClipRequestError) as e:
        collect_inputs(route, FakeStore(tmp_path), "road", 50, 130)   # needs segment 1
    assert e.value.status == 409 and "1" in str(e.value)

def test_provenance_uses_frame_times_and_coords(tmp_path, monkeypatch):
    import json
    from clips import provenance
    route = _route(tmp_path); store = FakeStore(tmp_path)
    seg2 = Path(route["_segments"][2]["path"])
    (seg2 / "coords.json").write_text(json.dumps([
        {"t": 120.0, "lat": 31.20, "lng": 121.40}, {"t": 130.0, "lat": 31.23, "lng": 121.47}]))
    monkeypatch.setattr(clips, "_frame_times",
                        lambda store, fullname, lid, seg: [1_780_000_000.0 + i / 20 for i in range(1200)] if seg == 2 else None)
    p = provenance(route, store, "road", 130.0, 170.0)
    assert p["wall_start_epoch"] == pytest.approx(1_780_000_000.0 + 10.0)   # frame 200 of segment 2
    assert p["gps"] == {"lat": 31.23, "lng": 121.47}
    assert (p["op_version"], p["git_branch"], p["git_commit"], p["platform"]) == ("0.11.2", "v0.11.2", "abc", "BMW_E90")

def test_provenance_without_logs_is_null_not_error(tmp_path, monkeypatch):
    from clips import provenance
    route = _route(tmp_path)
    monkeypatch.setattr(clips, "_frame_times", lambda *a: None)
    p = provenance(route, FakeStore(tmp_path), "road", 0.0, 10.0)
    assert p["wall_start_epoch"] is None and p["gps"] is None

def test_admission_refuses_below_emergency_line(world, monkeypatch):
    _, store, _ = world
    monkeypatch.setattr(clips, "_free_bytes", lambda store: 10 * 1024**3 + 1000)
    with pytest.raises(ClipRequestError) as e:
        check_admission(store, 2000)
    assert e.value.status == 409 and "free space" in str(e.value).lower()

@pytest.mark.parametrize("body,msg", [
    ({"start_sec": 10, "end_sec": 10.5, "cameras": ["road"]}, "at least 1"),
    ({"start_sec": 0, "end_sec": 121, "cameras": ["road"]}, "2 minutes"),
    ({"start_sec": 0, "end_sec": 30, "cameras": ["cabin"]}, "camera"),
    ({"start_sec": 0, "end_sec": 30, "cameras": ["road"], "bitrate_mbps": 7}, "bitrate"),
    ({"start_sec": 0, "end_sec": 30, "cameras": []}, "camera"),
    ({"start_sec": 0, "end_sec": 30, "cameras": ["road"], "name": "../x.mp4"}, "name"),
])
def test_build_requests_rejects_bad_input(world, body, msg):
    route, store, cs = world
    with pytest.raises(ClipRequestError) as e:
        build_requests(route, store, body, cs, now=1.0)
    assert e.value.status == 400 and msg in str(e.value)

def test_build_requests_one_sidecar_per_camera(world):
    route, store, cs = world
    reqs = build_requests(route, store, {"start_sec": 130, "end_sec": 170, "cameras": ["road", "wide"]}, cs, now=5.0)
    assert [r["camera"] for r in reqs] == ["road", "wide"]
    r = reqs[0]
    assert r["filename"] == "2026-10-27--08-14-33_road_02m10s-02m50s.mp4"
    assert r["status"] == "queued" and r["requested_at"] == 5.0
    assert r["bitrate_mbps"] == 8 and r["duration_sec"] == 40
    assert r["encode_start_sec"] == 10            # 130 s is 10 s into segment 2
    assert [Path(p).parent.name[-1] for p in r["inputs"]] == ["2"]
    assert r["estimate_bytes"] == 40 * 8 * 1_000_000 // 8

def test_default_filename_collision_gets_suffix(world):
    route, store, cs = world
    body = {"start_sec": 0, "end_sec": 30, "cameras": ["road"]}
    first = build_requests(route, store, body, cs, now=1.0)[0]; cs.write(first)
    second = build_requests(route, store, body, cs, now=2.0)[0]
    assert second["filename"] == first["filename"][:-4] + "_2.mp4"

def test_custom_name_applies_to_single_camera_and_suffixes_multi(world):
    route, store, cs = world
    one = build_requests(route, store, {"start_sec": 0, "end_sec": 30, "cameras": ["road"], "name": "crash"}, cs, now=1.0)
    assert one[0]["filename"] == "crash.mp4"
    two = build_requests(route, store, {"start_sec": 0, "end_sec": 30, "cameras": ["road", "wide"], "name": "crash"}, cs, now=1.0)
    assert [r["filename"] for r in two] == ["crash_road.mp4", "crash_wide.mp4"]
