"""Tests for log_parser.py — pure functions only (no cereal needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from log_parser import _haversine_dist, _sanitize_for_json


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


# ─── _sanitize_for_json ────────────────────────────────────────────

class TestSanitizeForJson:
    def test_short_bytes_to_hex(self):
        result = _sanitize_for_json(b"\xde\xad\xbe\xef")
        assert result == "deadbeef"

    def test_long_bytes_truncated(self):
        data = bytes(range(256)) * 2  # 512 bytes
        result = _sanitize_for_json(data)
        assert result == "<512 bytes>"

    def test_empty_bytes(self):
        assert _sanitize_for_json(b"") == ""

    def test_exactly_64_bytes(self):
        data = b"\x00" * 64
        result = _sanitize_for_json(data)
        assert result == "00" * 64  # hex string

    def test_65_bytes_truncated(self):
        data = b"\x00" * 65
        result = _sanitize_for_json(data)
        assert result == "<65 bytes>"

    def test_nan_becomes_none(self):
        assert _sanitize_for_json(float("nan")) is None

    def test_inf_becomes_none(self):
        assert _sanitize_for_json(float("inf")) is None

    def test_neg_inf_becomes_none(self):
        assert _sanitize_for_json(float("-inf")) is None

    def test_normal_float_unchanged(self):
        assert _sanitize_for_json(3.14) == 3.14

    def test_int_unchanged(self):
        assert _sanitize_for_json(42) == 42

    def test_string_unchanged(self):
        assert _sanitize_for_json("hello") == "hello"

    def test_none_unchanged(self):
        assert _sanitize_for_json(None) is None

    def test_dict_recursive(self):
        data = {"key": b"\xab\xcd", "nested": {"val": float("nan")}}
        result = _sanitize_for_json(data)
        assert result == {"key": "abcd", "nested": {"val": None}}

    def test_list_recursive(self):
        data = [b"\x01\x02", float("inf"), "ok"]
        result = _sanitize_for_json(data)
        assert result == ["0102", None, "ok"]

    def test_tuple_recursive(self):
        data = (b"\xff", 42)
        result = _sanitize_for_json(data)
        assert result == ["ff", 42]

    def test_nested_complex(self):
        data = {"signals": [{"data": b"\x00" * 100, "value": float("nan")}]}
        result = _sanitize_for_json(data)
        assert result == {"signals": [{"data": "<100 bytes>", "value": None}]}
