# tests/test_clips_store.py
import os

def test_config_exposes_clip_paths():
    import config
    assert config.COD_CLIPS_DIR == os.getenv("COD_CLIPS_DIR", "/data/media/0/cod_clips")
    assert config.ENCODERD_BIN.endswith("openpilot/system/loggerd/encoderd")

def test_version_is_0_11_2():
    from pathlib import Path
    assert Path(__file__).resolve().parent.parent.joinpath("VERSION").read_text().strip() == "0.11.2"


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

def test_missing_directory_is_tolerated_and_not_created_by_init(tmp_path):
    s = ClipStore(str(tmp_path / "nope"))
    assert not (tmp_path / "nope").exists()
    assert s.list() == [] and s.queued() == [] and s.total_bytes() == 0
    assert s.read("a.mp4") is None


@pytest.mark.parametrize("body", [
    "null", "[]",
    json.dumps({"status": "ready", "requested_at": 1.0}),
    json.dumps({"filename": "bad.mp4", "status": "queued", "requested_at": None}),
    json.dumps({"filename": "bad.mp4", "status": "queued", "requested_at": "soon"}),
])
def test_list_skips_malformed_sidecars(store, body):
    store.write(_clip("good.mp4", requested_at=5.0))
    store.dir.joinpath("bad.json").write_text(body)
    assert [c["filename"] for c in store.list()] == ["good.mp4"]
    assert [c["filename"] for c in store.queued()] == ["good.mp4"]
    assert store.total_bytes() == 0
