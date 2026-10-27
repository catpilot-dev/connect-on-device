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

# ── ffmpeg form, metadata, cancellation ──────────────────────────────

def test_encoder_command_ffmpeg_shape_and_metadata(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(clips, "FFMPEG_BIN", "/opt/ffmpeg")
    clip = {**_clip(tmp_path), "provenance": {"gps": None}}
    cmd = encoder_command(clip, "ffmpeg", "/out/.c.mp4")
    assert cmd[0] == "/opt/ffmpeg" and cmd[-1] == "/out/.c.mp4"
    assert cmd[cmd.index("-i") + 1] == "pipe:0"
    assert cmd[cmd.index("-ss") + 1] == "5.0" and cmd[cmd.index("-t") + 1] == "10"
    assert cmd[cmd.index("-b:v") + 1] == "8M" and "libx264" in cmd
    assert cmd[cmd.index("-movflags") + 1] == "+faststart+use_metadata_tags"
    tag, settings = cmd[cmd.index("-metadata") + 1].split("=", 1)
    assert tag == "ai.comma.clip.settings"
    meta = json.loads(settings)
    assert meta["requested_at"] == 1.0 and meta["provenance"] == {"gps": None}
    assert "inputs" not in meta and "status" not in meta
    enc = encoder_command(clip, "encoderd", "/out/.c.mp4")
    assert json.loads(enc[enc.index("--metadata") + 1]) == meta

def test_ffconcat_list_matches_upstream():
    assert clips._ffconcat_list(["/a/b.hevc", "/c/it's.hevc"]) == (
        "ffconcat version 1.0\n"
        "file 'file:/a/b.hevc'\noption framerate 20\nduration 60\n"
        "file 'file:/c/it'\\''s.hevc'\noption framerate 20\nduration 60\n")

@pytest.mark.asyncio
async def test_cancel_kills_encoder_and_cleans(cs, tmp_path, monkeypatch):
    import signal
    monkeypatch.setattr(clips, "ENCODERD_BIN", FAKE)
    monkeypatch.setenv("FAKE_ENCODERD_SLEEP", "5"); monkeypatch.setenv("FAKE_ENCODERD_LEAVE_LOCK", "1")
    procs, spawn = [], asyncio.create_subprocess_exec
    async def spy(*args, **kwargs):
        procs.append(await spawn(*args, **kwargs))
        return procs[-1]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    clip = _clip(tmp_path); cs.write(clip)
    task = asyncio.create_task(encode_one(cs, clip, "encoderd", poll_sec=0.05, onroad=lambda: False))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert procs[0].returncode == -signal.SIGKILL      # shutdown never leaves an orphan encoder running
    assert not cs.temp_path("c.mp4").exists() and not cs.lock_path("c.mp4").exists()
