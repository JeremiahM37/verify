"""Linux desktop backend — Xvfb + xdotool.

The app runs inside an isolated Xvfb display. We screenshot with xwd→PNG (no PIL
X grab required) and send input with xdotool. This means the test never touches
the user's real display, and CI works the same way as a developer machine.

Detection: presence of a Linux-only build artifact (CMakeLists with Qt/Gtk,
.desktop file, a built ELF binary in `build/`). It's a low-confidence backend
because tons of repos build Linux binaries — we only claim high confidence when
the project clearly targets a GUI.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

from verify.backends.base import (
    Backend,
    BackendCapabilities,
    DetectionResult,
    LaunchSpec,
)
from verify.backends.registry import register
from verify.detect import file_contains, glob_any, has_file


_KEY_MAP = {
    "enter": "Return",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "delete": "Delete",
}


def _which(name: str) -> str | None:
    return shutil.which(name)


@register
class LinuxDesktopBackend(Backend):
    name = "linux_desktop"

    def __init__(
        self,
        *,
        display: str = ":99",
        screen_size: tuple[int, int] = (1280, 800),
    ) -> None:
        self._display = display
        self._screen_size = screen_size
        self._xvfb: subprocess.Popen[bytes] | None = None
        self._app: subprocess.Popen[bytes] | None = None
        self._logs: deque[str] = deque(maxlen=10_000)
        self._log_thread = None

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        # Strong: AppImage build, .desktop file, Qt/Gtk in CMake, Tauri config.
        if has_file(project_dir, "AppImageBuilder.yml"):
            return DetectionResult(70, "AppImageBuilder.yml")
        if glob_any(project_dir, "*.desktop", "**/*.desktop"):
            return DetectionResult(60, ".desktop entry")
        if has_file(project_dir, "src-tauri/tauri.conf.json"):
            return DetectionResult(75, "Tauri project")
        cml = project_dir / "CMakeLists.txt"
        if cml.is_file() and file_contains(cml, "Qt5", "Qt6", "GTK", "wxWidgets"):
            return DetectionResult(70, "CMakeLists with Qt/Gtk/wx")
        return DetectionResult(0, "")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        missing = [t for t in ("Xvfb", "xdotool", "xwd") if not _which(t)]
        if missing:
            return (
                False,
                f"missing host tools: {', '.join(missing)}. apt install xvfb xdotool x11-apps",
            )
        return True, ""

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(can_navigate=False, can_query_dom=False)

    # ---- lifecycle -------------------------------------------------------

    def start(self, spec: LaunchSpec) -> None:
        self._start_xvfb()
        self._start_app(spec)
        if spec.wait_after:
            self.wait(spec.wait_after)

    def _start_xvfb(self) -> None:
        w, h = self._screen_size
        self._xvfb = subprocess.Popen(
            ["Xvfb", self._display, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Xvfb needs a moment to bind the display socket.
        for _ in range(50):
            if Path(f"/tmp/.X11-unix/X{self._display.lstrip(':')}").exists():
                return
            time.sleep(0.05)
        # fallback: still proceed; the app may handle the race fine.

    def _start_app(self, spec: LaunchSpec) -> None:
        if not spec.command:
            raise ValueError("linux_desktop backend needs launch.command")
        env = os.environ.copy()
        env["DISPLAY"] = self._display
        env.update(spec.env)
        cmd = shlex.split(spec.command) if " " in spec.command else [spec.command]
        cmd.extend(spec.args)
        self._app = subprocess.Popen(
            cmd,
            env=env,
            cwd=spec.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._start_log_drain()

    def _start_log_drain(self) -> None:
        import threading

        def drain():
            assert self._app is not None and self._app.stdout is not None
            for line in iter(self._app.stdout.readline, b""):
                try:
                    self._logs.append(line.decode("utf-8", errors="replace").rstrip())
                except Exception:
                    pass

        self._log_thread = threading.Thread(target=drain, daemon=True)
        self._log_thread.start()

    def stop(self) -> None:
        for p in (self._app, self._xvfb):
            if not p:
                continue
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._app = None
        self._xvfb = None

    # ---- ops -------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        e = os.environ.copy()
        e["DISPLAY"] = self._display
        return e

    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    def screenshot(self) -> bytes:
        """xwd | convert -> PNG, or fall back to scrot."""
        if _which("convert"):
            xwd = subprocess.run(
                ["xwd", "-root", "-display", self._display, "-silent"],
                capture_output=True,
                check=True,
            )
            png = subprocess.run(
                ["convert", "xwd:-", "png:-"],
                input=xwd.stdout,
                capture_output=True,
                check=True,
            )
            return png.stdout
        if _which("scrot"):
            with subprocess.Popen(
                ["scrot", "-o", "/dev/stdout"],
                env=self._env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as p:
                out, _ = p.communicate(timeout=10)
                return out
        raise RuntimeError(
            "need ImageMagick `convert` or `scrot` to encode screenshots"
        )

    def click(self, x: int, y: int, button: str = "left") -> None:
        btn = {"left": "1", "middle": "2", "right": "3"}[button]
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", btn],
            env=self._env(),
            check=True,
        )

    def type_text(self, text: str) -> None:
        subprocess.run(
            ["xdotool", "type", "--delay", "10", "--", text],
            env=self._env(),
            check=True,
        )

    def key(self, name: str) -> None:
        mapped = _KEY_MAP.get(name.lower(), name)
        subprocess.run(["xdotool", "key", mapped], env=self._env(), check=True)

    def read_logs(self, lines: int = 100) -> str:
        return "\n".join(list(self._logs)[-lines:])
