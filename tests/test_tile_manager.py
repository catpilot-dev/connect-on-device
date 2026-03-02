"""Tests for tile_manager.py — OSM tile management functions."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tile_manager
from tile_manager import (
    _snap_to_grid,
    cancel_download,
    delete_tile,
    get_downloaded_tiles,
    get_progress,
    get_storage_info,
)


# ─── _snap_to_grid ──────────────────────────────────────────────────

class TestSnapToGrid:
    def test_even_values_unchanged(self):
        assert _snap_to_grid(30, 120) == (30, 120)

    def test_odd_values_snapped_down(self):
        assert _snap_to_grid(31, 121) == (30, 120)

    def test_negative_values(self):
        assert _snap_to_grid(-33, -117) == (-34, -118)

    def test_zero(self):
        assert _snap_to_grid(0, 0) == (0, 0)

    def test_one(self):
        assert _snap_to_grid(1, 1) == (0, 0)

    def test_large_values(self):
        assert _snap_to_grid(89, 179) == (88, 178)


# ─── get_downloaded_tiles ───────────────────────────────────────────

class TestGetDownloadedTiles:
    def test_empty_when_no_dir(self, tmp_path):
        with patch.object(tile_manager, "OSM_OFFLINE", tmp_path / "nonexistent"):
            tiles = get_downloaded_tiles()
            assert tiles == []

    def test_finds_tiles(self, tmp_path):
        offline = tmp_path / "offline"
        # Create tile structure: offline/30/120/data.pbf
        tile_dir = offline / "30" / "120"
        tile_dir.mkdir(parents=True)
        (tile_dir / "data.pbf").write_bytes(b"\x00" * 1024)

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            tiles = get_downloaded_tiles()
            assert len(tiles) == 1
            assert tiles[0]["lat"] == 30
            assert tiles[0]["lon"] == 120
            assert tiles[0]["size_mb"] >= 0

    def test_ignores_non_numeric_dirs(self, tmp_path):
        offline = tmp_path / "offline"
        (offline / "readme").mkdir(parents=True)
        (offline / "30" / "abc").mkdir(parents=True)

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            tiles = get_downloaded_tiles()
            assert tiles == []

    def test_multiple_tiles(self, tmp_path):
        offline = tmp_path / "offline"
        for lat, lon in [(30, 120), (30, 122), (32, 120)]:
            d = offline / str(lat) / str(lon)
            d.mkdir(parents=True)
            (d / "data.pbf").write_bytes(b"\x00" * 512)

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            tiles = get_downloaded_tiles()
            assert len(tiles) == 3
            lats = {t["lat"] for t in tiles}
            assert lats == {30, 32}


# ─── get_storage_info ────────────────────────────────────────────────

class TestGetStorageInfo:
    def test_no_dir(self, tmp_path):
        with patch.object(tile_manager, "OSM_BASE", tmp_path / "nonexistent"), \
             patch.object(tile_manager, "OSM_OFFLINE", tmp_path / "nonexistent" / "offline"):
            info = get_storage_info()
            assert info["total_mb"] == 0
            assert info["tile_count"] == 0

    def test_with_tiles(self, tmp_path):
        base = tmp_path / "osm"
        offline = base / "offline"
        tile_dir = offline / "30" / "120"
        tile_dir.mkdir(parents=True)
        # Use 2MB to ensure non-zero after rounding
        (tile_dir / "data.pbf").write_bytes(b"\x00" * (2 * 1024 * 1024))

        with patch.object(tile_manager, "OSM_BASE", base), \
             patch.object(tile_manager, "OSM_OFFLINE", offline):
            info = get_storage_info()
            assert info["total_mb"] > 0
            assert info["tile_count"] == 1


# ─── delete_tile ─────────────────────────────────────────────────────

class TestDeleteTile:
    def test_delete_existing_tile(self, tmp_path):
        offline = tmp_path / "offline"
        tile_dir = offline / "30" / "120"
        tile_dir.mkdir(parents=True)
        (tile_dir / "data.pbf").write_bytes(b"\x00")

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            result = delete_tile(30, 120)
            assert result is True
            assert not tile_dir.exists()

    def test_delete_nonexistent_tile(self, tmp_path):
        offline = tmp_path / "offline"
        offline.mkdir(parents=True)

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            result = delete_tile(30, 120)
            assert result is False

    def test_delete_snaps_to_grid(self, tmp_path):
        offline = tmp_path / "offline"
        tile_dir = offline / "30" / "120"
        tile_dir.mkdir(parents=True)
        (tile_dir / "data.pbf").write_bytes(b"\x00")

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            # 31 snaps to 30, 121 snaps to 120
            result = delete_tile(31, 121)
            assert result is True
            assert not tile_dir.exists()

    def test_cleans_empty_parent(self, tmp_path):
        offline = tmp_path / "offline"
        tile_dir = offline / "30" / "120"
        tile_dir.mkdir(parents=True)
        (tile_dir / "data.pbf").write_bytes(b"\x00")

        with patch.object(tile_manager, "OSM_OFFLINE", offline):
            delete_tile(30, 120)
            # Parent lat dir should be cleaned up when empty
            assert not (offline / "30").exists()


# ─── get_progress / cancel_download ──────────────────────────────────

class TestDownloadState:
    def test_initial_state(self):
        # Reset module state
        with tile_manager._state_lock:
            tile_manager._state = {
                "active": False, "total": 0, "done": 0,
                "current": None, "error": None, "cancelled": False,
            }

        progress = get_progress()
        assert progress["active"] is False
        assert progress["total"] == 0
        assert progress["done"] == 0
        assert progress["current"] is None
        assert progress["error"] is None
        assert progress["cancelled"] is False

    def test_cancel_sets_flag(self):
        with tile_manager._state_lock:
            tile_manager._state = {
                "active": True, "total": 5, "done": 2,
                "current": "30,120", "error": None, "cancelled": False,
            }

        cancel_download()
        progress = get_progress()
        assert progress["cancelled"] is True

    def test_progress_returns_copy(self):
        progress1 = get_progress()
        progress2 = get_progress()
        assert progress1 is not progress2
