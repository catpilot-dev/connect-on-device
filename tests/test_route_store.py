"""Tests for route_store.py — pure functions and RouteStore business logic."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from route_store import RouteStore, _route_counter, CACHE_TTL


# ─── _route_counter ──────────────────────────────────────────────────

class TestRouteCounter:
    def test_normal(self):
        assert _route_counter("00000114--abc") == 114

    def test_leading_zeros(self):
        assert _route_counter("00000001--xyz") == 1

    def test_empty_string(self):
        assert _route_counter("") == 0

    def test_no_separator(self):
        assert _route_counter("noseparator") == 0

    def test_large(self):
        assert _route_counter("99999999--abc") == 99999999


# ─── _wall_time_to_route_date ────────────────────────────────────────

class TestWallTimeToRouteDate:
    def _make_store(self, tmp_path):
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                return RouteStore(str(tmp_path))

    def test_utc_conversion(self, tmp_path):
        store = self._make_store(tmp_path)
        # 2025-06-15 12:30:45 UTC
        dt = datetime(2025, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        nanos = int(dt.timestamp() * 1e9)
        result = store._wall_time_to_route_date(nanos)
        assert result == "2025-06-15--12-30-45"

    def test_with_longitude_utc_plus_8(self, tmp_path):
        store = self._make_store(tmp_path)
        # 2025-06-15 20:00:00 UTC -> 2025-06-16 04:00:00 UTC+8
        dt = datetime(2025, 6, 15, 20, 0, 0, tzinfo=timezone.utc)
        nanos = int(dt.timestamp() * 1e9)
        result = store._wall_time_to_route_date(nanos, lng=121.0)
        assert result == "2025-06-16--04-00-00"


# ─── _meta_to_internal ───────────────────────────────────────────────

class TestMetaToInternal:
    def _make_store(self, tmp_path, metadata=None):
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store = RouteStore(str(tmp_path))
                if metadata:
                    store._metadata = metadata
                return store

    def test_full_metadata(self, tmp_path):
        meta = {
            "test--route": {
                "dongle_id": "abc123",
                "creation_time": "2025-11-15T10:30:00+00:00",
                "gps_coordinates": [31.23, 121.47],
                "git_commit": "deadbeef",
                "git_branch": "main",
                "openpilot_version": "0.9.8",
                "car_fingerprint": "BMW_E90",
                "git_remote": "https://github.com/test/op.git",
                "device_type": "tici",
            }
        }
        store = self._make_store(tmp_path, meta)
        result = store._meta_to_internal("test--route")
        assert result["dongle_id"] == "abc123"
        assert result["start_lat"] == 31.23
        assert result["start_lng"] == 121.47
        assert result["car_fingerprint"] == "BMW_E90"
        assert result["git_commit"] == "deadbeef"
        assert result["device_type"] == "tici"
        assert "create_time" in result
        assert "wall_time_nanos" in result

    def test_missing_route(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store._meta_to_internal("nonexistent--route")
        assert result == {}

    def test_unknown_values_excluded(self, tmp_path):
        meta = {
            "test--route": {
                "dongle_id": "Unknown",
                "git_commit": "Unknown",
                "openpilot_version": "Unknown",
            }
        }
        store = self._make_store(tmp_path, meta)
        result = store._meta_to_internal("test--route")
        assert "dongle_id" not in result
        assert "git_commit" not in result
        assert "version" not in result


# ─── _needs_enrich ───────────────────────────────────────────────────

class TestNeedsEnrich:
    def _make_store(self, tmp_path, metadata=None):
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store = RouteStore(str(tmp_path))
                if metadata:
                    store._metadata = metadata
                return store

    def test_missing_metadata(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store._needs_enrich("unknown--route") is True

    def test_missing_car_fingerprint(self, tmp_path):
        store = self._make_store(tmp_path, {"r": {"gps_coordinates": [1, 2], "total_distance_m": 100, "device_type": "tici"}})
        assert store._needs_enrich("r") is True

    def test_agnos_build_date(self, tmp_path):
        store = self._make_store(tmp_path, {
            "r": {
                "car_fingerprint": "BMW_E90",
                "device_type": "tici",
                "gps_coordinates": [1, 2],
                "total_distance_m": 100,
                "creation_time": "2025-07-02T12:00:00+00:00",
            }
        })
        assert store._needs_enrich("r") is True

    def test_fully_enriched(self, tmp_path):
        store = self._make_store(tmp_path, {
            "r": {
                "car_fingerprint": "BMW_E90",
                "device_type": "tici",
                "gps_coordinates": [31.23, 121.47],
                "total_distance_m": 5000.0,
                "creation_time": "2025-11-15T10:30:00+08:00",
            }
        })
        assert store._needs_enrich("r") is False


# ─── scan ────────────────────────────────────────────────────────────

class TestScan:
    def test_finds_routes(self, mock_store):
        routes = mock_store.scan()
        # Should find 2 routes (42 and 100)
        assert len(routes) >= 1
        # Check _raw has the local_ids
        assert "00000042--abc123" in mock_store._raw
        assert "00000100--def456" in mock_store._raw

    def test_ignores_non_route_dirs(self, populated_data_dir):
        # Create non-route directory
        (populated_data_dir / "random_dir").mkdir()
        (populated_data_dir / "notaroute").mkdir()
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store = RouteStore(str(populated_data_dir))
                store._dongle_id = "test123"
                store.scan(force=True)
        assert "random_dir" not in store._raw
        assert "notaroute" not in store._raw

    def test_cache_hit_within_ttl(self, mock_store):
        mock_store.scan(force=True)
        first_scan_time = mock_store._last_scan
        # Scan again without force — should use cache
        mock_store.scan()
        assert mock_store._last_scan == first_scan_time

    def test_force_bypasses_cache(self, mock_store):
        mock_store.scan(force=True)
        first_scan_time = mock_store._last_scan
        import time
        time.sleep(0.01)
        mock_store.scan(force=True)
        assert mock_store._last_scan > first_scan_time


# ─── _build_route ────────────────────────────────────────────────────

class TestBuildRoute:
    def test_full_build(self, mock_store):
        routes = mock_store.scan()
        # Find the route with counter 100
        route_100 = None
        for r in routes.values():
            if r["_local_id"] == "00000100--def456":
                route_100 = r
                break
        assert route_100 is not None
        assert route_100["dongle_id"] == "test123"
        assert route_100["platform"] == "BMW_E90"
        assert route_100["_local_id"] == "00000100--def456"
        assert route_100["maxqlog"] >= 0
        assert "fullname" in route_100
        assert "start_time" in route_100
        assert "end_time" in route_100

    def test_counter_fallback_no_create_time(self, populated_data_dir):
        """Without create_time in metadata, fall back to counter."""
        meta_path = populated_data_dir / "metadata.json"
        data = json.loads(meta_path.read_text())
        # Remove creation_time from a route
        del data["routes"]["00000100--def456"]["creation_time"]
        meta_path.write_text(json.dumps(data))

        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store = RouteStore(str(populated_data_dir))
                store._dongle_id = "test123"
                store.scan(force=True)

        for r in store._routes.values():
            if r["_local_id"] == "00000100--def456":
                # create_time should be the counter (100)
                assert r["create_time"] == 100
                break

    def test_stub_filtering(self, tmp_path):
        """Routes with maxqlog < 1 and no distance should be filtered out."""
        # Create a single-segment route with no distance data
        d = tmp_path / "00000001--stub--0"
        d.mkdir()
        (d / "rlog.zst").write_bytes(b"")
        # No metadata, no distance
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store = RouteStore(str(tmp_path))
                store._dongle_id = "test123"
                store.scan(force=True)
        # Single segment = maxqlog 0, no distance -> should be filtered
        for r in store._routes.values():
            assert r["_local_id"] != "00000001--stub"


# ─── Route operations ────────────────────────────────────────────────

class TestRouteOperations:
    def test_hide_route(self, mock_store):
        lid = "00000042--abc123"
        mock_store.preserve_route(lid)
        assert mock_store.is_preserved(lid) is True

        mock_store.hide_route(lid)
        assert lid in mock_store._hidden
        # Should be removed from routes
        for r in mock_store._routes.values():
            assert r["_local_id"] != lid
        # Should be removed from preserved
        assert lid not in mock_store._preserved

    def test_preserve_unpreserve(self, mock_store):
        lid = "00000042--abc123"
        mock_store.preserve_route(lid)
        assert mock_store.is_preserved(lid) is True
        mock_store.unpreserve_route(lid)
        assert mock_store.is_preserved(lid) is False

    def test_is_preserved_default_false(self, mock_store):
        assert mock_store.is_preserved("nonexistent") is False

    def test_resolve_segment_path_exists(self, mock_store):
        routes = mock_store.scan()
        # Get a route's fullname
        for fullname, r in routes.items():
            if r["_local_id"] == "00000042--abc123":
                path = mock_store.resolve_segment_path(fullname, 0, "rlog.zst")
                assert path is not None
                assert path.exists()
                break

    def test_resolve_segment_path_missing(self, mock_store):
        routes = mock_store.scan()
        for fullname, r in routes.items():
            if r["_local_id"] == "00000042--abc123":
                path = mock_store.resolve_segment_path(fullname, 99, "rlog.zst")
                assert path is None
                break

    def test_get_route_and_get_local_id(self, mock_store):
        routes = mock_store.scan()
        for fullname, r in routes.items():
            lid = mock_store.get_local_id(fullname)
            assert lid == r["_local_id"]
            route_back = mock_store.get_route(fullname)
            assert route_back is not None
            assert route_back["fullname"] == fullname
            break


# ─── Metadata persistence ────────────────────────────────────────────

class TestMetadata:
    def test_save_load_roundtrip(self, mock_store):
        mock_store.preserve_route("00000042--abc123")
        mock_store._save_metadata()

        # Reload
        with patch("route_store.run_cleanup", return_value={"free_pct": 50, "deleted": []}):
            with patch.object(RouteStore, "_detect_dongle_id"), \
                 patch.object(RouteStore, "_detect_agnos_version"):
                store2 = RouteStore(str(mock_store.data_dir))
        assert "00000042--abc123" in store2._preserved
        assert "00000042--abc123" in store2._metadata

    def test_rlog_to_metadata_entry(self, mock_store):
        rlog_meta = {
            "dongle_id": "test123",
            "wall_time_nanos": 1700000000000000000,
            "start_lat": 31.23,
            "start_lng": 121.47,
            "git_commit": "abc123",
            "version": "0.9.8",
            "car_fingerprint": "BMW_E90",
            "device_type": "tici",
            "total_distance_m": 5000.0,
        }
        entry = mock_store._rlog_to_metadata_entry("00000042--abc123", rlog_meta)
        assert entry["route_id"] == "00000042--abc123"
        assert entry["dongle_id"] == "test123"
        assert entry["car_fingerprint"] == "BMW_E90"
        assert entry["gps_coordinates"] == [31.23, 121.47]
        assert entry["creation_time"] is not None
        assert entry["source"] == "connect_server"


# ─── Distance calculation ────────────────────────────────────────────

class TestDistance:
    def test_from_coords_json(self, mock_store):
        # Create a coords.json in a segment directory
        routes = mock_store.scan()
        for r in routes.values():
            if r["_local_id"] == "00000042--abc123":
                seg_path = Path(r["_segments"][0]["path"])
                coords = [{"t": 0, "lat": 31.0, "lng": 121.0, "speed": 10, "dist": 0},
                          {"t": 60, "lat": 31.01, "lng": 121.01, "speed": 10, "dist": 1500.0}]
                (seg_path / "coords.json").write_text(json.dumps(coords))
                # Force rebuild
                mock_store.scan(force=True)
                break
        # Check that route now has distance from coords
        for r in mock_store._routes.values():
            if r["_local_id"] == "00000042--abc123":
                assert r["distance"] is not None
                assert r["distance"] > 0
                break

    def test_fallback_to_metadata_distance(self, mock_store):
        # Routes should have distance from metadata total_distance_m
        routes = mock_store.scan()
        for r in routes.values():
            if r["_local_id"] == "00000100--def456":
                # total_distance_m=12300 -> ~7.6 miles
                assert r["distance"] is not None
                assert r["distance"] > 0
                break
