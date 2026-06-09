"""Tests for verify.backends.generic — host-screen fallback."""

from __future__ import annotations

import platform
import subprocess
import types

import pytest

from verify.backends.generic import GenericBackend


def test_detection_low_confidence(tmp_path):
    # Always 1 — fallback never beats a real match but always available.
    assert GenericBackend.detect(tmp_path).confidence == 1


def test_linux_click_uses_xdotool(monkeypatch):
    calls: list[list] = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = GenericBackend()
    b._os = "Linux"
    b.click(10, 20)
    assert calls[-1] == ["xdotool", "mousemove", "10", "20", "click", "1"]


def test_linux_type_uses_xdotool(monkeypatch):
    calls: list[list] = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = GenericBackend()
    b._os = "Linux"
    b.type_text("hi")
    assert calls[-1][:3] == ["xdotool", "type", "--delay"]


def test_linux_key_translates(monkeypatch):
    calls: list[list] = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = GenericBackend()
    b._os = "Linux"
    b.key("enter")
    assert calls[-1] == ["xdotool", "key", "Return"]


def test_darwin_uses_cliclick(monkeypatch):
    calls: list[list] = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = GenericBackend()
    b._os = "Darwin"
    b.click(50, 60)
    assert calls[-1] == ["cliclick", "c:50,60"]
    b.type_text("hello")
    assert calls[-1] == ["cliclick", "t:hello"]


def test_screenshot_path_uses_mss(monkeypatch):
    """Verify the mss-based path is exercised. We mock mss + Image."""
    fake_monitor = {"width": 100, "height": 50}

    class FakeRaw:
        size = (100, 50)
        bgra = b"\x00" * (100 * 50 * 4)

    class FakeMss:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        monitors = [fake_monitor, fake_monitor]

        def grab(self, m):
            return FakeRaw()

    monkeypatch.setattr("mss.mss", lambda: FakeMss())
    b = GenericBackend()
    png = b.screenshot()
    assert png.startswith(b"\x89PNG")


def test_screen_size_via_mss(monkeypatch):
    class FakeMss:
        monitors = [None, {"width": 1920, "height": 1080}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("mss.mss", lambda: FakeMss())
    b = GenericBackend()
    assert b.screen_size() == (1920, 1080)


def test_unsupported_os_routes_to_powershell(monkeypatch):
    calls: list[list] = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = GenericBackend()
    b._os = "Windows"
    b.type_text("hi")
    assert calls[-1][0] == "powershell"
