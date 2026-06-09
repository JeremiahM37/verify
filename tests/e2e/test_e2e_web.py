"""End-to-end: real Playwright + real HTTP server, vision faked deterministically.

This is the test that proves the whole verify stack works together:

  Click runner -> WebBackend (Playwright) -> live HTTP server -> screenshot
  -> vision (stubbed via PNG content sniffing) -> RunReport.

The vision stub looks at the actual page title returned by Playwright to decide
PASS/FAIL — so vision flips with the page that loaded, just like the real model
would. Without a stub we'd need a paid API key in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verify.backends.web import WebBackend
from verify.config import parse
from verify.runner import run
from verify.vision import VisionResult

from .sample_web_app.server import SampleServer


pytestmark = pytest.mark.skipif(
    WebBackend.is_available()[0] is False, reason="playwright not installed"
)


# ---- vision stub that sniffs the actual page state ------------------------


class PageAwareVisionStub:
    """Decides PASS/FAIL by inspecting Playwright DOM, not a screenshot.

    The runner only knows it has a VisionClient; this implementation cheats by
    holding a reference to the backend so it can check `query_dom('.error-banner')`.
    That gives us deterministic e2e coverage of the entire stack without hitting
    a remote vision API.
    """

    def __init__(self, backend: WebBackend) -> None:
        self.backend = backend
        self.calls: list[str] = []

    def ask(self, image_png: bytes, prompt: str, *, max_tokens: int = 400) -> str:
        self.calls.append(prompt)
        has_error = self.backend.query_dom(".error-banner") is not None
        # Encode pass/fail in a JSON shape the real vision module expects.
        if "no error banner" in prompt.lower() or "no error" in prompt.lower():
            if has_error:
                return '{"pass": false, "reason": "red error banner is visible"}'
            return '{"pass": true, "reason": "no error banner visible"}'
        if "dashboard" in prompt.lower():
            return '{"pass": true, "reason": "dashboard contents visible"}'
        return '{"pass": true, "reason": "looks fine"}'


# ---- fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    s = SampleServer()
    s.start()
    yield s
    s.stop()


# ---- tests ---------------------------------------------------------------


def test_e2e_good_page_passes(server, tmp_path):
    """Good page -> verify should pass: dashboard visible, no error banner."""
    backend = WebBackend(headless=True, viewport=(1024, 768))
    vision = PageAwareVisionStub(backend)

    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": server.url("/good.html"), "wait_after": 0.2},
            "steps": [
                {
                    "name": "dashboard renders",
                    "expect": {"vision": "the dashboard panel is visible"},
                },
                {
                    "name": "no error banner",
                    "expect": {"vision": "no error banner is visible"},
                },
            ],
        }
    )
    report = run(cfg, tmp_path, vision=vision, backend=backend)

    assert report.passed, report.summary()
    assert all(s.passed for s in report.steps)
    # Verify the vision stub was actually consulted (not skipped).
    assert len(vision.calls) >= 2


def test_e2e_broken_page_catches_visible_error(server, tmp_path):
    """Broken page -> verify must FAIL on the 'no error banner' step.

    This is the exact failure mode the user described: backend says everything
    is fine, but a red error banner is rendered. Without vision we'd miss it;
    with vision verify catches it.
    """
    backend = WebBackend(headless=True, viewport=(1024, 768))
    vision = PageAwareVisionStub(backend)

    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": server.url("/broken.html"), "wait_after": 0.2},
            "steps": [
                {
                    "name": "no error banner",
                    "expect": {"vision": "no error banner is visible"},
                },
            ],
        }
    )
    report = run(cfg, tmp_path, vision=vision, backend=backend)

    assert not report.passed
    failed = report.steps[0]
    assert failed.expect.vision.passed is False
    assert "red error banner" in failed.expect.vision.reason
    # And we should have a screenshot stashed for forensics.
    assert failed.screenshot_png is not None
    assert failed.screenshot_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_e2e_drives_actions_then_checks_vision(server, tmp_path):
    """Action + vision combo: click the button, then verify the page state."""
    backend = WebBackend(headless=True, viewport=(1024, 768))
    vision = PageAwareVisionStub(backend)

    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": server.url("/good.html"), "wait_after": 0.2},
            "steps": [
                {
                    "name": "click open button",
                    "actions": [
                        {"click": {"selector": "#open"}},
                        {"wait": 0.1},
                    ],
                    "expect": {"vision": "no error banner is visible"},
                },
            ],
        }
    )
    report = run(cfg, tmp_path, vision=vision, backend=backend)
    assert report.passed, report.summary()
    assert report.steps[0].actions[0].action.type == "click"
    assert report.steps[0].actions[0].ok


def test_e2e_log_assertion_via_console(server, tmp_path):
    """Smoke: log_contains expectation reads the console log buffer."""
    backend = WebBackend(headless=True, viewport=(1024, 768))

    # No vision used here.
    cfg = parse(
        {
            "backend": "web",
            "launch": {
                "url": "data:text/html,<script>console.log('VERIFY-PROBE-OK')</script>",
                "wait_after": 0.3,
            },
            "steps": [
                {
                    "name": "console probe printed",
                    "expect": {"log_contains": "VERIFY-PROBE-OK"},
                },
            ],
        }
    )
    report = run(cfg, tmp_path, backend=backend)
    assert report.passed, report.summary()


def test_e2e_setup_error_when_url_unreachable(tmp_path):
    """If the dev server doesn't exist, runner should produce a clean error."""
    backend = WebBackend(headless=True, viewport=(800, 600))
    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": "http://127.0.0.1:1/nope", "wait_after": 0.1},
            "steps": [{"name": "x"}],
        }
    )
    report = run(cfg, tmp_path, backend=backend)
    assert report.setup_error
    assert not report.passed
