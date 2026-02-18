"""Tests for route_helpers.py — pure functions and mock request objects."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from route_helpers import _base_url, _clean_route, _resolve_local_id, _route_engagement, _set_route_url


# ─── _clean_route ────────────────────────────────────────────────────

class TestCleanRoute:
    def test_removes_underscore_keys(self):
        route = {"fullname": "a/b", "distance": 1.0, "_local_id": "x", "_segments": []}
        cleaned = _clean_route(route)
        assert "fullname" in cleaned
        assert "distance" in cleaned
        assert "_local_id" not in cleaned
        assert "_segments" not in cleaned

    def test_preserves_public_keys(self):
        route = {"a": 1, "b": 2, "c": 3}
        assert _clean_route(route) == {"a": 1, "b": 2, "c": 3}


# ─── _base_url ───────────────────────────────────────────────────────

def _make_request(host="localhost:8082", scheme="http", forwarded_proto=None, forwarded_host=None):
    req = MagicMock()
    req.host = host
    req.scheme = scheme
    headers = {}
    if forwarded_proto:
        headers["X-Forwarded-Proto"] = forwarded_proto
    if forwarded_host:
        headers["X-Forwarded-Host"] = forwarded_host
    req.headers = headers
    return req


class TestBaseUrl:
    def test_default(self):
        req = _make_request()
        assert _base_url(req) == "http://localhost:8082"

    def test_with_forwarded_headers(self):
        req = _make_request(forwarded_proto="https", forwarded_host="connect.example.com")
        assert _base_url(req) == "https://connect.example.com"


# ─── _set_route_url ─────────────────────────────────────────────────

class TestSetRouteUrl:
    def test_sets_url(self):
        route = {"dongle_id": "abc", "fullname": "abc/2025-01-01--12-00-00", "url": None}
        req = _make_request()
        result = _set_route_url(route, req)
        assert result["url"] == "http://localhost:8082/connectdata/abc/2025-01-01--12-00-00"
        # Original dict should not be modified
        assert route["url"] is None

    def test_does_not_mutate_original(self):
        route = {"dongle_id": "x", "fullname": "x/date", "url": None, "extra": 1}
        req = _make_request()
        result = _set_route_url(route, req)
        assert result is not route
        assert result["extra"] == 1


# ─── _resolve_local_id ──────────────────────────────────────────────

class TestResolveLocalId:
    def test_found(self, mock_store):
        from aiohttp import web
        routes = mock_store.scan()
        for fullname in routes:
            req = MagicMock()
            req.match_info = {"routeName": fullname.replace("/", "|")}
            lid = _resolve_local_id(mock_store, req)
            assert lid is not None
            break

    def test_not_found(self, mock_store):
        from aiohttp import web
        req = MagicMock()
        req.match_info = {"routeName": "nonexistent|route"}
        with pytest.raises(web.HTTPNotFound):
            _resolve_local_id(mock_store, req)

    def test_pipe_replacement(self, mock_store):
        """routeName uses pipes in URL, converted to slashes."""
        routes = mock_store.scan()
        for fullname in routes:
            req = MagicMock()
            piped = fullname.replace("/", "|")
            req.match_info = {"routeName": piped}
            lid = _resolve_local_id(mock_store, req)
            assert lid is not None
            break


# ─── _route_engagement ───────────────────────────────────────────────

class TestRouteEngagement:
    def test_no_events(self, mock_store):
        route = {"_local_id": "00000042--abc123", "_segments": [], "maxqlog": 0}
        engaged_ms, total_ms = _route_engagement(mock_store, route)
        assert engaged_ms == 0
        assert total_ms > 0

    def test_with_events(self, mock_store, sample_events_json):
        # Write events.json to a segment dir
        routes = mock_store.scan()
        for r in routes.values():
            if r["_local_id"] == "00000042--abc123" and r["_segments"]:
                seg_path = Path(r["_segments"][0]["path"])
                (seg_path / "events.json").write_text(json.dumps(sample_events_json))
                engaged_ms, total_ms = _route_engagement(mock_store, r)
                # Engagement: 10000ms to 40000ms = 30000ms
                assert engaged_ms == 30000.0
                assert total_ms > 0
                break

    def test_open_span_closes_at_segment_end(self, mock_store):
        """Engagement that starts but doesn't end should close at segment end."""
        events = [
            {
                "type": "state",
                "time": 1700000010.0,
                "offset_millis": 10000,
                "route_offset_millis": 10000,
                "data": {"state": "enabled", "enabled": True, "alertStatus": 0},
            },
            # No disengage event — should close at segment end (60000ms)
        ]
        routes = mock_store.scan()
        for r in routes.values():
            if r["_local_id"] == "00000042--abc123" and r["_segments"]:
                seg_path = Path(r["_segments"][0]["path"])
                (seg_path / "events.json").write_text(json.dumps(events))
                engaged_ms, total_ms = _route_engagement(mock_store, r)
                # Segment 0 ends at 60000ms, engaged from 10000ms
                assert engaged_ms == 50000.0
                break
