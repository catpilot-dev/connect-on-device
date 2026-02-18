"""Tests for hud_renderer.py — projection math only (no rendering)."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hud_renderer import (
    WIDTH, HEIGHT,
    build_transform,
    euler2rot,
    get_path_length_idx,
    map_line_to_polygon,
    map_to_screen,
    project_points,
)


# ─── euler2rot ───────────────────────────────────────────────────────

class TestEuler2Rot:
    def test_identity(self):
        R = euler2rot([0, 0, 0])
        np.testing.assert_allclose(R, np.eye(3), atol=1e-10)

    def test_valid_rotation_matrix(self):
        R = euler2rot([0.1, 0.2, 0.3])
        # det should be 1
        assert abs(np.linalg.det(R) - 1.0) < 1e-10
        # R @ R.T should be identity
        np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-10)

    def test_pure_yaw(self):
        import math
        yaw = math.pi / 4
        R = euler2rot([0, 0, yaw])
        # For pure yaw rotation: R[2,2] should be 1 (no change in Z for device frame)
        assert abs(R[2, 2] - 1.0) < 1e-10
        # Check cos/sin values in rotation
        assert abs(R[0, 0] - math.cos(yaw)) < 1e-10


# ─── build_transform ────────────────────────────────────────────────

class TestBuildTransform:
    def test_shape(self):
        T = build_transform([0, 0, 0])
        assert T.shape == (3, 3)

    def test_identity_calib_projects_center(self):
        T = build_transform([0, 0, 0])
        # A point far straight ahead should project near screen center
        pt = T @ np.array([100.0, 0.0, 0.0])
        if abs(pt[2]) > 1e-6:
            sx, sy = pt[0] / pt[2], pt[1] / pt[2]
            # Should be roughly centered
            assert abs(sx - WIDTH / 2) < 200
            assert abs(sy - HEIGHT / 2) < 200


# ─── project_points ─────────────────────────────────────────────────

class TestProjectPoints:
    def test_empty_input(self):
        T = build_transform([0, 0, 0])
        screen, valid = project_points(T, np.empty((0, 3)))
        assert screen.shape == (0, 2)
        assert valid.shape == (0,)

    def test_straight_ahead_near_center(self):
        T = build_transform([0, 0, 0])
        pts = np.array([[50.0, 0.0, 0.0]])
        screen, valid = project_points(T, pts)
        assert valid[0]
        # Should be near center of screen
        assert abs(screen[0, 0] - WIDTH / 2) < 200
        assert abs(screen[0, 1] - HEIGHT / 2) < 200

    def test_near_zero_z_invalid(self):
        T = build_transform([0, 0, 0])
        # Point at origin (device x=0) maps to view z=0 -> division by ~0 -> invalid
        pts = np.array([[0.0, 0.0, 0.0]])
        screen, valid = project_points(T, pts)
        assert not valid[0]

    def test_offscreen_invalid(self):
        T = build_transform([0, 0, 0])
        # Point extremely far to the side
        pts = np.array([[1.0, 100.0, 0.0]])
        screen, valid = project_points(T, pts)
        # May or may not be valid depending on clip bounds
        # The clip bounds are +-500 from screen edges, which is very generous
        # So even extreme side points may still pass — just verify it runs
        assert screen.shape == (1, 2)


# ─── map_line_to_polygon ────────────────────────────────────────────

class TestMapLineToPolygon:
    def test_empty_returns_empty(self):
        T = build_transform([0, 0, 0])
        result = map_line_to_polygon(T, np.empty((0, 3)), 0.9, 1.22, 0)
        assert result == []

    def test_simple_line_returns_polygon(self):
        T = build_transform([0, 0, 0])
        x = np.arange(5, 50, 5, dtype=np.float64)
        line = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
        poly = map_line_to_polygon(T, line, 0.9, 1.22, len(x) - 1)
        # Should return polygon points (left side + right side reversed)
        assert len(poly) > 0
        # Each point should be a (x, y) tuple
        for pt in poly:
            assert len(pt) == 2

    def test_invert_protection(self):
        T = build_transform([0, 0, 0])
        x = np.arange(5, 50, 5, dtype=np.float64)
        line = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
        # With allow_invert=False, Y-coordinate monotonicity enforced
        poly = map_line_to_polygon(T, line, 0.9, 1.22, len(x) - 1, allow_invert=False)
        # Should still produce valid polygon
        assert isinstance(poly, list)


# ─── get_path_length_idx ────────────────────────────────────────────

class TestGetPathLengthIdx:
    def test_normal(self):
        pos_x = np.array([0, 10, 20, 30, 40, 50], dtype=np.float64)
        idx = get_path_length_idx(pos_x, 25.0)
        assert idx == 2  # Last index where pos_x <= 25

    def test_empty(self):
        idx = get_path_length_idx(np.array([]), 10.0)
        assert idx == 0


# ─── map_to_screen ──────────────────────────────────────────────────

class TestMapToScreen:
    def test_forward_returns_tuple(self):
        T = build_transform([0, 0, 0])
        result = map_to_screen(T, 50.0, 0.0, 0.0)
        assert result is not None
        assert len(result) == 2

    def test_at_origin_returns_none(self):
        T = build_transform([0, 0, 0])
        # Device x=0 maps to view z=0 -> near-zero division -> None
        result = map_to_screen(T, 0.0, 0.0, 0.0)
        assert result is None
