# Dashcam Clips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third export path to COD that hardware-transcodes a chosen window of raw camera footage into a shareable H.264 MP4 on the device, with a persistent clips library, available only on the `v0.11.2` branch.

**Architecture:** A new `clips.py` module owns the on-disk clip store (one MP4 + one JSON sidecar per clip under `/data/media/0/cod_clips/`), request validation, and an asyncio worker that runs upstream's `encoderd --clip` one job at a time while offroad. `handlers/clips.py` exposes it as five REST endpoints following the screenshots handlers' shape. The Svelte frontend gets a `ClipDialog` opened from the video player and from bookmark rows, and a `ClipsPage` mirroring `ScreenshotsPage`.

**Tech Stack:** Python 3.12, aiohttp, pytest + pytest-aiohttp (backend); Svelte 5 + Vite + Tailwind + vitest (frontend); openpilot v0.11.2 `encoderd` binary (device), ffmpeg (laptop dev only).

**Spec:** `docs/superpowers/specs/2026-10-27-dashcam-clips-design.md`

## Global Constraints

- Work happens only on branch `v0.11.2`. Nothing in this plan touches `dev` or the 0.11.1 release line.
- `VERSION` reads `0.11.2` before any other task (Task 1). `make_release.sh` is never run on this branch during this plan; device testing uses `deploy_dev.sh` only.
- Clip directory: `/data/media/0/cod_clips/`, env override `COD_CLIPS_DIR`. Never under `realdata`, never under `/data/connect-on-device`.
- Maximum clip length **120 s**, minimum **1 s**. Bookmark window **30 s before, 30 s after** the tap.
- Bitrates **5, 8, 12** Mbps; default **8**. Size estimate `duration_sec × bitrate_mbps / 8` MB.
- Cameras: `road` → `fcamera.hevc`, `wide` → `ecamera.hevc`, `driver` → `dcamera.hevc`. Default selection road + wide.
- Admission: refuse when `free_bytes − estimate_bytes < 10 GB` (`storage_management.EMERGENCY_BYTES`).
- Encoder on device: `<OPENPILOT_DIR>/openpilot/system/loggerd/encoderd --clip OUT START DUR --bitrate BPS --speedup 1 --metadata JSON -- seg0/<cam>.hevc seg1/<cam>.hevc ...`. Always pass `--metadata`. Stop it with **SIGKILL**, then remove the temp output and its `.lock`.
- The software encoder (ffmpeg concat → libx264) is selected only when the encoderd binary is absent (laptop). It is never chosen on the device.
- Offroad only: no encode starts or continues while `IsOnroad == "1"`, and the worker yields to a running user HUD render.
- Clips are never reclaimed by `storage_management`.
- `POST`/`DELETE` clip endpoints and `GET /v1/clips/{filename}` are refused onroad (409) via `handlers/middleware.py`; `GET /v1/clips` and `GET /v1/route/{routeName}/clips` stay available.
- Filenames are bare names (`os.path.basename(name) == name`, no leading dot, `.mp4` suffix). Default name `<route date>_<camera>_<MMmSSs>-<MMmSSs>.mp4`; collisions get `_2`, `_3`, ….

## Review Focus

1. **A route whose last segment is still being written** (the drive that just ended, segment file growing): the request must either succeed on the frames present or be refused clearly, never produce a truncated clip that reports `ready`. → test in Task 3 (`test_window_is_clamped_to_real_frame_count`).
2. **Two requests for the same default filename in quick succession** (user double-taps Create): the second must get a `_2` name, not overwrite or error. → test in Task 3 (`test_default_filename_collision_gets_suffix`).
3. **A sidecar whose MP4 is missing or whose MP4 has no sidecar** (manual deletion, crash between the two writes): the listing must not crash and must not show a phantom `ready` clip. → test in Task 2 (`test_list_ignores_orphans`).
4. **Delete of the clip currently encoding, then the encoder exits**: the worker must not resurrect the sidecar or leave the temp/lock file. → test in Task 4 (`test_delete_during_encode_leaves_nothing`).
5. **Server restart with a clip stuck in `encoding`** (power loss mid-encode): it must come back as `queued` and its temp file be gone. → test in Task 4 (`test_startup_requeues_encoding_and_removes_temp`).

---

### Task 1: Branch bookkeeping — VERSION and config

**Files:**
- Modify: `VERSION`
- Modify: `config.py:36-38`
- Test: `tests/test_clips_store.py` (created here, extended by later tasks)

**Interfaces:**
- Produces: `config.COD_CLIPS_DIR: str` (default `/data/media/0/cod_clips`), `config.ENCODERD_BIN: str` (default `os.path.join(OPENPILOT_DIR, "openpilot/system/loggerd/encoderd")`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clips_store.py
import os

def test_config_exposes_clip_paths():
    import config
    assert config.COD_CLIPS_DIR == os.getenv("COD_CLIPS_DIR", "/data/media/0/cod_clips")
    assert config.ENCODERD_BIN.endswith("openpilot/system/loggerd/encoderd")

def test_version_is_0_11_2():
    from pathlib import Path
    assert Path(__file__).resolve().parent.parent.joinpath("VERSION").read_text().strip() == "0.11.2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_clips_store.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'COD_CLIPS_DIR'` and VERSION assertion `'0.11.1' == '0.11.2'`.

- [ ] **Step 3: Bump VERSION and add the two config values**

`VERSION` → `0.11.2`. In `config.py` after `COD_HLS_TMP_DIR`, add `COD_CLIPS_DIR` (env `COD_CLIPS_DIR`) and, in the Binaries block, `ENCODERD_BIN` (env `ENCODERD_BIN`). Add `COD_CLIPS_DIR` to the module docstring's env var list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_clips_store.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add VERSION config.py tests/test_clips_store.py
git commit -m "chore: bump to 0.11.2; add clip store and encoderd paths to config"
```

---

### Task 2: Clip store — sidecars, listing, naming

**Files:**
- Create: `clips.py`
- Test: `tests/test_clips_store.py`

**Interfaces:**
- Produces, in `clips.py`:
  - `CAMERAS: dict[str, str] = {"road": "fcamera.hevc", "wide": "ecamera.hevc", "driver": "dcamera.hevc"}`
  - `BITRATES_MBPS = (5, 8, 12)`, `DEFAULT_BITRATE_MBPS = 8`, `MAX_CLIP_SEC = 120`, `MIN_CLIP_SEC = 1`, `BOOKMARK_PAD_SEC = 30`
  - `STATUS_QUEUED = "queued"`, `STATUS_ENCODING = "encoding"`, `STATUS_READY = "ready"`, `STATUS_ERROR = "error"`
  - `class ClipStore:`
    - `__init__(self, clips_dir: str)`
    - `sidecar_path(self, filename: str) -> Path` (`<dir>/<stem>.json`), `mp4_path(self, filename: str) -> Path`, `temp_path(self, filename: str) -> Path` (`<dir>/.<filename>`), `lock_path(self, filename: str) -> Path` (`<dir>/.<filename>.lock`)
    - `write(self, clip: dict) -> None` — atomic sidecar write (tmp + rename)
    - `read(self, filename: str) -> dict | None`
    - `list(self) -> list[dict]` — all sidecars, newest `requested_at` first; a `ready` sidecar whose MP4 is missing is dropped from the result and its sidecar deleted; MP4s without a sidecar are ignored
    - `queued(self) -> list[dict]` — status `queued`, oldest `requested_at` first
    - `total_bytes(self) -> int` — sum of `size` over `ready` clips
    - `delete(self, filename: str) -> bool` — removes sidecar, MP4, temp and lock if present
    - `unique_filename(self, base: str) -> str` — `base.mp4`, else `base_2.mp4`, `base_3.mp4`, … (checks sidecar and MP4)
  - `default_filename(route_date: str, camera: str, start_sec: float, end_sec: float) -> str` → `f"{route_date}_{camera}_{mm:02d}m{ss:02d}s-{mm:02d}m{ss:02d}s"` (no extension)
  - `validate_filename(name: str) -> str` — raises `ValueError` unless bare, not dot-prefixed, ends `.mp4`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clips_store.py (append)
import json, pytest
from clips import ClipStore, default_filename, validate_filename, STATUS_READY, STATUS_QUEUED

def _clip(filename, status=STATUS_QUEUED, requested_at=1.0, size=None):
    return {"filename": filename, "status": status, "requested_at": requested_at,
            "route": "d/2026-10-27--08-14-33", "local_id": "0000011d--258ef95d84",
            "camera": "road", "start_sec": 130.0, "end_sec": 170.0, "bitrate_mbps": 8,
            "size": size, "error": None}

@pytest.fixture
def store(tmp_path):
    return ClipStore(str(tmp_path / "cod_clips"))

def test_write_then_read_roundtrip(store):
    store.write(_clip("a.mp4"))
    assert store.read("a.mp4")["camera"] == "road"
    assert store.sidecar_path("a.mp4").name == "a.json"
    assert store.temp_path("a.mp4").name == ".a.mp4"
    assert store.lock_path("a.mp4").name == ".a.mp4.lock"

def test_list_newest_first_and_queued_oldest_first(store):
    store.write(_clip("old.mp4", requested_at=1.0))
    store.write(_clip("new.mp4", requested_at=2.0))
    assert [c["filename"] for c in store.list()] == ["new.mp4", "old.mp4"]
    assert [c["filename"] for c in store.queued()] == ["old.mp4", "new.mp4"]

def test_list_ignores_orphans(store):
    store.write(_clip("ghost.mp4", status=STATUS_READY, size=10))      # sidecar, no mp4
    store.mp4_path("stray.mp4").parent.mkdir(parents=True, exist_ok=True)
    store.mp4_path("stray.mp4").write_bytes(b"x")                         # mp4, no sidecar
    assert store.list() == []
    assert not store.sidecar_path("ghost.mp4").exists()

def test_total_bytes_counts_ready_only(store):
    store.mp4_path("r.mp4").parent.mkdir(parents=True, exist_ok=True)
    store.mp4_path("r.mp4").write_bytes(b"12345")
    store.write(_clip("r.mp4", status=STATUS_READY, size=5))
    store.write(_clip("q.mp4", size=99))
    assert store.total_bytes() == 5

def test_delete_removes_every_artifact(store):
    store.write(_clip("d.mp4"))
    for p in (store.mp4_path("d.mp4"), store.temp_path("d.mp4"), store.lock_path("d.mp4")):
        p.write_bytes(b"")
    assert store.delete("d.mp4") is True
    assert not any(p.exists() for p in (store.sidecar_path("d.mp4"), store.mp4_path("d.mp4"),
                                         store.temp_path("d.mp4"), store.lock_path("d.mp4")))
    assert store.delete("d.mp4") is False

def test_default_filename_and_collision_suffix(store):
    base = default_filename("2026-10-27--08-14-33", "road", 130, 170)
    assert base == "2026-10-27--08-14-33_road_02m10s-02m50s"
    assert store.unique_filename(base) == base + ".mp4"
    store.write(_clip(base + ".mp4"))
    assert store.unique_filename(base) == base + "_2.mp4"

@pytest.mark.parametrize("bad", ["../x.mp4", ".hidden.mp4", "a/b.mp4", "noext", ""])
def test_validate_filename_rejects(bad):
    with pytest.raises(ValueError):
        validate_filename(bad)

def test_validate_filename_accepts():
    assert validate_filename("crash_road.mp4") == "crash_road.mp4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_clips_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clips'`

- [ ] **Step 3: Implement `clips.py` store section**

Module docstring: what a clip is, the sidecar-is-truth rule, why the directory is outside `realdata` and the install dir (copy the two reasons from the spec's Storage section). Implement the constants and `ClipStore` per the Interfaces block. `write` uses `json.dumps` to `<stem>.json.tmp` then `os.replace`. `list` scans `*.json` in the directory, skipping names starting with `.`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_clips_store.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add clips.py tests/test_clips_store.py
git commit -m "feat(clips): on-disk clip store with JSON sidecars"
```

---

### Task 3: Request validation — window, cameras, inputs, provenance, admission

**Files:**
- Modify: `clips.py`
- Test: `tests/test_clips_request.py`

**Interfaces:**
- Consumes: `ClipStore` (Task 2); `route_store.RouteStore.resolve_segment_path(fullname, segment, filename) -> Path | None`; route dict keys `fullname`, `_local_id`, `maxqlog`, `_segments` (each `{"number", "path", "files": set}`), `version`, `git_branch`, `git_commit`, `platform`; `storage_management.EMERGENCY_BYTES`; `handlers.media._frame_times_cached(store, fullname, local_id, segment) -> list | None`; `handlers.media._lookup_gps(coords, t) -> dict`.
- Produces, in `clips.py`:
  - `class ClipRequestError(ValueError)` with `.status: int` (400 for bad input, 409 for admission/missing footage) and message
  - `available_ranges(route: dict) -> dict[str, list[list[int]]]` — per camera key (`road`/`wide`/`driver`), merged `[start_sec, end_sec]` runs of consecutive segments whose `files` contain that camera file (60 s per segment)
  - `segment_frame_count(path: str) -> int` — packet count of one `.hevc` via `ffprobe -v error -count_packets -show_entries stream=nb_read_packets -of csv=p=0`; module-level `_FRAME_COUNT_CACHE: dict[str, int]`
  - `clamp_window(route: dict, store, start_sec: float, end_sec: float, camera: str) -> tuple[float, float, bool]` — returns `(start, end, clamped)`; clamps `end` to `last_seg*60 + segment_frame_count(last file)/20` when the window reaches the last recorded segment
  - `collect_inputs(route: dict, store, camera: str, start_sec: float, end_sec: float) -> list[str]` — the consecutive `<camera>.hevc` paths covering `[start, end)`; raises `ClipRequestError(409, "...no <camera> footage for <segment>")` if any is missing
  - `check_admission(store, estimate_bytes: int) -> None` — raises `ClipRequestError(409, "Not enough free space: <free> free, clip needs <estimate>")` when `free − estimate < EMERGENCY_BYTES`; uses `shutil.disk_usage(store.data_dir).free` through a module-level `_free_bytes(store)` for patching
  - `_frame_times(store, fullname: str, local_id: str, segment: int) -> list | None` — thin wrapper that lazily imports `handlers.media._frame_times_cached` (patch point for tests)
  - `provenance(route: dict, store, camera: str, start_sec: float, end_sec: float) -> dict` — `wall_start_epoch` from `_frame_times(...)` for the start segment (index `int((start % 60) * 20)`, `None` if unavailable or out of range), `gps` `{lat, lng}` from the start segment's `coords.json` (`t` is route-relative seconds) via `handlers.media._lookup_gps` (`None` if no file or empty), plus `op_version` (route `version`), `git_branch`, `git_commit`, `platform`
  - `build_requests(route: dict, store, body: dict, clip_store: ClipStore, now: float) -> list[dict]` — validates `start_sec`, `end_sec`, `cameras`, `bitrate_mbps`, optional `name`; applies clamp, inputs, admission (once, on the summed estimate); returns one sidecar dict per camera with status `queued`, `requested_at = now`, `inputs`, `encode_start_sec` (start relative to first input), `duration_sec`, `estimate_bytes`, `clamped`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_clips_request.py -v`
Expected: FAIL — `ImportError: cannot import name 'ClipRequestError' from 'clips'`

- [ ] **Step 3: Implement the request section of `clips.py`**

Per the Interfaces block. `build_requests` order: validate types and ranges (400s) → `clamp_window` per camera → `collect_inputs` → sum estimates → `check_admission` once → `provenance` → sidecars. Estimate is `duration_sec * bitrate_mbps * 1_000_000 // 8`. `name` handling: strip, drop a trailing `.mp4`, validate the result as `<name>.mp4`; with more than one camera append `_<camera>` before the extension. Run `unique_filename` per camera against the store **and** against names already produced in this call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_clips_request.py tests/test_clips_store.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add clips.py tests/test_clips_request.py
git commit -m "feat(clips): validate clip requests — window clamp, inputs, admission, provenance"
```

---

### Task 4: Encoder backends and the offroad worker

**Files:**
- Modify: `clips.py`
- Create: `tests/fake_encoderd.py` (test helper, executable script)
- Test: `tests/test_clips_worker.py`

**Interfaces:**
- Consumes: `ClipStore`, request dicts (Task 3); `handlers.middleware.is_onroad() -> bool`; `handlers.hud._hud_prerender_tasks`; `config.ENCODERD_BIN`, `config.FFMPEG_BIN`.
- Produces, in `clips.py`:
  - `probe_encoder(encoderd_bin: str = ENCODERD_BIN) -> str` — returns `"encoderd"` when the binary exists and `[bin, "--clip"]` exits 2 with `usage:` on stderr; `"ffmpeg"` when the binary is absent and `FFMPEG_BIN` exists; `"none"` otherwise. Result cached in module global `ENCODER: str`, set by `init_encoder(encoderd_bin=ENCODERD_BIN) -> str`.
  - `encoder_command(clip: dict, encoder: str, output: str) -> list[str]` — encoderd form: `[bin, "--clip", output, str(encode_start_sec), str(duration_sec), "--bitrate", str(bitrate_mbps*1_000_000), "--speedup", "1", "--metadata", json.dumps(metadata_dict), "--", *inputs]`; ffmpeg form copies upstream's PC command (concat demuxer on stdin, `-c:v libx264 -preset veryfast -b:v <N>M -pix_fmt yuv420p -movflags +faststart+use_metadata_tags -metadata ai.comma.clip.settings=<json>`). `metadata_dict` is the sidecar minus `inputs` and `status`, plus `requested_at` kept numeric.
  - `async def encode_one(clip_store: ClipStore, clip: dict, encoder: str, poll_sec: float = 1.0, onroad: Callable[[], bool] = is_onroad) -> str` — runs the command with `asyncio.create_subprocess_exec` writing to `temp_path`; while running, every `poll_sec` checks `onroad()` and whether the sidecar still exists; on onroad → `proc.kill()`, await, remove temp + lock, rewrite sidecar as `queued`, return `"requeued"`; on sidecar gone (deleted) → kill, remove temp + lock, return `"deleted"`; on exit 0 → `os.replace(temp, mp4)`, sidecar `ready` with `size`, `encode_sec`, `finished_at`, return `"ready"`; on nonzero → remove temp + lock, sidecar `error` with `error = "clip encoder exited with code N"`, return `"error"`. Always removes `lock_path` if present.
  - `recover_on_startup(clip_store: ClipStore) -> int` — every sidecar in `encoding` → `queued`, its temp and lock removed; returns count.
  - `_user_render_busy() -> bool` — same body as `screenshot_worker._user_render_busy` (lazy import of `handlers.hud._hud_prerender_tasks`); defined here so tests can patch `clips._user_render_busy`.
  - `async def clip_worker_tick(app) -> str` — one scheduling decision: returns `"unsupported"` when `app["clips_encoder"] == "none"`, `"onroad"` when `is_onroad()`, `"busy"` when `_user_render_busy()`, `"idle"` when `clip_store.queued()` is empty; otherwise takes the oldest queued clip, re-runs `check_admission(app["store"], clip["estimate_bytes"])` (on failure writes the sidecar as `error` with the admission message and returns `"admission_error"`), writes it as `encoding` with `started_at = time.time()`, then returns `await encode_one(clip_store, clip, app["clips_encoder"])`.
  - `async def clip_worker(app) -> None` — `while True: await asyncio.sleep(CLIP_WORKER_TICK_SEC); try: await clip_worker_tick(app) except CancelledError: raise except Exception: logger.exception(...)`. `app["store"]`, `app["clip_store"]`, `app["clips_encoder"]` are set by Task 5.
  - `CLIP_WORKER_TICK_SEC = 2`, `ENCODE_RATE_PLACEHOLDER = 4.0` (seconds of footage per wall second; measured on device later — see spec Done criteria), used for `eta_sec` in the status payload: `eta_sec = duration_sec / ENCODE_RATE_PLACEHOLDER`.

- [ ] **Step 1: Write the fake encoder and the failing tests**

`tests/fake_encoderd.py` (chmod +x): parses the encoderd `--clip` argv shape; with no args beyond `--clip` prints `usage: encoderd --clip ...` to stderr and exits 2; otherwise creates `<output>.lock`, sleeps `FAKE_ENCODERD_SLEEP` seconds (env, default 0.2), writes `FAKE_ENCODERD_BYTES` (env, default 4096) bytes to `output`, removes the lock unless `FAKE_ENCODERD_LEAVE_LOCK=1`, exits `FAKE_ENCODERD_EXIT` (env, default 0). It ignores SIGTERM (`signal.signal(SIGTERM, SIG_IGN)`) to mirror the real binary.

```python
# tests/test_clips_worker.py
import asyncio, os, sys, pytest
from pathlib import Path
import clips
from clips import ClipStore, probe_encoder, encoder_command, encode_one, recover_on_startup

FAKE = str(Path(__file__).parent / "fake_encoderd.py")

def _clip(tmp_path, filename="c.mp4", duration=10):
    seg = tmp_path / "seg0"; seg.mkdir(exist_ok=True); (seg / "fcamera.hevc").write_bytes(b"\x00")
    return {"filename": filename, "status": "queued", "requested_at": 1.0, "route": "d/x",
            "local_id": "l", "camera": "road", "start_sec": 5.0, "end_sec": 5.0 + duration,
            "encode_start_sec": 5.0, "duration_sec": duration, "bitrate_mbps": 8,
            "estimate_bytes": 1, "inputs": [str(seg / "fcamera.hevc")], "error": None, "size": None}

@pytest.fixture
def cs(tmp_path):
    return ClipStore(str(tmp_path / "cod_clips"))

def test_probe_encoder_detects_usage_exit_2(tmp_path):
    assert probe_encoder(FAKE) == "encoderd"
    assert probe_encoder(str(tmp_path / "missing")) in ("ffmpeg", "none")

def test_encoder_command_shape(tmp_path):
    clip = _clip(tmp_path)
    cmd = encoder_command(clip, "encoderd", "/out/.c.mp4")
    assert cmd[1:3] == ["--clip", "/out/.c.mp4"]
    assert cmd[3:5] == ["5.0", "10"]
    assert cmd[cmd.index("--bitrate") + 1] == "8000000"
    assert cmd[cmd.index("--speedup") + 1] == "1"
    assert "--metadata" in cmd and cmd[cmd.index("--") + 1:] == clip["inputs"]

@pytest.mark.asyncio
async def test_encode_success_writes_ready(cs, tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    clip = _clip(tmp_path); cs.write(clip)
    result = await encode_one(cs, clip, "encoderd", poll_sec=0.05, onroad=lambda: False)
    assert result == "ready"
    side = cs.read("c.mp4")
    assert side["status"] == "ready" and side["size"] == 4096 and side["encode_sec"] >= 0
    assert cs.mp4_path("c.mp4").exists() and not cs.temp_path("c.mp4").exists()
    assert not cs.lock_path("c.mp4").exists()

@pytest.mark.asyncio
async def test_encode_failure_writes_error_and_cleans(cs, tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    monkeypatch.setenv("FAKE_ENCODERD_EXIT", "1"); monkeypatch.setenv("FAKE_ENCODERD_LEAVE_LOCK", "1")
    clip = _clip(tmp_path); cs.write(clip)
    assert await encode_one(cs, clip, "encoderd", poll_sec=0.05, onroad=lambda: False) == "error"
    assert cs.read("c.mp4")["error"] == "clip encoder exited with code 1"
    assert not cs.temp_path("c.mp4").exists() and not cs.lock_path("c.mp4").exists()

@pytest.mark.asyncio
async def test_onroad_kills_and_requeues(cs, tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    monkeypatch.setenv("FAKE_ENCODERD_SLEEP", "5"); monkeypatch.setenv("FAKE_ENCODERD_LEAVE_LOCK", "1")
    clip = _clip(tmp_path); cs.write(clip)
    flips = iter([False, True, True, True])
    t0 = asyncio.get_event_loop().time()
    result = await encode_one(cs, clip, "encoderd", poll_sec=0.05, onroad=lambda: next(flips, True))
    assert result == "requeued"
    assert asyncio.get_event_loop().time() - t0 < 2.0          # SIGKILL, not a 5 s wait
    assert cs.read("c.mp4")["status"] == "queued"
    assert not cs.temp_path("c.mp4").exists() and not cs.lock_path("c.mp4").exists()

@pytest.mark.asyncio
async def test_delete_during_encode_leaves_nothing(cs, tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    monkeypatch.setenv("FAKE_ENCODERD_SLEEP", "5")
    clip = _clip(tmp_path); cs.write(clip)
    task = asyncio.create_task(encode_one(cs, clip, "encoderd", poll_sec=0.05, onroad=lambda: False))
    await asyncio.sleep(0.2)
    cs.delete("c.mp4")
    assert await task == "deleted"
    assert not cs.sidecar_path("c.mp4").exists() and not cs.temp_path("c.mp4").exists()
    assert not cs.lock_path("c.mp4").exists() and not cs.mp4_path("c.mp4").exists()

def test_startup_requeues_encoding_and_removes_temp(cs, tmp_path):
    clip = _clip(tmp_path); clip["status"] = "encoding"; cs.write(clip)
    cs.temp_path("c.mp4").write_bytes(b"partial"); cs.lock_path("c.mp4").write_bytes(b"")
    assert recover_on_startup(cs) == 1
    assert cs.read("c.mp4")["status"] == "queued"
    assert not cs.temp_path("c.mp4").exists() and not cs.lock_path("c.mp4").exists()

# ── scheduling (clip_worker_tick) ────────────────────────────────────

class _Store:
    data_dir = Path("/tmp")

def _app(cs, encoder="encoderd"):
    return {"store": _Store(), "clip_store": cs, "clips_encoder": encoder}

@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    monkeypatch.setattr(clips, "is_onroad", lambda: False)
    monkeypatch.setattr(clips, "_user_render_busy", lambda: False)
    monkeypatch.setattr(clips, "_free_bytes", lambda store: 100 * 1024**3)

@pytest.mark.asyncio
async def test_tick_states_without_work(cs, quiet, monkeypatch):
    from clips import clip_worker_tick
    assert await clip_worker_tick(_app(cs, "none")) == "unsupported"
    assert await clip_worker_tick(_app(cs)) == "idle"
    monkeypatch.setattr(clips, "is_onroad", lambda: True)
    assert await clip_worker_tick(_app(cs)) == "onroad"

@pytest.mark.asyncio
async def test_tick_waits_for_user_render(cs, tmp_path, quiet, monkeypatch):
    from clips import clip_worker_tick
    cs.write(_clip(tmp_path))
    monkeypatch.setattr(clips, "_user_render_busy", lambda: True)
    assert await clip_worker_tick(_app(cs)) == "busy"
    assert cs.read("c.mp4")["status"] == "queued"

@pytest.mark.asyncio
async def test_tick_marks_admission_error(cs, tmp_path, quiet, monkeypatch):
    from clips import clip_worker_tick
    cs.write(_clip(tmp_path))
    monkeypatch.setattr(clips, "_free_bytes", lambda store: 1)
    assert await clip_worker_tick(_app(cs)) == "admission_error"
    side = cs.read("c.mp4")
    assert side["status"] == "error" and "free space" in side["error"].lower()

@pytest.mark.asyncio
async def test_tick_encodes_oldest_queued(cs, tmp_path, quiet):
    from clips import clip_worker_tick
    cs.write({**_clip(tmp_path, "later.mp4"), "requested_at": 2.0})
    cs.write({**_clip(tmp_path, "first.mp4"), "requested_at": 1.0})
    assert await clip_worker_tick(_app(cs)) == "ready"
    assert cs.read("first.mp4")["status"] == "ready" and cs.read("first.mp4")["started_at"] > 0
    assert cs.read("later.mp4")["status"] == "queued"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_clips_worker.py -v`
Expected: FAIL — `ImportError: cannot import name 'probe_encoder' from 'clips'`

- [ ] **Step 3: Implement the encoder and worker section of `clips.py`**

Per the Interfaces block. `encode_one` must wait on the process in a loop (`proc.wait()` raced against `asyncio.sleep(poll_sec)` via `asyncio.wait_for`), so the onroad and deleted checks run between polls. Read `ENCODERD_BIN`, `FFMPEG_BIN`, `is_onroad`, `_user_render_busy` and `_free_bytes` as module globals at call time (tests monkeypatch them). The ffmpeg form writes the concat list to stdin exactly as upstream's `VideoClips._encode` does (`ffconcat version 1.0`, one `file 'file:<path>'` + `option framerate 20` + `duration 60` block per input). Keep `encode_one` and `clip_worker_tick` free of aiohttp imports so the tests can run them with a plain dict as `app`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_clips_worker.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add clips.py tests/fake_encoderd.py tests/test_clips_worker.py
git commit -m "feat(clips): encoderd/ffmpeg backends and offroad clip worker with SIGKILL cleanup"
```

---

### Task 5: REST handlers, onroad guard, server wiring, API docs

**Files:**
- Create: `handlers/clips.py`
- Modify: `handlers/__init__.py` (export block), `handlers/middleware.py:30-60`, `server.py` (imports, `_startup`, `_shutdown`, routes after the screenshots block), `API.md` (new section after "HUD Video Rendering")
- Test: `tests/test_handlers.py` (extend `BLOCKED`, `BLOCKED_READS`), `tests/test_clips_handlers.py`

**Interfaces:**
- Consumes: everything in `clips.py`; `handler_helpers.get_route_or_404(request) -> (route_name, route, store)`, `handler_helpers.parse_json(request)`; `store.preserve_route(local_id)`.
- Produces, in `handlers/clips.py`:
  - `handle_clips_create(request)` — `POST /v1/route/{routeName}/clips`, body `{start_sec, end_sec, cameras[], bitrate_mbps?, name?}`; on success writes each sidecar, calls `store.preserve_route(local_id)`, sets the `user.preserve` xattr (`os.setxattr(seg_dir, b"user.preserve", b"1")`, errors ignored) on every segment dir in the window, returns **202** `{"clips": [sidecar, ...], "clamped": bool}`; `ClipRequestError` → its `.status` with `{"error": msg}`; when `request.app["clips_encoder"] == "none"` → 503 `{"error": "Clip encoder not available on this device"}`
  - `handle_clips_for_route(request)` — `GET /v1/route/{routeName}/clips` → `{"clips": [...this route's, newest first], "cameras": available_ranges(route), "supported": bool, "free_bytes": int}`
  - `handle_clips_list(request)` — `GET /v1/clips` → `{"clips": [...all, newest first, each with "eta_sec"], "total_bytes": int, "supported": bool, "free_bytes": int}`
  - `handle_clip_serve(request)` — `GET /v1/clips/{filename}` → `web.FileResponse` of the MP4 with `Content-Type: video/mp4`, `Content-Disposition: attachment; filename="<name>"`; 404 unless sidecar status is `ready` and the file exists; 400 on `validate_filename` failure
  - `handle_clip_delete(request)` — `DELETE /v1/clips/{filename}` → `{"status": "ok", "filename": name}` or 404
- Modify `handlers/middleware.py`: add `r"|route/[^/]+/clips"` and `r"|clips/"` to `_ONROAD_FORBIDDEN`; add a second read regex entry so `GET /v1/clips/<filename>` is refused: extend `_ONROAD_FORBIDDEN_READ` to `^/v1/(route/[^/]+/(…existing…)|clips/[^/]+)` (keep `GET /v1/clips` and `GET /v1/route/{r}/clips` unguarded — the route-level `clips` path is excluded because the read regex only matches the existing media sub-paths).
- `server.py` `_startup`: `app["clip_store"] = ClipStore(COD_CLIPS_DIR)`; `app["clips_encoder"] = init_encoder()`; log the result; `recover_on_startup(app["clip_store"])`; `app["clip_task"] = asyncio.create_task(clip_worker(app))`. `_shutdown` cancels `clip_task`.

- [ ] **Step 1: Write the failing tests**

Extend `tests/test_handlers.py`:
```python
# in BLOCKED add:
        ("post", "/v1/route/000003e5--c904a8df49/clips"),
        ("delete", "/v1/clips/evidence.mp4"),
# in BLOCKED_READS add:
        "/v1/clips/evidence.mp4",
# new test in the same class:
    @pytest.mark.asyncio
    async def test_clip_listings_allowed_onroad(self, client):
        c = await client
        with patch("handlers.middleware.read_param", return_value="1"):
            for path in ("/v1/clips", "/v1/route/00000100--def456/clips"):
                resp = await c.get(path)
                assert resp.status != 409, path
```

```python
# tests/test_clips_handlers.py
import json, pytest
from pathlib import Path
import clips

@pytest.fixture
def clip_app(app, tmp_path, monkeypatch):
    from clips import ClipStore
    app["clip_store"] = ClipStore(str(tmp_path / "cod_clips"))
    app["clips_encoder"] = "encoderd"
    monkeypatch.setattr(clips, "segment_frame_count", lambda path: 1200)
    monkeypatch.setattr(clips, "_free_bytes", lambda store: 100 * 1024**3)
    monkeypatch.setattr(clips, "provenance", lambda *a, **k: {"wall_start_epoch": None, "gps": None})
    return app

@pytest.fixture
def clip_client(aiohttp_client, clip_app):
    return aiohttp_client(clip_app)

ROUTE = "00000100--def456"   # fake_data_dir route with fcamera.hevc in segment 0

@pytest.mark.asyncio
async def test_create_returns_202_and_preserves_route(clip_client, clip_app):
    c = await clip_client
    resp = await c.post(f"/v1/route/{ROUTE}/clips",
                        json={"start_sec": 5, "end_sec": 25, "cameras": ["road"]})
    assert resp.status == 202
    data = await resp.json()
    assert data["clips"][0]["status"] == "queued" and data["clips"][0]["camera"] == "road"
    assert ROUTE in clip_app["store"]._preserved

@pytest.mark.asyncio
async def test_create_rejects_missing_camera_with_409(clip_client):
    c = await clip_client
    resp = await c.post(f"/v1/route/{ROUTE}/clips",
                        json={"start_sec": 5, "end_sec": 25, "cameras": ["wide"]})
    assert resp.status == 409
    assert "wide" in (await resp.json())["error"]

@pytest.mark.asyncio
async def test_create_503_when_no_encoder(clip_client, clip_app):
    clip_app["clips_encoder"] = "none"
    c = await clip_client
    resp = await c.post(f"/v1/route/{ROUTE}/clips", json={"start_sec": 0, "end_sec": 10, "cameras": ["road"]})
    assert resp.status == 503

@pytest.mark.asyncio
async def test_list_and_route_list(clip_client, clip_app):
    c = await clip_client
    await c.post(f"/v1/route/{ROUTE}/clips", json={"start_sec": 0, "end_sec": 10, "cameras": ["road"]})
    all_ = await (await c.get("/v1/clips")).json()
    assert all_["supported"] is True and len(all_["clips"]) == 1 and "eta_sec" in all_["clips"][0]
    assert "free_bytes" in all_ and all_["total_bytes"] == 0
    mine = await (await c.get(f"/v1/route/{ROUTE}/clips")).json()
    assert len(mine["clips"]) == 1 and mine["cameras"]["road"] == [[0, 60]]
    assert mine["supported"] is True and "free_bytes" in mine

@pytest.mark.asyncio
async def test_serve_only_when_ready(clip_client, clip_app):
    c = await clip_client
    cs = clip_app["clip_store"]
    cs.write({"filename": "q.mp4", "status": "queued", "requested_at": 1.0, "size": None})
    assert (await c.get("/v1/clips/q.mp4")).status == 404
    cs.mp4_path("r.mp4").parent.mkdir(parents=True, exist_ok=True)
    cs.mp4_path("r.mp4").write_bytes(b"\x00" * 16)
    cs.write({"filename": "r.mp4", "status": "ready", "requested_at": 2.0, "size": 16})
    resp = await c.get("/v1/clips/r.mp4")
    assert resp.status == 200 and resp.headers["Content-Type"] == "video/mp4"
    assert 'filename="r.mp4"' in resp.headers["Content-Disposition"]
    assert (await c.get("/v1/clips/..%2Fetc.mp4")).status in (400, 404)

@pytest.mark.asyncio
async def test_delete(clip_client, clip_app):
    c = await clip_client
    clip_app["clip_store"].write({"filename": "d.mp4", "status": "queued", "requested_at": 1.0, "size": None})
    assert (await c.delete("/v1/clips/d.mp4")).status == 200
    assert (await c.delete("/v1/clips/d.mp4")).status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_clips_handlers.py tests/test_handlers.py -k "clip or onroad or Onroad" -v`
Expected: FAIL — 404s on the new paths (no routes registered) and the guard tests fail with status != 409.

- [ ] **Step 3: Implement handlers, guard, wiring, docs**

`handlers/clips.py` per Interfaces. `handle_clips_create` runs `build_requests` in `store._executor` via `run_in_executor` (it probes `ffprobe`). Register routes in `server.py` right after the screenshots block:

```python
    # Dashcam clips (hardware-transcoded H.264 exports; 0.11.2+)
    app.router.add_post("/v1/route/{routeName}/clips", handle_clips_create)
    app.router.add_get("/v1/route/{routeName}/clips", handle_clips_for_route)
    app.router.add_get("/v1/clips", handle_clips_list)
    app.router.add_get("/v1/clips/{filename}", handle_clip_serve)
    app.router.add_delete("/v1/clips/{filename}", handle_clip_delete)
```

Note `conftest.app` clears `on_startup`, so the handler tests set `clip_store`/`clips_encoder` themselves (the `clip_app` fixture). `API.md`: add a "## Dashcam Clips" section documenting the five endpoints, the body fields with their limits (1–120 s, cameras, bitrates, name), the sidecar fields, status values, and the onroad rules, in the same table style as the HUD section.

- [ ] **Step 4: Run the full backend suite**

Run: `python -m pytest tests/ -v`
Expected: all passed (including the pre-existing suite)

- [ ] **Step 5: Commit**

```bash
git add handlers/clips.py handlers/__init__.py handlers/middleware.py server.py API.md tests/test_handlers.py tests/test_clips_handlers.py
git commit -m "feat(clips): REST endpoints, onroad guard, worker wiring, API docs"
```

---

### Task 6: Frontend API wrappers

**Files:**
- Modify: `frontend/src/lib/api.js` (new block after the screenshots block, ~line 583)
- Test: `frontend/src/lib/__tests__/api.test.js`

**Interfaces:**
- Produces, in `api.js`:
  - `createClip(routeName, { startSec, endSec, cameras, bitrateMbps, name }) -> Promise<{clips, clamped}>` — POST JSON `{start_sec, end_sec, cameras, bitrate_mbps, name}` (omit `name` when empty); on `!res.ok` throw `Error((await res.json()).error || \`createClip: ${res.status}\`)`
  - `fetchRouteClips(routeName) -> Promise<{clips, cameras, supported, free_bytes}>`
  - `fetchClips() -> Promise<{clips, total_bytes, supported, free_bytes}>`
  - `clipUrl(filename) -> string` = `/v1/clips/${encodeURIComponent(filename)}`
  - `deleteClip(filename) -> Promise<object>`
  - `CLIP_MAX_SEC = 120`, `CLIP_BOOKMARK_PAD_SEC = 30`, `CLIP_BITRATES = [5, 8, 12]`, `CLIP_DEFAULT_BITRATE = 8`, `clipEstimateMB(durationSec, bitrateMbps) -> number` = `durationSec * bitrateMbps / 8`

- [ ] **Step 1: Write the failing tests**

```js
// frontend/src/lib/__tests__/api.test.js (append)
import { clipUrl, clipEstimateMB, CLIP_MAX_SEC, CLIP_BOOKMARK_PAD_SEC, CLIP_BITRATES, CLIP_DEFAULT_BITRATE } from '../api.js'

describe('clips', () => {
  it('clipUrl encodes the filename', () => {
    expect(clipUrl('a b.mp4')).toBe('/v1/clips/a%20b.mp4')
  })
  it('estimate is duration × bitrate / 8 in MB', () => {
    expect(clipEstimateMB(120, 8)).toBe(120)
    expect(clipEstimateMB(60, 12)).toBe(90)
  })
  it('constants match the spec', () => {
    expect(CLIP_MAX_SEC).toBe(120)
    expect(CLIP_BOOKMARK_PAD_SEC).toBe(30)
    expect(CLIP_BITRATES).toEqual([5, 8, 12])
    expect(CLIP_DEFAULT_BITRATE).toBe(8)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- api`
Expected: FAIL — `clipUrl` is not exported

- [ ] **Step 3: Implement the wrappers in `api.js`**

Follow the screenshots block's style (`fetch`, `if (!res.ok) throw`, `res.json()`); `createClip` surfaces the server's `error` message so the dialog can show "Not enough free space…" verbatim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/lib/__tests__/api.test.js
git commit -m "feat(clips): frontend API wrappers and constants"
```

---

### Task 7: ClipDialog and its two entry points on the route page

**Files:**
- Create: `frontend/src/lib/components/ClipDialog.svelte`
- Modify: `frontend/src/lib/components/VideoPlayer.svelte:33-35,498-512` (new `onClip` prop and button beside HUD Download), `frontend/src/lib/pages/RouteDetailPage.svelte` (imports, state, handlers, `<VideoPlayer onClip=…>`, bookmark row button, `<ClipDialog>` mount)
- Test: manual check in `npm run dev` against `python server.py --data-dir … --port 8082` (no component test framework in the repo beyond vitest unit tests; the dialog's logic lives in `api.js`, already tested)

**Interfaces:**
- Consumes: `createClip`, `fetchRouteClips`, `clipEstimateMB`, `CLIP_*` (Task 6); `RouteDetailPage` state `selectionStart`, `selectionEnd`, `duration`, `currentTime`, `metaBookmarks[i].time_sec`, `route.local_id`, `route.maxqlog`.
- Produces: `ClipDialog` props `{ open = $bindable(false), routeName, startSec, endSec, routeDurationSec, onClose, onCreated(clips) }`. Behaviour:
  - Shows the window as `MM:SS – MM:SS (N s)`; if `endSec - startSec > CLIP_MAX_SEC` shows "Window is longer than 2 minutes" with two buttons: **First 2 min** (`end = start + 120`) and **Around playhead** (`[currentTime − 60, currentTime + 60]` clamped to `[0, routeDurationSec]`); Create is disabled until one is picked. If the window is under 1 s, Create is disabled with "Select at least 1 second".
  - Camera checkboxes Road (checked), Wide (checked), Driver (unchecked); a camera whose `cameras[<key>]` ranges from `fetchRouteClips` do not cover the window is shown disabled with "not recorded for this window".
  - Quality radio 5/8/12 Mbps, default 8; estimate line `~{MB} MB per camera · {free} GB free` (`free_bytes` from `fetchRouteClips`).
  - When `supported === false`: notice "Clip encoding needs openpilot 0.11.2 on this device" and Create disabled.
  - Optional name input, placeholder = the server's default pattern described in words ("route date, camera, window").
  - Create → `createClip`; on success `onCreated(clips)`, close; error text shown inline (red), dialog stays open.
- `VideoPlayer`: new prop `onClip`; when present, render a button identical in style to HUD Download, title "Dashcam clip", label **Clip**, placed immediately after the HUD Download button.
- `RouteDetailPage`: `let clipOpen = $state(false)`, `let clipStart = $state(0)`, `let clipEnd = $state(0)`, `let clipToast = $state(null)`; `openClipFromSelection()` uses `selectionStart || 0` and `selectionEnd > 0 ? selectionEnd : duration || (route.maxqlog + 1) * 60`; `openClipFromBookmark(i)` uses `max(0, time_sec − 30)` and `min(routeDuration, time_sec + 30)`; `onCreated` sets `clipToast = "Clip queued — see Clips"` with a link to `/clips`, auto-hiding after 6 s. Bookmark rows get a third icon button (film/scissors glyph) titled "Clip 30 s around this bookmark", before the frame-export button.

- [ ] **Step 1: Build `ClipDialog.svelte`**

Copy `DownloadDialog.svelte`'s `<dialog>` scaffold (`showModal` effect, `handleClose`, Tailwind classes) and implement the behaviour above. On open, call `fetchRouteClips(routeName)` once to fill camera availability, `supported` and free space.

- [ ] **Step 2: Add the `onClip` button to `VideoPlayer.svelte`**

After the HUD Download `{#if onHudDownload}` block, add `{#if onClip}` with the same classes, title "Dashcam clip", label "Clip".

- [ ] **Step 3: Wire `RouteDetailPage.svelte`**

Import `ClipDialog`; add the state and the two open functions; pass `onClip={!enriching && !hudMode ? openClipFromSelection : undefined}` to `<VideoPlayer>`; add the bookmark-row button calling `openClipFromBookmark(i)`; mount `<ClipDialog bind:open={clipOpen} routeName={route.local_id} startSec={clipStart} endSec={clipEnd} routeDurationSec={…} onCreated={…} />` next to where `RouteActions` renders its dialogs; render `clipToast` above the video controls.

- [ ] **Step 4: Verify in the browser**

Run: backend `python server.py --data-dir <local data dir> --port 8082` and `cd frontend && npm run dev`. Open a route, drag a selection, click **Clip**: dialog shows window, cameras, estimate; a >2 min selection shows the two shortening buttons; Create on a laptop without `encoderd` returns the 503 message inline (expected: "Clip encoder not available on this device" unless `ffmpeg` is installed, in which case the clip is queued). Click the bookmark clip icon: dialog opens with a 60 s window.
Expected: no console errors; `npm run build` succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/ClipDialog.svelte frontend/src/lib/components/VideoPlayer.svelte frontend/src/lib/pages/RouteDetailPage.svelte
git commit -m "feat(clips): Clip dialog from the timeline selection and from bookmarks"
```

---

### Task 8: Clips page and navigation

**Files:**
- Create: `frontend/src/lib/pages/ClipsPage.svelte`
- Modify: `frontend/src/App.svelte` (import, `parsePage` → `'clips'`, `showClips()`, nav button "Clips" after "Captures", `{:else if page === 'clips'}<ClipsPage />`)
- Modify: `README.md` (Features list: one bullet for dashcam clips), `DESIGN.md` (project structure entries for `clips.py` and `handlers/clips.py`; a short "Dashcam clips" subsection after "Screen-capture extraction")

**Interfaces:**
- Consumes: `fetchClips`, `clipUrl`, `deleteClip` (Task 6); `formatBytes`, `formatVideoTime` from `format.js`; `createPoll` from `utils/poll.js`.
- Produces: `ClipsPage.svelte` — header "Clips (N) · total size"; when `supported === false`, a notice "Clip encoding needs openpilot 0.11.2 on this device"; list newest first, each row: name, route link (`/{dongleId}/{local_id}/{start}/{end}` via `selectedRoute`), camera, `MM:SS–MM:SS`, size; status chip: `queued`, `encoding… {elapsed} of ~{eta}` (elapsed from `started_at`), `error: {error}`; ready rows have Play (toggles an inline `<video controls src={clipUrl(f)}>`), Download (`<a href={clipUrl(f)} download={f}>`), Delete (confirm). Polls `fetchClips` every 2 s while any clip is `queued`/`encoding`, else every 15 s.

- [ ] **Step 1: Build `ClipsPage.svelte`**

Model the layout on `ScreenshotsPage.svelte` (header, loading/error/empty states, `btn-ghost` buttons) but as a list, not a grid.

- [ ] **Step 2: Register the page in `App.svelte`**

`parsePage`: `if (parts[0] === 'clips') return 'clips'`; `showClips()` mirrors `showScreenshots()`; nav button labelled **Clips** right after **Captures**; render branch `{:else if page === 'clips'}<ClipsPage />`.

- [ ] **Step 3: Update README.md and DESIGN.md**

README Features: "**Dashcam clips** — hardware-transcoded H.264 clips of any ≤2 min window, for sharing or insurance claims (openpilot 0.11.2+)". DESIGN.md: add `clips.py` and `handlers/clips.py` to the tree, and a subsection stating the sidecar-is-truth rule, the offroad SIGKILL/requeue behaviour, and that `cod_clips` is never reclaimed.

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run build`
Expected: tests pass, build succeeds. In the dev server, `/clips` lists sidecars written by the handler tests' flow (queue one via the dialog on a laptop with ffmpeg, watch it go queued → encoding → ready, play and download it).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/pages/ClipsPage.svelte frontend/src/App.svelte README.md DESIGN.md
git commit -m "feat(clips): Clips page, navigation, docs"
```

---

### Task 9: On-device checklist (blocking before the first v0.11.2 release)

**Files:**
- Modify: `clips.py` (`ENCODE_RATE_PLACEHOLDER` → measured value, renamed `ENCODE_RATE_SEC_PER_SEC`)
- Modify: `docs/superpowers/specs/2026-10-27-dashcam-clips-design.md` (tick the Done criteria with dates)

**Interfaces:** none new.

Prerequisite: the device runs catpilot `rebase/v0.11.2` (Phase 10a–10c) and COD is deployed with `./deploy_dev.sh`.

- [ ] **Step 1: Confirm the probe**

Run on the device: `/data/openpilot/openpilot/system/loggerd/encoderd --clip; echo "exit $?"`
Expected: `usage: encoderd --clip OUTPUT START DURATION …` on stderr, `exit 2`. `GET /v1/clips` returns `"supported": true`.

- [ ] **Step 2: Measure the encode rate**

Queue a 120 s road clip at 8 Mbps from the Clips dialog; read `encode_sec` from `GET /v1/clips`. Set `ENCODE_RATE_SEC_PER_SEC = round(120 / encode_sec, 1)` in `clips.py`.

- [ ] **Step 3: Kill paths**

(a) Queue a 120 s clip, then `DELETE /v1/clips/<name>` while `encoding`: `ls -la /data/media/0/cod_clips/` shows no `.<name>.mp4`, no `.lock`, no sidecar. (b) Queue a clip, turn ignition on mid-encode: sidecar returns to `queued`, no temp/lock; turn ignition off: it encodes and becomes `ready`.

- [ ] **Step 4: Playback and coexistence**

Download a 12 Mbps clip to an iPhone and an Android phone from the Clips page; both play. Set `IsLiveStreaming` (start a connect livestream or `echo -n 1 > /data/params/d/IsLiveStreaming`) and queue a clip: it reaches `ready`.

- [ ] **Step 5: Short final segment**

On the drive that just ended, request a window reaching into the last segment: the response has `"clamped": true` and the clip's `end_sec` matches the footage.

- [ ] **Step 6: Commit**

```bash
git add clips.py docs/superpowers/specs/2026-10-27-dashcam-clips-design.md
git commit -m "chore(clips): measured encode rate; on-device checklist passed"
```

---

## Deferred follow-ups (recorded 2026-10-27 after execution)

Tasks 1–8 are implemented and reviewed on `v0.11.2` (commits `7b17b86..58a4891`). Task 9 above has **not** run: it needs the rebased catpilot on a device and remains the gate for the first `v0.11.2` release. Items below were found by the reviews and deliberately left for after Task 9.

- **Retry action** on `error` rows (rewrite the sidecar as `queued`; inputs are already recorded).
- **Requeue cap**: the per-tick stranded-clip requeue has no attempt limit, so a persistent post-encode disk error re-encodes on every parked tick.
- **Stale route view** can over-clamp a window within 2 min of a rescan (`CACHE_TTL`); check `resolve_segment_path(..., last+1, ...)` before clamping.
- **Delete then re-create the same name within 1 s** lets the old encode overwrite the new sidecar; tag sidecars with an encode id.
- **Lone UTF-16 surrogate in a name** passes validation and 500s on serve; add `\ud800-\udfff` to the forbidden set.
- **NaN/Infinity** in a sidecar field other than the estimate reaches the listing JSON as literal `NaN`.
- `kill_stray_encoders` also kills a comma-connect `encoderd --clip` if one is running at COD start; document or narrow the match.
- The encoder probe runs at every COD start even onroad (millisecond usage print on 0.11.2); gate on `not is_onroad()`.
- Add `COD_CLIPS_DIR` to the health checks.
- Admission does not count already-queued clips (the worker re-check catches it); fsync on sidecar writes; overlapping Clips-page polls; failed-delete error cleared by the next poll; `createClip` error body parse unguarded; no mocked-fetch tests for the clip wrappers.
- Task 9 step 3: also `ls -la` for any `.encoderd-*.tmp` or faststart side file, and read a failing clip's `error` (it now carries the encoder's stderr tail).
