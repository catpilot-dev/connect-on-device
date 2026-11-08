"""Tests for render_clip_headless.py helpers that don't need openpilot."""

import sys
import types

import render_clip_headless


def _fake_hardware(monkeypatch):
    for name in ("openpilot", "openpilot.common"):
        pkg = types.ModuleType(name)
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, name, pkg)
    hw = types.ModuleType("openpilot.common.hardware")
    hw.COMMA_HARDWARE = True
    monkeypatch.setitem(sys.modules, "openpilot.common.hardware", hw)
    return hw


def test_camera_view_falls_back_to_texture_path(monkeypatch):
    """The headless pbuffer context can't import dmabufs as EGL images; the
    camera view takes the texture-copy path only when COMMA_HARDWARE is False
    at its import."""
    hw = _fake_hardware(monkeypatch)
    render_clip_headless._force_camera_texture_path()
    assert hw.COMMA_HARDWARE is False


def test_imports_no_ui_modules(monkeypatch):
    """application.py reads RECORD/BIG/OFFSCREEN at import and hooks must be
    registered before ui_state loads, so this must not import any UI module."""
    _fake_hardware(monkeypatch)
    before = set(sys.modules)
    render_clip_headless._force_camera_texture_path()
    assert not {m for m in set(sys.modules) - before if "ui" in m.split(".")}
