"""Tests for handler_helpers.py — error_response, parse_json, get_route_or_404,
resolve_route_name, read_param, write_param."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handler_helpers import error_response, get_route_or_404, read_param, resolve_route_name, write_param


# ─── error_response ─────────────────────────────────────────────────

class TestErrorResponse:
    def test_default_status(self):
        resp = error_response("something broke")
        assert resp.status == 400
        body = json.loads(resp.body)
        assert body["error"] == "something broke"

    def test_custom_status(self):
        resp = error_response("not found", status=404)
        assert resp.status == 404
        body = json.loads(resp.body)
        assert body["error"] == "not found"

    def test_server_error_status(self):
        resp = error_response("internal", status=500)
        assert resp.status == 500

    def test_content_type_json(self):
        resp = error_response("err")
        assert resp.content_type == "application/json"


# ─── read_param / write_param ────────────────────────────────────────

class TestReadWriteParam:
    def test_read_missing_returns_default(self, tmp_path):
        from handler_helpers import PARAMS_DIR
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        assert read_param("Nonexistent") == ""

    def test_read_missing_custom_default(self, tmp_path):
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        assert read_param("Nonexistent", "fallback") == "fallback"

    def test_write_then_read(self, tmp_path):
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        write_param("TestKey", "hello")
        assert read_param("TestKey") == "hello"

    def test_write_strips_whitespace(self, tmp_path):
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        # Write value with newline (simulating param files)
        (tmp_path / "SpacedKey").write_text("  value  \n")
        assert read_param("SpacedKey") == "value"

    def test_write_overwrites(self, tmp_path):
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        write_param("Key", "first")
        write_param("Key", "second")
        assert read_param("Key") == "second"

    def test_write_int_value(self, tmp_path):
        import handler_helpers
        handler_helpers.PARAMS_DIR = str(tmp_path)
        write_param("IntKey", 42)
        assert read_param("IntKey") == "42"


# ─── resolve_route_name ─────────────────────────────────────────────

class TestResolveRouteName:
    def test_pipe_to_slash(self):
        req = MagicMock()
        req.match_info = {"routeName": "abc123|2025-01-01--12-00-00"}
        result = resolve_route_name(req)
        assert result == "abc123/2025-01-01--12-00-00"

    def test_no_pipe(self):
        req = MagicMock()
        req.match_info = {"routeName": "00000042--abc123"}
        result = resolve_route_name(req)
        assert result == "00000042--abc123"


# ─── get_route_or_404 ───────────────────────────────────────────────

class TestGetRouteOr404:
    def test_found(self, mock_store):
        routes = mock_store.scan()
        fullname = next(iter(routes))
        req = MagicMock()
        req.match_info = {"routeName": fullname.replace("/", "|")}
        req.app = {"store": mock_store}

        route_name, route, store = get_route_or_404(req)
        assert route is not None
        assert route["fullname"] == fullname

    def test_not_found_raises_404(self, mock_store):
        req = MagicMock()
        req.match_info = {"routeName": "nonexistent|route"}
        req.app = {"store": mock_store}

        with pytest.raises(web.HTTPNotFound):
            get_route_or_404(req)

    def test_returns_store(self, mock_store):
        routes = mock_store.scan()
        fullname = next(iter(routes))
        req = MagicMock()
        req.match_info = {"routeName": fullname.replace("/", "|")}
        req.app = {"store": mock_store}

        _, _, returned_store = get_route_or_404(req)
        assert returned_store is mock_store
