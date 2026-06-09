"""End-to-end test using the REAL vision pipeline (Ollama, locally hosted).

Skips automatically if no Ollama at the configured host or if a vision-capable
model isn't pulled. When it does run, it exercises the entire stack:

    Playwright -> live HTTP server -> screenshot bytes -> Ollama vision model
    -> JSON pass/fail -> RunReport.

This is the test that proves verify literally sees what the user sees. The
PageAwareVisionStub e2e tests are good for CI but they cheat by peeking at the
DOM. This one looks at actual pixels.
"""

from __future__ import annotations

import os
import urllib.request

import pytest

from verify.backends.web import WebBackend
from verify.config import parse
from verify.runner import run
from verify.vision import OllamaVisionClient

from .sample_web_app.server import SampleServer


OLLAMA_HOST = os.environ.get("VERIFY_TEST_OLLAMA_HOST", "http://192.168.1.86:11434")
OLLAMA_MODEL = os.environ.get("VERIFY_TEST_OLLAMA_MODEL", "gemma4:e4b")


def _ollama_with_vision_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2) as r:
            import json

            data = json.loads(r.read())
            return any(m.get("name") == OLLAMA_MODEL for m in data.get("models", []))
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        WebBackend.is_available()[0] is False, reason="playwright not installed"
    ),
    pytest.mark.skipif(
        not _ollama_with_vision_reachable(),
        reason=f"local Ollama with {OLLAMA_MODEL} not reachable at {OLLAMA_HOST}",
    ),
]


@pytest.fixture(scope="module")
def server():
    s = SampleServer()
    s.start()
    yield s
    s.stop()


@pytest.fixture
def vision():
    return OllamaVisionClient(model=OLLAMA_MODEL, host=OLLAMA_HOST, timeout=60)


def test_real_vision_passes_clean_page(server, vision, tmp_path):
    """The good page has no error banner — vision must agree."""
    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": server.url("/good.html"), "wait_after": 0.3},
            "steps": [
                {
                    "name": "no error banner visible",
                    "expect": {"vision": "no error banner is visible on this page"},
                }
            ],
        }
    )
    backend = WebBackend(headless=True, viewport=(1024, 768))
    report = run(cfg, tmp_path, vision=vision, backend=backend)

    assert report.passed, (
        f"vision incorrectly flagged the clean page: "
        f"{report.steps[0].expect.vision.reason}"
    )


def test_real_vision_catches_visible_error_banner(server, vision, tmp_path):
    """The broken page has a visible red error banner — vision must FAIL.

    This is the headline test: the kind of bug where the backend test passes
    but the UI obviously shows an error to a real user.
    """
    cfg = parse(
        {
            "backend": "web",
            "launch": {"url": server.url("/broken.html"), "wait_after": 0.3},
            "steps": [
                {
                    "name": "no error banner visible",
                    "expect": {"vision": "no error banner is visible on this page"},
                }
            ],
        }
    )
    backend = WebBackend(headless=True, viewport=(1024, 768))
    report = run(cfg, tmp_path, vision=vision, backend=backend)

    assert not report.passed, "vision missed the visible red error banner"
    reason = report.steps[0].expect.vision.reason.lower()
    # The model should mention the visible error or network text.
    assert any(
        kw in reason
        for kw in ("error", "banner", "network", "unavailable", "red")
    ), f"vision reason didn't reference the bug: {reason!r}"
