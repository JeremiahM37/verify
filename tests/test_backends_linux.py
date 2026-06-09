"""Tests for verify.backends.linux_desktop — mocks xdotool/Xvfb/xwd."""

from __future__ import annotations

import subprocess
import types

import pytest

from verify.backends.linux_desktop import LinuxDesktopBackend, _KEY_MAP


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_click_uses_xdotool(captured):
    b = LinuxDesktopBackend()
    b.click(100, 200, button="left")
    assert captured[-1]["args"] == [
        "xdotool", "mousemove", "100", "200", "click", "1"
    ]
    b.click(50, 60, button="right")
    assert captured[-1]["args"][-1] == "3"


def test_click_passes_display_env(captured):
    b = LinuxDesktopBackend(display=":42")
    b.click(1, 1)
    env = captured[-1]["kwargs"]["env"]
    assert env["DISPLAY"] == ":42"


def test_type_text_uses_xdotool_with_delay(captured):
    b = LinuxDesktopBackend()
    b.type_text("hello world")
    args = captured[-1]["args"]
    assert args[0] == "xdotool"
    assert args[1] == "type"
    assert "--" in args
    assert args[-1] == "hello world"


def test_key_maps_friendly_names(captured):
    b = LinuxDesktopBackend()
    b.key("enter")
    assert captured[-1]["args"] == ["xdotool", "key", "Return"]
    b.key("backspace")
    assert captured[-1]["args"][-1] == "BackSpace"
    b.key("F5")  # passthrough
    assert captured[-1]["args"][-1] == "F5"


def test_key_map_covers_common_keys():
    for k in ["enter", "tab", "escape", "backspace", "up", "down"]:
        assert k in _KEY_MAP


def test_screenshot_xwd_pipeline(monkeypatch):
    """Verify xwd | convert pipeline is invoked and PNG bytes returned."""
    monkeypatch.setattr(
        "verify.backends.linux_desktop._which",
        lambda name: "/usr/bin/" + name if name in {"convert", "xwd"} else None,
    )

    runs: list[dict] = []

    def fake_run(args, **kwargs):
        runs.append({"args": args, "kwargs": kwargs})
        if args[0] == "xwd":
            return types.SimpleNamespace(returncode=0, stdout=b"XWD-BYTES", stderr=b"")
        if args[0] == "convert":
            assert kwargs.get("input") == b"XWD-BYTES"
            return types.SimpleNamespace(returncode=0, stdout=b"\x89PNG\r\n\x1a\nPNGDATA", stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = LinuxDesktopBackend()
    png = b.screenshot()
    assert png.startswith(b"\x89PNG")
    cmds = [r["args"][0] for r in runs]
    assert cmds == ["xwd", "convert"]


def test_screenshot_raises_when_no_encoder(monkeypatch):
    monkeypatch.setattr("verify.backends.linux_desktop._which", lambda name: None)
    b = LinuxDesktopBackend()
    with pytest.raises(RuntimeError, match="convert"):
        b.screenshot()


def test_screen_size_returned(captured):
    b = LinuxDesktopBackend(screen_size=(1920, 1080))
    assert b.screen_size() == (1920, 1080)


def test_start_requires_command():
    b = LinuxDesktopBackend()
    from verify.backends.base import LaunchSpec

    with pytest.raises(ValueError, match="launch.command"):
        # Bypass Xvfb to isolate the validation path.
        b._start_app(LaunchSpec())
