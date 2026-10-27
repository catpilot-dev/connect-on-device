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
