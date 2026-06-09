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


# ---- full launch lifecycle (subprocess + socket fully mocked) -----------


class _FakeRenodeProc:
    def __init__(self):
        self.stdout = None  # disables the log-drain thread inside the backend
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class _FakeMonitorSocket:
    def __init__(self):
        self.sent: list[bytes] = []
        self.closed = False
        self.recv_queue: list[bytes] = []

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, n):
        if not self.recv_queue:
            raise BlockingIOError()
        return self.recv_queue.pop(0)

    def settimeout(self, *a):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def renode_mocked(monkeypatch):
    import socket as _socket
    import subprocess
    import shutil

    proc = _FakeRenodeProc()
    sock = _FakeMonitorSocket()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(_socket, "create_connection", lambda *a, **kw: sock)
    return proc, sock


def test_renode_full_start_and_stop(renode_mocked):
    from verify.backends.base import LaunchSpec
    from verify.backends.renode import RenodeBackend

    proc, sock = renode_mocked
    b = RenodeBackend()
    b.start(LaunchSpec(command="./demo.resc"))
    assert b._proc is proc
    assert b._sock is sock
    b.stop()
    # `quit` was sent on shutdown.
    assert any(b"quit" in s for s in sock.sent)
    assert sock.closed
    assert proc.terminated or proc.killed
    assert b._sock is None
    assert b._proc is None


def test_renode_capabilities_reports_no_screenshot_by_default():
    from verify.backends.renode import RenodeBackend

    caps = RenodeBackend().capabilities()
    assert caps.has_screenshot is False


def test_renode_capabilities_with_frame_analyzer():
    from verify.backends.renode import RenodeBackend

    caps = RenodeBackend(frame_analyzer="sysbus.lcd").capabilities()
    assert caps.has_screenshot is True


def test_renode_send_collects_multichunk_response(renode_mocked):
    from verify.backends.renode import RenodeBackend

    _, sock = renode_mocked
    b = RenodeBackend()
    b._sock = sock
    sock.recv_queue = [b"line one\n", b"line two\n"]
    out = b.send("status")
    assert "line one" in out and "line two" in out
    # Monitor history recorded the command and both reply lines.
    history = "\n".join(b._monitor_logs)
    assert "status" in history
    assert "line one" in history


def test_renode_key_maps_to_writechar(renode_mocked):
    from verify.backends.renode import RenodeBackend

    _, sock = renode_mocked
    b = RenodeBackend()
    b._sock = sock
    b.key("enter")
    sent = b"".join(sock.sent).decode()
    assert "WriteChar" in sent
    # \r = 0x0d
    assert "0x0d" in sent


def test_renode_unknown_key_uses_first_char(renode_mocked):
    from verify.backends.renode import RenodeBackend

    _, sock = renode_mocked
    b = RenodeBackend()
    b._sock = sock
    b.key("A")
    sent = b"".join(sock.sent).decode()
    assert "0x41" in sent  # 'A'


def test_renode_type_text_uses_writeline(renode_mocked):
    from verify.backends.renode import RenodeBackend

    _, sock = renode_mocked
    b = RenodeBackend()
    b._sock = sock
    b.type_text("hello")
    assert b'WriteLine "hello"' in b"".join(sock.sent)


def test_renode_connect_monitor_retries_until_open(monkeypatch):
    """If the first few connect attempts fail (Renode still booting), the
    backend should retry until the socket is reachable."""
    import socket as _socket

    from verify.backends.renode import RenodeBackend

    attempts = {"n": 0}

    def fake_connect(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("Connection refused")
        return _FakeMonitorSocket()

    monkeypatch.setattr(_socket, "create_connection", fake_connect)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # speed up
    b = RenodeBackend()
    b._connect_monitor(timeout=10)
    assert attempts["n"] == 3
    assert b._sock is not None
