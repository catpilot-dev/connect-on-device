"""Tests for _find_closest_snapshot in handlers.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import _find_closest_snapshot


class TestFindClosestSnapshot:
    def test_empty_returns_none(self):
        assert _find_closest_snapshot([], 5000) is None

    def test_exact_match(self):
        snaps = [{"offset_ms": 0}, {"offset_ms": 1000}, {"offset_ms": 2000}]
        result = _find_closest_snapshot(snaps, 1000)
        assert result["offset_ms"] == 1000

    def test_between_two_picks_closer(self):
        snaps = [{"offset_ms": 0}, {"offset_ms": 1000}, {"offset_ms": 2000}]
        # 1600 is closer to 2000 than to 1000
        result = _find_closest_snapshot(snaps, 1600)
        assert result["offset_ms"] == 2000
        # 1400 is closer to 1000 than to 2000
        result = _find_closest_snapshot(snaps, 1400)
        assert result["offset_ms"] == 1000

    def test_before_first(self):
        snaps = [{"offset_ms": 500}, {"offset_ms": 1000}]
        result = _find_closest_snapshot(snaps, 0)
        assert result["offset_ms"] == 500

    def test_after_last(self):
        snaps = [{"offset_ms": 0}, {"offset_ms": 1000}]
        result = _find_closest_snapshot(snaps, 9999)
        assert result["offset_ms"] == 1000

    def test_single_item(self):
        snaps = [{"offset_ms": 500}]
        assert _find_closest_snapshot(snaps, 0)["offset_ms"] == 500
        assert _find_closest_snapshot(snaps, 999)["offset_ms"] == 500
