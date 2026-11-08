"""Tests for log_parser.py — pure functions only (no cereal needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import log_parser
from log_parser import (
    _extract_bookmark_epochs,
    _gps_anchor,
    _haversine_dist,
    _sanitize_for_json,
)


# ─── fake capnp events (no cereal needed) ──────────────────────────

class _FakeEvent:
    """Minimal stand-in for a capnp Event: .which() + one payload attr."""

    def __init__(self, which, mono, **payload):
        self._which = which
        self.logMonoTime = mono
        if payload:
            setattr(self, which, type("P", (), payload)())

    def which(self):
        return self._which


def _gps(mono, unix_s, fixed=True):
    return _FakeEvent("gpsLocationExternal", mono,
                      flags=1 if fixed else 0,
                      unixTimestampMillis=int(unix_s * 1000))


def _bookmark(mono):
    return _FakeEvent("userBookmark", mono)


def _other(mono):
    return _FakeEvent("carState", mono)


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


# ─── _gps_anchor ───────────────────────────────────────────────────

OFFSET = 1_786_499_000.0  # wall epoch at mono t=0, as on a real drive


class TestGpsAnchor:
    def _patch(self, monkeypatch, events):
        monkeypatch.setattr(log_parser, "_iter_log_head", lambda path, **kw: iter(events))

    def test_first_valid_fix(self, monkeypatch):
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _gps(101_000_000_000, OFFSET + 101.0),
            _gps(102_000_000_000, OFFSET + 102.0),
        ])
        assert _gps_anchor("q.zst") == (101_000_000_000, OFFSET + 101.0)

    def test_skips_unfixed_and_zero_timestamp(self, monkeypatch):
        self._patch(monkeypatch, [
            _gps(100_000_000_000, OFFSET + 100.0, fixed=False),  # no fix
            _gps(101_000_000_000, 0.0),                          # fix, no time yet
            _gps(102_000_000_000, OFFSET + 102.0),
        ])
        assert _gps_anchor("q.zst") == (102_000_000_000, OFFSET + 102.0)

    def test_no_fix_returns_none(self, monkeypatch):
        self._patch(monkeypatch, [_other(100_000_000_000)])
        assert _gps_anchor("q.zst") is None

    def test_read_error_returns_none(self, monkeypatch):
        def boom(path, **kw):
            raise OSError("no such file")
        monkeypatch.setattr(log_parser, "_iter_log_head", boom)
        assert _gps_anchor("q.zst") is None


# ─── _extract_bookmark_epochs ──────────────────────────────────────

class TestExtractBookmarkEpochs:
    def _patch(self, monkeypatch, events):
        monkeypatch.setattr(log_parser, "_iter_log", lambda path: iter(events))

    def test_offset_is_segment_relative(self, monkeypatch):
        self._patch(monkeypatch, [
            _other(100_000_000_000),                      # base_mono
            _gps(101_000_000_000, OFFSET + 101.0),
            _bookmark(120_000_000_000),                   # 20 s into segment 1
        ])
        (offset, epoch), = _extract_bookmark_epochs("q.zst", 1)
        assert offset == 80_000                           # 60 s + 20 s
        assert epoch == pytest.approx(OFFSET + 120.0)

    def test_initdata_does_not_set_base_mono(self, monkeypatch):
        # initData carries the openpilot start time, not the segment start
        self._patch(monkeypatch, [
            _FakeEvent("initData", 1_000_000_000),
            _other(100_000_000_000),
            _gps(101_000_000_000, OFFSET + 101.0),
            _bookmark(110_000_000_000),
        ])
        (offset, _), = _extract_bookmark_epochs("q.zst", 0)
        assert offset == 10_000

    def test_uses_nearest_fix_not_first(self, monkeypatch):
        # Second fix disagrees with the first — the tap sits next to it, so
        # the tap must be dated from it.
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _gps(101_000_000_000, OFFSET + 101.0),
            _gps(140_000_000_000, OFFSET + 145.0),        # +5 s apart
            _bookmark(138_000_000_000),
        ])
        (_, epoch), = _extract_bookmark_epochs("q.zst", 0)
        assert epoch == pytest.approx(OFFSET + 143.0)     # not OFFSET + 138.0

    def test_segment_fixes_win_over_route_anchor(self, monkeypatch):
        # The segment's own fix is closer in time than a route-wide anchor,
        # so it dates the tap — the anchor is only a fallback.
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _gps(119_000_000_000, OFFSET + 119.5),
            _bookmark(120_000_000_000),
        ])
        anchor = (500_000_000_000, OFFSET + 500.0)
        (_, epoch), = _extract_bookmark_epochs("q.zst", 0, anchor)
        assert epoch == pytest.approx(OFFSET + 120.5)     # not OFFSET + 120.0

    def test_route_anchor_dates_taps_in_a_fixless_segment(self, monkeypatch):
        # A segment that never got a fix still yields an exact tap time,
        # because logMonoTime is continuous across the whole route.
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _bookmark(120_000_000_000),
        ])
        anchor = (500_000_000_000, OFFSET + 500.0)
        (_, epoch), = _extract_bookmark_epochs("q.zst", 0, anchor)
        assert epoch == pytest.approx(OFFSET + 120.0)

    def test_no_fix_and_no_anchor_gives_none(self, monkeypatch):
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _gps(101_000_000_000, OFFSET + 101.0, fixed=False),
            _bookmark(120_000_000_000),
        ])
        (offset, epoch), = _extract_bookmark_epochs("q.zst", 0)
        assert offset == 20_000
        assert epoch is None

    def test_multiple_taps_sorted(self, monkeypatch):
        self._patch(monkeypatch, [
            _other(100_000_000_000),
            _gps(101_000_000_000, OFFSET + 101.0),
            _bookmark(130_000_000_000),
            _bookmark(110_000_000_000),
        ])
        result = _extract_bookmark_epochs("q.zst", 0)
        assert [o for o, _ in result] == [10_000, 30_000]

    def test_no_bookmarks(self, monkeypatch):
        self._patch(monkeypatch, [_other(100_000_000_000)])
        assert _extract_bookmark_epochs("q.zst", 0) == []


# ─── extract_frame_times ───────────────────────────────────────────

class TestExtractFrameTimes:
    """v0.11.2 renamed the road camera events (same ordinals):
    roadCameraState -> narrowRoadCameraState, roadEncodeIdx -> narrowRoadEncodeIdx."""

    def _patch(self, monkeypatch, events):
        monkeypatch.setattr(log_parser, "_iter_log_full", lambda path: iter(events))

    @staticmethod
    def _cam(frame_id, sof_s):
        return _FakeEvent("narrowRoadCameraState", 0,
                          frameId=frame_id, timestampSof=int(sof_s * 1e9))

    @staticmethod
    def _enc(frame_id):
        return _FakeEvent("narrowRoadEncodeIdx", 0, frameId=frame_id)

    def test_maps_encoded_frames_to_capture_time(self, monkeypatch):
        self._patch(monkeypatch, [
            _gps(100_000_000_000, OFFSET + 100.0),
            self._cam(2, 100.00), self._enc(2),
            self._cam(3, 100.05), self._enc(3),
        ])
        assert log_parser.extract_frame_times("r.zst") == [
            round(OFFSET + 100.00, 3), round(OFFSET + 100.05, 3)]

    def test_no_camera_events_returns_none(self, monkeypatch):
        self._patch(monkeypatch, [_gps(100_000_000_000, OFFSET + 100.0)])
        assert log_parser.extract_frame_times("r.zst") is None
