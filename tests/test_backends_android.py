"""Tests for verify.backends.android — mocks adb subprocess calls."""

from __future__ import annotations

import subprocess
import types

import pytest

from verify.backends.android import AndroidBackend, _KEY_MAP


@pytest.fixture
def captured(monkeypatch):
    """Capture every subprocess.run call the backend makes."""
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        # Sensible defaults — caller can override via specific mocks.
        rv = types.SimpleNamespace(
            returncode=0, stdout=b"", stderr=b""
        )
        return rv

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_screenshot_uses_exec_out(monkeypatch, captured):
    def fake_run(args, **kwargs):
        if args[-3:] == ["exec-out", "screencap", "-p"]:
            captured.append({"args": args, "kwargs": kwargs})
            return types.SimpleNamespace(returncode=0, stdout=b"\x89PNG\r\n\x1a\nIMG", stderr=b"")
        captured.append({"args": args, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = AndroidBackend()
    out = b.screenshot()
    assert out.startswith(b"\x89PNG")
    last = captured[-1]["args"]
    assert last == ["adb", "exec-out", "screencap", "-p"]


def test_serial_passes_through(monkeypatch, captured):
    b = AndroidBackend(serial="emulator-5554")
    b.click(10, 20)
    args = captured[-1]["args"]
    assert args[:3] == ["adb", "-s", "emulator-5554"]
    assert args[-5:] == ["shell", "input", "tap", "10", "20"]


def test_type_text_escapes_spaces(monkeypatch, captured):
    b = AndroidBackend()
    b.type_text("hello world")
    args = captured[-1]["args"]
    assert args == ["adb", "shell", "input", "text", "hello%sworld"]


def test_key_maps_friendly_names(monkeypatch, captured):
    b = AndroidBackend()
    b.key("enter")
    assert captured[-1]["args"][-1] == "KEYCODE_ENTER"
    b.key("back")
    assert captured[-1]["args"][-1] == "KEYCODE_BACK"
    b.key("DPAD_CENTER")  # unknown -> pass through verbatim
    assert captured[-1]["args"][-1] == "DPAD_CENTER"


def test_key_map_covers_common_keys():
    # Sanity check on the static map shape.
    for k in ["enter", "back", "home", "tab", "escape", "space", "up"]:
        assert k in _KEY_MAP
        assert _KEY_MAP[k].startswith("KEYCODE_")


def test_screen_size_parses_wm_output(monkeypatch):
    def fake_run(args, **kwargs):
        return types.SimpleNamespace(
            returncode=0, stdout=b"Physical size: 1080x2400\n", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = AndroidBackend()
    assert b.screen_size() == (1080, 2400)
    # Cached on second call.
    assert b.screen_size() == (1080, 2400)


def test_screen_size_falls_back_on_bad_output(monkeypatch):
    def fake_run(args, **kwargs):
        return types.SimpleNamespace(
            returncode=0, stdout=b"nonsense", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = AndroidBackend()
    assert b.screen_size() == (1080, 1920)


def test_list_devices_parses_adb_devices(monkeypatch):
    out = b"List of devices attached\nemulator-5554\tdevice\noffline-xyz\toffline\n"

    def fake_run(args, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    b = AndroidBackend()
    assert b.list_devices() == ["emulator-5554"]


def test_detection_does_not_require_adb(tmp_path):
    # Detection must work even when host has no adb.
    (tmp_path / "AndroidManifest.xml").write_text("<manifest/>")
    r = AndroidBackend.detect(tmp_path)
    assert r.confidence == 95


def test_docker_emulator_path_starts_and_stops_sandbox(monkeypatch):
    """When docker_image is set, the backend boots a Sandbox, attaches via adb,
    and tears the container down on stop()."""
    from verify.backends.base import LaunchSpec
    from verify import sandbox as sb_mod

    # Track sandbox lifecycle.
    events: list[str] = []

    class FakeSandbox:
        instances: list["FakeSandbox"] = []

        def __init__(self):
            self.container_id = "fake-cid"
            self.stopped = False
            self.port_map = {5555: 49555}
            events.append("sandbox_run")
            FakeSandbox.instances.append(self)

        def wait_for_log(self, needle, *, timeout):
            events.append(f"wait_for_log:{needle}")
            return True

        def host_port_for(self, p):
            return self.port_map[p]

        def stop(self):
            events.append("sandbox_stop")
            self.stopped = True

    monkeypatch.setattr(sb_mod.Sandbox, "run", classmethod(lambda cls, *a, **kw: FakeSandbox()))

    # Mock subprocess.run so adb calls always succeed.
    def fake_run(args, **kw):
        events.append("subproc:" + " ".join(args[:3]))
        return types.SimpleNamespace(returncode=0, stdout=b"1", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Skip the boot-completed waiter loop.
    monkeypatch.setattr(AndroidBackend, "_wait_for_boot", lambda self, timeout=60: None)
    monkeypatch.setattr(AndroidBackend, "_start_logcat", lambda self: None)

    b = AndroidBackend(docker_image="budtmo/docker-android:emulator_14.0")
    b.start(LaunchSpec())
    assert b._serial == "127.0.0.1:49555"  # picked up host port
    assert b._sandbox is not None
    b.stop()

    assert "sandbox_run" in events
    assert any(e.startswith("wait_for_log:") for e in events)
    assert "sandbox_stop" in events
    assert b._sandbox is None  # cleared after stop


def test_docker_emulator_path_raises_on_boot_timeout(monkeypatch):
    from verify.backends.base import LaunchSpec
    from verify import sandbox as sb_mod

    class FakeSandbox:
        container_id = "cid"

        def wait_for_log(self, needle, *, timeout):
            return False  # boot never completes

        def stop(self):
            pass

    monkeypatch.setattr(sb_mod.Sandbox, "run", classmethod(lambda cls, *a, **kw: FakeSandbox()))
    b = AndroidBackend(docker_image="foo")
    with pytest.raises(TimeoutError, match="never logged"):
        b.start(LaunchSpec())
