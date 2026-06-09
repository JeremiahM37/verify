"""Generic backend — host-screen fallback.

Runs the binary under the host's real display, captures pixels with `mss` (works
on Linux/macOS/Windows), and injects input through the platform's native tool:
xdotool on Linux, cliclick on macOS, PowerShell `SendKeys` on Windows.

This is the path that just works when no specialized backend matches. It is the
least sandboxed option — the app is on YOUR desktop — so prefer dedicated
backends when possible.
"""

from __future__ import annotations

import io
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
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


_LINUX_KEY = {
    "enter": "Return",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


@register
class GenericBackend(Backend):
    name = "generic"

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._logs: deque[str] = deque(maxlen=10_000)
        self._os = platform.system()  # "Linux" | "Darwin" | "Windows"

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        # Bottom of the barrel: confidence 1. Better than nothing, never wins
        # against a real match.
        return DetectionResult(1, "fallback for any project")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import mss  # noqa: F401
        except ImportError:
            return False, "mss not installed (pip install verify-cli[desktop])"
        return True, ""

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(can_navigate=False, can_query_dom=False)

    # ---- lifecycle -------------------------------------------------------

    def start(self, spec: LaunchSpec) -> None:
        if spec.command:
            env = os.environ.copy()
            env.update(spec.env)
            cmd = shlex.split(spec.command) if " " in spec.command else [spec.command]
            cmd.extend(spec.args)
            self._proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=spec.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            def drain():
                assert self._proc is not None and self._proc.stdout is not None
                for line in iter(self._proc.stdout.readline, b""):
                    self._logs.append(line.decode("utf-8", errors="replace").rstrip())

            threading.Thread(target=drain, daemon=True).start()
        if spec.wait_after:
            self.wait(spec.wait_after)

    def stop(self) -> None:
        if not self._proc:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    # ---- ops -------------------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        import mss

        with mss.mss() as sct:
            mon = sct.monitors[1]
            return (mon["width"], mon["height"])

    def screenshot(self) -> bytes:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    def click(self, x: int, y: int, button: str = "left") -> None:
        if self._os == "Linux":
            btn = {"left": "1", "middle": "2", "right": "3"}[button]
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y), "click", btn], check=True
            )
        elif self._os == "Darwin":
            # cliclick: `cliclick c:x,y`
            subprocess.run(["cliclick", f"c:{x},{y}"], check=True)
        else:  # Windows
            self._powershell(
                f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y}); "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait(' '); "  # placeholder; real click uses mouse_event
            )

    def type_text(self, text: str) -> None:
        if self._os == "Linux":
            subprocess.run(["xdotool", "type", "--delay", "10", "--", text], check=True)
        elif self._os == "Darwin":
            subprocess.run(["cliclick", f"t:{text}"], check=True)
        else:
            safe = text.replace('"', "`\"")
            self._powershell(
                "Add-Type -AssemblyName System.Windows.Forms; "
                f'[System.Windows.Forms.SendKeys]::SendWait("{safe}")'
            )

    def key(self, name: str) -> None:
        low = name.lower()
        if self._os == "Linux":
            subprocess.run(["xdotool", "key", _LINUX_KEY.get(low, name)], check=True)
        elif self._os == "Darwin":
            mac_map = {
                "enter": "return",
                "tab": "tab",
                "escape": "esc",
                "backspace": "backspace",
                "space": "space",
            }
            subprocess.run(["cliclick", f"kp:{mac_map.get(low, name)}"], check=True)
        else:
            win_map = {
                "enter": "{ENTER}",
                "tab": "{TAB}",
                "escape": "{ESC}",
                "backspace": "{BS}",
                "space": " ",
            }
            self._powershell(
                "Add-Type -AssemblyName System.Windows.Forms; "
                f'[System.Windows.Forms.SendKeys]::SendWait("{win_map.get(low, name)}")'
            )

    def read_logs(self, lines: int = 100) -> str:
        return "\n".join(list(self._logs)[-lines:])

    # ---- platform helpers -----------------------------------------------

    def _powershell(self, script: str) -> None:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
