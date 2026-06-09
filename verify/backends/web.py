"""Web backend — Playwright over Chromium.

Detection: project has a frontend package.json, index.html, or `verify.yaml`
launches with a `url:`. Lowest-friction backend, used for both real web apps and
as the substrate for any mobile-web or hybrid UI.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from verify.backends.base import (
    Backend,
    BackendCapabilities,
    DetectionResult,
    LaunchSpec,
)
from verify.backends.registry import register
from verify.detect import file_contains, has_file


@register
class WebBackend(Backend):
    name = "web"

    def __init__(self, *, headless: bool = True, viewport: tuple[int, int] = (1280, 800)) -> None:
        self._headless = headless
        self._viewport = viewport
        self._pw = None  # playwright instance
        self._browser = None
        self._context = None
        self._page = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_buf: list[str] = []
        self._console_buf: list[str] = []

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        if has_file(project_dir, "package.json"):
            pkg = project_dir / "package.json"
            if file_contains(
                pkg, "react", "vue", "next", "svelte", "vite", "astro", "remix"
            ):
                return DetectionResult(80, "package.json with web framework")
            return DetectionResult(40, "package.json present")
        if has_file(project_dir, "index.html"):
            return DetectionResult(50, "index.html in project root")
        return DetectionResult(0, "")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return False, "playwright not installed (pip install verify-cli[web])"
        return True, ""

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_navigate=True, can_query_dom=True, has_logs=True
        )

    # ---- lifecycle -------------------------------------------------------

    def start(self, spec: LaunchSpec) -> None:
        if spec.command:
            self._start_dev_server(spec)
        self._start_browser()
        if spec.url:
            self.navigate(spec.url)
        if spec.wait_after:
            self.wait(spec.wait_after)

    def _start_dev_server(self, spec: LaunchSpec) -> None:
        env = os.environ.copy()
        env.update(spec.env)
        cmd = [spec.command] if spec.command else []
        if spec.args:
            cmd.extend(spec.args)
        # If user gave a string with spaces, split for shell-less exec.
        if len(cmd) == 1 and " " in cmd[0]:
            cmd = shlex.split(cmd[0])
        self._proc = subprocess.Popen(
            cmd,
            cwd=spec.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def _start_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            viewport={"width": self._viewport[0], "height": self._viewport[1]}
        )
        self._page = self._context.new_page()
        self._page.on("console", lambda msg: self._console_buf.append(f"[{msg.type}] {msg.text}"))
        self._page.on("pageerror", lambda exc: self._console_buf.append(f"[pageerror] {exc}"))

    def stop(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._pw = self._browser = self._context = self._page = None
        self._proc = None

    # ---- ops -------------------------------------------------------------

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("WebBackend.start() must be called before use")
        return self._page

    def screen_size(self) -> tuple[int, int]:
        return self._viewport

    def screenshot(self) -> bytes:
        return self._require_page().screenshot(type="png", full_page=False)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self._require_page().mouse.click(x, y, button=button)

    def click_selector(self, selector: str) -> None:
        self._require_page().click(selector)

    def type_text(self, text: str) -> None:
        self._require_page().keyboard.type(text)

    def key(self, name: str) -> None:
        # Normalize a few common names.
        mapped = {
            "enter": "Enter",
            "escape": "Escape",
            "esc": "Escape",
            "tab": "Tab",
            "backspace": "Backspace",
            "space": "Space",
            "up": "ArrowUp",
            "down": "ArrowDown",
            "left": "ArrowLeft",
            "right": "ArrowRight",
            "home": "Home",
            "end": "End",
            "pageup": "PageUp",
            "pagedown": "PageDown",
            "delete": "Delete",
        }.get(name.lower(), name)
        self._require_page().keyboard.press(mapped)

    def read_logs(self, lines: int = 100) -> str:
        joined: list[str] = []
        if self._proc and self._proc.stdout:
            try:
                # Drain whatever is available without blocking.
                self._proc.stdout.flush()
            except Exception:
                pass
        joined.extend(self._console_buf[-lines:])
        return "\n".join(joined[-lines:])

    def navigate(self, url: str) -> None:
        self._require_page().goto(url)

    def query_dom(self, selector: str) -> dict[str, Any] | None:
        page = self._require_page()
        loc = page.locator(selector)
        if loc.count() == 0:
            return None
        first = loc.first
        return {
            "selector": selector,
            "count": loc.count(),
            "text": first.text_content(),
            "visible": first.is_visible(),
            "bounding_box": first.bounding_box(),
        }

    # ---- web-specific helpers --------------------------------------------

    def current_url(self) -> str:
        return self._require_page().url
