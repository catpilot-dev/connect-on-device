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
    assert "inputs" not in data["clips"][0]
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
    assert "inputs" not in all_["clips"][0]
    assert "free_bytes" in all_ and all_["total_bytes"] == 0
    mine = await (await c.get(f"/v1/route/{ROUTE}/clips")).json()
    assert len(mine["clips"]) == 1 and mine["cameras"]["road"] == [[0, 60]]
    assert "eta_sec" in mine["clips"][0] and "inputs" not in mine["clips"][0]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", ["a%0Ab.mp4", "a%0D%0Ab.mp4", "a%00b.mp4", "say%20%22hi%22.mp4"])
async def test_serve_and_delete_reject_control_chars_and_quotes(clip_client, encoded):
    from yarl import URL
    c = await clip_client
    url = URL(f"/v1/clips/{encoded}", encoded=True)
    assert (await c.get(url)).status == 400
    assert (await c.delete(url)).status == 400

@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["line\nbreak", "cr\rlf", 'say "hi"'])
async def test_create_rejects_control_chars_and_quotes_in_name(clip_client, clip_app, name):
    c = await clip_client
    resp = await c.post(f"/v1/route/{ROUTE}/clips",
                        json={"start_sec": 0, "end_sec": 10, "cameras": ["road"], "name": name})
    assert resp.status == 400
    assert clip_app["clip_store"].list() == []

@pytest.mark.asyncio
async def test_serve_non_ascii_name_uses_rfc5987_filename(clip_client, clip_app):
    from urllib.parse import quote
    c = await clip_client
    cs = clip_app["clip_store"]
    name = "事故 現場.mp4"
    cs.mp4_path(name).parent.mkdir(parents=True, exist_ok=True)
    cs.mp4_path(name).write_bytes(b"\x00" * 16)
    cs.write({"filename": name, "status": "ready", "requested_at": 2.0, "size": 16})
    resp = await c.get("/v1/clips/" + quote(name))
    assert resp.status == 200
    disposition = resp.headers["Content-Disposition"]
    assert disposition == (
        'attachment; filename="__ __.mp4"; '
        "filename*=UTF-8''%E4%BA%8B%E6%95%85%20%E7%8F%BE%E5%A0%B4.mp4")
    assert disposition.isascii()


@pytest.mark.asyncio
@pytest.mark.parametrize("taken_by", ["sidecar", "mp4"])
async def test_create_rechecks_the_name_on_the_loop_before_writing(clip_client, clip_app, monkeypatch, taken_by):
    # Another POST claimed the name after build_requests chose it in the executor.
    def fixed(route, store, body, clip_store, now):
        return [{"filename": "crash.mp4", "status": "queued", "requested_at": now, "camera": "road",
                 "local_id": route["_local_id"], "duration_sec": 10, "inputs": [],
                 "clamped": False, "size": None, "error": None}]
    monkeypatch.setattr(clips, "build_requests", fixed)
    cs = clip_app["clip_store"]
    original = {"filename": "crash.mp4", "status": "queued", "requested_at": 1.0, "size": None,
                "camera": "wide"}
    if taken_by == "sidecar":
        cs.write(original)
    else:
        cs.mp4_path("crash.mp4").parent.mkdir(parents=True, exist_ok=True)
        cs.mp4_path("crash.mp4").write_bytes(b"theirs")
    c = await clip_client
    resp = await c.post(f"/v1/route/{ROUTE}/clips",
                        json={"start_sec": 0, "end_sec": 10, "cameras": ["road"], "name": "crash"})
    assert resp.status == 202
    assert [x["filename"] for x in (await resp.json())["clips"]] == ["crash_2.mp4"]
    assert cs.read("crash_2.mp4")["camera"] == "road"
    if taken_by == "sidecar":
        assert cs.read("crash.mp4") == original
    else:
        assert cs.read("crash.mp4") is None and cs.mp4_path("crash.mp4").read_bytes() == b"theirs"
