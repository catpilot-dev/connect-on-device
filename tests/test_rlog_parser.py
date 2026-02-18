"""Tests for rlog_parser.py — pure functions only (no cereal needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rlog_parser import AttributeDict, _haversine_dist


# ─── _haversine_dist ─────────────────────────────────────────────────

class TestHaversineDist:
    def test_same_point(self):
        assert _haversine_dist(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_ny_london(self):
        # New York to London ~ 5570 km
        d = _haversine_dist(40.7128, -74.0060, 51.5074, -0.1278)
        assert 5500_000 < d < 5650_000  # meters

    def test_short_distance(self):
        # ~111 meters (0.001 degree latitude)
        d = _haversine_dist(31.230, 121.470, 31.231, 121.470)
        assert 100 < d < 120

    def test_antipodal(self):
        # Opposite sides of Earth ~ 20015 km
        d = _haversine_dist(0.0, 0.0, 0.0, 180.0)
        assert 20000_000 < d < 20100_000


# ─── AttributeDict ──────────────────────────────────────────────────

class TestAttributeDict:
    def test_basic_access(self):
        d = AttributeDict({"x": 1, "y": 2})
        assert d.x == 1
        assert d.y == 2

    def test_nested_wrap(self):
        d = AttributeDict.wrap({"outer": {"inner": 42}})
        assert d.outer.inner == 42

    def test_list_wrap(self):
        d = AttributeDict.wrap({"points": [{"a": 1}, {"a": 2}]})
        assert d.points[0].a == 1
        assert d.points[1].a == 2

    def test_missing_key_raises(self):
        d = AttributeDict({"x": 1})
        with pytest.raises(AttributeError, match="no key"):
            _ = d.missing

    def test_setattr(self):
        d = AttributeDict()
        d.foo = "bar"
        assert d["foo"] == "bar"
        assert d.foo == "bar"
