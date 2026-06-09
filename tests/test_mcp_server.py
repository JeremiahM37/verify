"""Tests for verify.mcp_server.

We can't really stand up a stdio server in a pytest, but we can patch FastMCP
to capture every @mcp.tool() registration and prove each one calls through to
the right backend method. That gives us coverage of the entire serve() body
without the I/O.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from verify import mcp_server
from verify.backends.base import (
    Backend,
    BackendCapabilities,
    DetectionResult,
    LaunchSpec,
)
from verify.backends.registry import register
from verify.vision import StubVisionClient


class _RecordingBackend(Backend):
    """Backend that records every primitive call so we can assert through MCP tools."""

    name = "mcp_test_backend"

    def __init__(self, **_kw) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.started_with: LaunchSpec | None = None
        self.stopped = False
        self._screen = (320, 480)

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        return DetectionResult(0, "")

    def start(self, spec: LaunchSpec) -> None:
        self.started_with = spec

    def stop(self) -> None:
        self.stopped = True

    def screen_size(self) -> tuple[int, int]:
        return self._screen

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot", (), {}))
        return b"FAKEPNG"

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("click", (x, y), {"button": button}))

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", (text,), {}))

    def key(self, name: str) -> None:
        self.calls.append(("key", (name,), {}))

    def read_logs(self, lines: int = 100) -> str:
        self.calls.append(("read_logs", (lines,), {}))
        return f"<{lines} lines of log>"

    def navigate(self, url: str) -> None:
        self.calls.append(("navigate", (url,), {}))

    def wait(self, seconds: float) -> None:
        self.calls.append(("wait", (seconds,), {}))


# Register only for the scope of this module, then deregister.
register(_RecordingBackend)


class _FakeFastMCP:
    """Captures every @tool() decorated function, never starts a real server."""

    last: "_FakeFastMCP | None" = None

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, callable] = {}
        self.run_called_with: tuple[tuple, dict] | None = None
        _FakeFastMCP.last = self

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco

    def run(self, *args, **kwargs):
        self.run_called_with = (args, kwargs)


@pytest.fixture
def patched_mcp(monkeypatch):
    """Patch the mcp SDK import inside serve() with our fake."""
    import sys
    import types

    fake_mod = types.ModuleType("mcp.server.fastmcp")
    fake_mod.FastMCP = _FakeFastMCP
    fake_root = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    monkeypatch.setitem(sys.modules, "mcp", fake_root)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_mod)
    return _FakeFastMCP


@pytest.fixture
def config_file(tmp_path):
    """Write a .verify.yaml that targets our recording backend."""
    p = tmp_path / ".verify.yaml"
    p.write_text("backend: mcp_test_backend\nlaunch:\n  url: http://x\n")
    return p


def test_serve_registers_all_nine_tools(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    last = _FakeFastMCP.last
    assert last is not None
    expected = {
        "screenshot",
        "click",
        "type_text",
        "key",
        "wait",
        "read_logs",
        "navigate",
        "locate",
        "screen_size",
    }
    assert set(last.tools) == expected


def test_serve_starts_backend_with_launch_spec(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    # Find the recording-backend instance the runner created.
    # FakeFastMCP captured the closure; the backend lives inside the tools' globals.
    # We can introspect via the screenshot tool's __closure__.
    screenshot = _FakeFastMCP.last.tools["screenshot"]
    backend = screenshot.__closure__[0].cell_contents  # `backend` is the first closed-over var
    assert isinstance(backend, _RecordingBackend)
    assert backend.started_with is not None
    assert backend.started_with.url == "http://x"


def test_serve_stops_backend_on_exit(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    backend = _FakeFastMCP.last.tools["screenshot"].__closure__[0].cell_contents
    assert backend.stopped is True


def test_serve_runs_mcp_via_stdio(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    args, kwargs = _FakeFastMCP.last.run_called_with
    assert kwargs.get("transport") == "stdio"


# ---- per-tool behavior --------------------------------------------------


def _backend(last):
    return last.tools["screenshot"].__closure__[0].cell_contents


def test_screenshot_tool_returns_base64_png_with_dimensions(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    out = _FakeFastMCP.last.tools["screenshot"]()
    assert out["mime_type"] == "image/png"
    assert base64.b64decode(out["data_base64"]) == b"FAKEPNG"
    assert out["width"] == 320 and out["height"] == 480


def test_click_tool_calls_backend(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    msg = _FakeFastMCP.last.tools["click"](100, 200, button="right")
    assert ("click", (100, 200), {"button": "right"}) in b.calls
    assert "100" in msg and "200" in msg


def test_type_text_tool(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    msg = _FakeFastMCP.last.tools["type_text"]("hello world")
    assert ("type_text", ("hello world",), {}) in b.calls
    assert "11" in msg  # char count


def test_key_tool(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    _FakeFastMCP.last.tools["key"]("enter")
    assert ("key", ("enter",), {}) in b.calls


def test_wait_tool_invokes_backend_wait(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    _FakeFastMCP.last.tools["wait"](0.05)
    assert any(c[0] == "wait" for c in b.calls)


def test_read_logs_tool_returns_string(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    out = _FakeFastMCP.last.tools["read_logs"](lines=42)
    assert "42 lines" in out
    assert ("read_logs", (42,), {}) in b.calls


def test_navigate_tool_invokes_backend(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    _FakeFastMCP.last.tools["navigate"]("http://target/x")
    assert ("navigate", ("http://target/x",), {}) in b.calls


def test_screen_size_tool(patched_mcp, config_file):
    mcp_server.serve(config_path=config_file)
    out = _FakeFastMCP.last.tools["screen_size"]()
    assert out == {"width": 320, "height": 480}


def test_locate_tool_uses_vision_and_screenshots(patched_mcp, config_file, monkeypatch):
    # Inject a vision client via env so default_client picks it; OR patch directly.
    stub = StubVisionClient(default='{"x": 50, "y": 75, "found": true}')
    monkeypatch.setattr("verify.vision.default_client", lambda: stub)

    mcp_server.serve(config_path=config_file)
    b = _backend(_FakeFastMCP.last)
    out = _FakeFastMCP.last.tools["locate"]("the login button")
    assert out == {"found": True, "x": 50, "y": 75}
    # A screenshot was taken to feed vision.
    assert any(c[0] == "screenshot" for c in b.calls)


def test_locate_tool_returns_not_found(patched_mcp, config_file, monkeypatch):
    stub = StubVisionClient(default='{"x": 0, "y": 0, "found": false, "reason": "absent"}')
    monkeypatch.setattr("verify.vision.default_client", lambda: stub)

    mcp_server.serve(config_path=config_file)
    out = _FakeFastMCP.last.tools["locate"]("a unicorn")
    assert out == {"found": False, "x": None, "y": None}


def test_serve_raises_when_mcp_sdk_missing(monkeypatch, config_file):
    """If `import mcp.server.fastmcp` fails, serve must raise a clean error."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp.server.fastmcp" or name.startswith("mcp.server"):
            raise ImportError("not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r"mcp SDK not installed"):
        mcp_server.serve(config_path=config_file)


def test_serve_stops_backend_even_when_run_throws(patched_mcp, config_file):
    """If mcp.run() raises, finally-clause must still call backend.stop()."""

    class BoomFastMCP(_FakeFastMCP):
        def run(self, *a, **kw):
            super().run(*a, **kw)
            raise RuntimeError("transport died")

    import sys

    sys.modules["mcp.server.fastmcp"].FastMCP = BoomFastMCP
    with pytest.raises(RuntimeError, match="transport died"):
        mcp_server.serve(config_path=config_file)
    backend = _FakeFastMCP.last.tools["screenshot"].__closure__[0].cell_contents
    assert backend.stopped is True
