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


# ---- full lifecycle (Xvfb + app), all subprocesses mocked ----------------


class _FakeProc:
    def __init__(self, name):
        self.name = name
        self.stdout = None
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def linux_lifecycle(monkeypatch):
    """Mock Popen and subprocess.run so we can exercise start/stop end-to-end."""
    procs: list[_FakeProc] = []

    def fake_popen(cmd, **kwargs):
        name = "xvfb" if "Xvfb" in cmd[0] else "app"
        p = _FakeProc(name)
        procs.append(p)
        return p

    def fake_run(args, **kw):
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    # Skip the Xvfb-socket wait by faking the marker path.
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", fake_run)

    # Pretend Xvfb's UNIX socket appears instantly.
    import pathlib

    real_exists = pathlib.Path.exists

    def maybe_exists(self):
        if "/tmp/.X11-unix/X" in str(self):
            return True
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", maybe_exists)
    return procs


def test_full_start_and_stop(linux_lifecycle):
    from verify.backends.base import LaunchSpec
    from verify.backends.linux_desktop import LinuxDesktopBackend

    b = LinuxDesktopBackend(display=":42", screen_size=(640, 480))
    b.start(LaunchSpec(command="./myapp", wait_after=0))
    # Both Xvfb and app proc started.
    assert len(linux_lifecycle) == 2
    names = {p.name for p in linux_lifecycle}
    assert names == {"xvfb", "app"}
    b.stop()
    for p in linux_lifecycle:
        assert p.terminated or p.killed


def test_stop_with_no_procs_is_safe():
    """stop() before start() must not crash."""
    from verify.backends.linux_desktop import LinuxDesktopBackend

    b = LinuxDesktopBackend()
    b.stop()  # no exception


def test_capabilities_reports_no_dom():
    from verify.backends.linux_desktop import LinuxDesktopBackend

    caps = LinuxDesktopBackend().capabilities()
    assert caps.can_navigate is False
    assert caps.can_query_dom is False
