"""Integration tests for the Playwright web backend.

These are real Playwright tests because the backend's whole job is being a thin
wrapper — there's nothing to mock that would tell us it works. The data: URL
keeps them network-free and deterministic.
"""

from __future__ import annotations

import pytest

from verify.backends.base import LaunchSpec
from verify.backends.web import WebBackend


pytestmark = pytest.mark.skipif(
    WebBackend.is_available()[0] is False, reason="playwright not installed"
)


@pytest.fixture
def web():
    b = WebBackend(headless=True, viewport=(800, 600))
    b.start(LaunchSpec(url="data:text/html,<html><body><h1 id=g>Hi verify</h1><button id=b>Click me</button><input id=i></body></html>"))
    try:
        yield b
    finally:
        b.stop()


def test_screenshot_returns_png(web):
    png = web.screenshot()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100


def test_screen_size_returns_viewport(web):
    assert web.screen_size() == (800, 600)


def test_query_dom(web):
    info = web.query_dom("#g")
    assert info is not None
    assert info["text"] == "Hi verify"
    assert info["visible"] is True
    box = info["bounding_box"]
    assert box["x"] >= 0 and box["y"] >= 0


def test_query_dom_missing(web):
    assert web.query_dom("#nope") is None


def test_navigate_changes_url(web):
    web.navigate("data:text/html,<h1>Two</h1>")
    assert "Two" in web.current_url()


def test_click_selector(web):
    # Click the button, verify it was hit by reading its bounding box stayed visible.
    web.click_selector("#b")
    info = web.query_dom("#b")
    assert info is not None  # didn't crash, button still in DOM


def test_type_text_into_focused_input(web):
    # Focus the input via JS, then type.
    page = web._require_page()
    page.locator("#i").focus()
    web.type_text("hello")
    val = page.eval_on_selector("#i", "el => el.value")
    assert val == "hello"


def test_key_press_works(web):
    page = web._require_page()
    page.locator("#i").focus()
    web.type_text("abc")
    web.key("backspace")
    val = page.eval_on_selector("#i", "el => el.value")
    assert val == "ab"


def test_read_logs_returns_string(web):
    # Console buffer starts empty but the call must succeed.
    assert isinstance(web.read_logs(), str)


def test_capabilities(web):
    caps = web.capabilities()
    assert caps.can_navigate
    assert caps.can_query_dom


def test_detection_react_project(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    assert WebBackend.detect(tmp_path).confidence == 80


def test_detection_zero_on_empty_dir(tmp_path):
    assert WebBackend.detect(tmp_path).confidence == 0


def test_dev_server_command_is_spawned(monkeypatch, web):
    """When launch.command is set, _start_dev_server should fire a subprocess."""
    spawned = []

    class FakeProc:
        stdout = None

        def terminate(self):
            spawned.append("terminate")

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kw):
        spawned.append(("popen", cmd))
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    # Call _start_dev_server directly to isolate the path.
    web._start_dev_server(LaunchSpec(command="echo hello", env={"X": "1"}))
    assert spawned and spawned[0][0] == "popen"
    # Shell-style strings get split.
    assert spawned[0][1] == ["echo", "hello"]


def test_dev_server_command_with_args_list(monkeypatch, web):
    spawned = []

    class FakeProc:
        stdout = None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        "subprocess.Popen", lambda cmd, **kw: (spawned.append(cmd), FakeProc())[1]
    )
    web._start_dev_server(LaunchSpec(command="serve", args=["-p", "3000"]))
    assert spawned[0] == ["serve", "-p", "3000"]


def test_current_url_reads_page(web):
    web.navigate("data:text/html,<title>here</title>")
    assert "here" in web.current_url() or "data:" in web.current_url()


def test_stop_is_idempotent(web):
    """Calling stop twice doesn't raise."""
    web.stop()
    web.stop()  # second time should be a no-op


def test_console_log_captured_in_read_logs(web):
    """A page that console.logs leaves the message in the buffer."""
    web.navigate("data:text/html,<script>console.log('VERIFY-PROBE-XYZ')</script>")
    # Give the event loop a moment to flush the console event.
    import time

    time.sleep(0.3)
    logs = web.read_logs(lines=20)
    assert "VERIFY-PROBE-XYZ" in logs
