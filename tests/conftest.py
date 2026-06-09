"""Shared pytest fixtures for the verify test suite."""

from __future__ import annotations

import os
import pathlib
import sys

# Ensure tests can import the in-tree package even if it isn't installed.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from verify.backends.base import (
    Backend,
    BackendCapabilities,
    DetectionResult,
    LaunchSpec,
)


class FakeBackend(Backend):
    """In-memory backend for runner tests."""

    name = "fake"

    def __init__(self, *, screen_size: tuple[int, int] = (800, 600)) -> None:
        self._screen = screen_size
        self.started_with: LaunchSpec | None = None
        self.stopped = False
        self.events: list[tuple[str, tuple, dict]] = []
        self.url: str = ""
        self.logs: str = ""
        self.dom: dict[str, dict] = {}

    @classmethod
    def detect(cls, project_dir) -> DetectionResult:
        return DetectionResult(0, "")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_navigate=True, can_query_dom=True, has_screenshot=True, has_input=True
        )

    def start(self, spec: LaunchSpec) -> None:
        self.started_with = spec
        if spec.url:
            self.url = spec.url

    def stop(self) -> None:
        self.stopped = True

    def screen_size(self) -> tuple[int, int]:
        return self._screen

    def screenshot(self) -> bytes:
        self.events.append(("screenshot", (), {}))
        # Bare minimum PNG header — runner only checks bytes are returned.
        return b"\x89PNG\r\n\x1a\nFAKE"

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.events.append(("click", (x, y), {"button": button}))

    def type_text(self, text: str) -> None:
        self.events.append(("type_text", (text,), {}))

    def key(self, name: str) -> None:
        self.events.append(("key", (name,), {}))

    def read_logs(self, lines: int = 100) -> str:
        self.events.append(("read_logs", (lines,), {}))
        return self.logs

    def navigate(self, url: str) -> None:
        self.events.append(("navigate", (url,), {}))
        self.url = url

    def query_dom(self, selector: str):
        self.events.append(("query_dom", (selector,), {}))
        return self.dom.get(selector)

    def current_url(self) -> str:
        return self.url


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def tmp_project(tmp_path):
    """A project dir with no recognizable files."""
    return tmp_path
