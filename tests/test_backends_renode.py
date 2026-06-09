"""Tests for verify.backends.renode — mocks Monitor socket + subprocess."""

from __future__ import annotations

import socket
import subprocess
import types

import pytest

from verify.backends.renode import RenodeBackend


def test_detection_resc_script(tmp_path):
    (tmp_path / "demo.resc").write_text("# resc")
    assert RenodeBackend.detect(tmp_path).confidence == 95


def test_send_pushes_command(monkeypatch):
    b = RenodeBackend()

    class FakeSock:
        def __init__(self):
            self.sent: list[bytes] = []

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, n):
            raise BlockingIOError()

        def settimeout(self, *a):
            pass

        def close(self):
            pass

    b._sock = FakeSock()
    b.send("cpu Start")
    assert b._sock.sent == [b"cpu Start\n"]
    # Logged in monitor history.
    assert any("cpu Start" in line for line in b._monitor_logs)


def test_send_collects_response(monkeypatch):
    b = RenodeBackend()

    class FakeSock:
        def __init__(self):
            self.calls = 0

        def sendall(self, data):
            pass

        def recv(self, n):
            self.calls += 1
            if self.calls == 1:
                return b"ok 1\n"
            raise BlockingIOError()

        def settimeout(self, *a):
            pass

        def close(self):
            pass

    b._sock = FakeSock()
    out = b.send("status")
    assert "ok 1" in out


def test_send_raises_without_connection():
    b = RenodeBackend()
    with pytest.raises(RuntimeError, match="not connected"):
        b.send("anything")


def test_screenshot_requires_frame_analyzer():
    b = RenodeBackend(frame_analyzer=None)
    with pytest.raises(RuntimeError, match="framebuffer"):
        b.screenshot()


def test_click_not_supported():
    b = RenodeBackend()
    with pytest.raises(NotImplementedError, match="no click"):
        b.click(10, 10)


def test_start_requires_resc(tmp_path):
    b = RenodeBackend()
    from verify.backends.base import LaunchSpec

    with pytest.raises(ValueError, match=r"\.resc"):
        b.start(LaunchSpec())  # no command


def test_screen_size_default():
    b = RenodeBackend()
    assert b.screen_size() == (240, 320)


def test_logs_buffered_in_uart_log_deque():
    b = RenodeBackend()
    for i in range(5):
        b._uart_logs.append(f"line {i}")
    text = b.read_logs(lines=3)
    assert text.splitlines() == ["line 2", "line 3", "line 4"]
