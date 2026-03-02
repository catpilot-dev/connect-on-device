"""Tests for handlers/signals.py — pure functions _parse_segments and _resolve_log_pairs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers.signals import _parse_segments, _resolve_log_pairs


# ─── _parse_segments ─────────────────────────────────────────────────

class TestParseSegments:
    def test_single_number(self):
        assert _parse_segments("3") == [3]

    def test_range(self):
        assert _parse_segments("0-5") == [0, 1, 2, 3, 4, 5]

    def test_comma_list(self):
        assert _parse_segments("0,1,3") == [0, 1, 3]

    def test_mixed_range_and_single(self):
        assert _parse_segments("0-2,5,8-9") == [0, 1, 2, 5, 8, 9]

    def test_sorted_output(self):
        assert _parse_segments("5,1,3") == [1, 3, 5]

    def test_whitespace_handling(self):
        assert _parse_segments(" 1 , 3 , 5 ") == [1, 3, 5]

    def test_single_range(self):
        assert _parse_segments("2-2") == [2]

    def test_zero(self):
        assert _parse_segments("0") == [0]


# ─── _resolve_log_pairs ─────────────────────────────────────────────

class TestResolveLogPairs:
    def test_finds_rlog_zst(self, mock_store):
        routes = mock_store.scan()
        fullname = next(iter(routes))
        # Write some content so the file exists
        local_id = mock_store.get_local_id(fullname)
        seg_dir = mock_store.data_dir / f"{local_id}--0"
        (seg_dir / "rlog.zst").write_bytes(b"data")

        pairs = _resolve_log_pairs(mock_store, fullname, [0])
        assert pairs is not None
        assert len(pairs) == 1
        assert pairs[0][0] == 0
        assert "rlog.zst" in pairs[0][1]

    def test_returns_none_for_unknown_route(self, mock_store):
        mock_store.scan()
        result = _resolve_log_pairs(mock_store, "nonexistent/route", [0])
        assert result is None

    def test_empty_for_missing_segments(self, mock_store):
        routes = mock_store.scan()
        fullname = next(iter(routes))
        pairs = _resolve_log_pairs(mock_store, fullname, [99])
        assert pairs is not None
        assert len(pairs) == 0

    def test_prefers_qlog_over_rlog(self, mock_store):
        routes = mock_store.scan()
        fullname = next(iter(routes))
        local_id = mock_store.get_local_id(fullname)
        seg_dir = mock_store.data_dir / f"{local_id}--0"
        (seg_dir / "rlog.zst").write_bytes(b"rlog")
        (seg_dir / "qlog.zst").write_bytes(b"qlog")

        pairs = _resolve_log_pairs(mock_store, fullname, [0])
        assert len(pairs) == 1
        assert "qlog.zst" in pairs[0][1]

    def test_multiple_segments(self, mock_store):
        routes = mock_store.scan()
        for fullname, r in routes.items():
            if r["_local_id"] == "00000042--abc123":
                for seg in r["_segments"]:
                    seg_path = Path(seg["path"])
                    (seg_path / "rlog.zst").write_bytes(b"data")
                pairs = _resolve_log_pairs(mock_store, fullname, [0, 1])
                assert len(pairs) == 2
                break
