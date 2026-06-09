"""Android backend — adb screencap + input + logcat.

The user already has an emulator or device running (or `start.command` boots
one). We grab pixels with `adb exec-out screencap -p` (raw PNG over stdout),
inject input with `adb shell input ...`, and tail `adb logcat`.

Detection: AndroidManifest.xml or build.gradle with android plugins.

Why not UIAutomator? Vision-first means we don't need the UI tree, and dump+parse
adds latency. UIAutomator2 is supported as an optional locator when you really
want semantic selectors, but the default loop is screenshot+vision+coords.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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
from verify.detect import file_contains, glob_any, has_file
from verify.sandbox import Sandbox


# adb shell input keyevent <name> — these are the codes/names adb accepts.
_KEY_MAP = {
    "enter": "KEYCODE_ENTER",
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "menu": "KEYCODE_MENU",
    "tab": "KEYCODE_TAB",
    "escape": "KEYCODE_ESCAPE",
    "esc": "KEYCODE_ESCAPE",
    "backspace": "KEYCODE_DEL",
    "delete": "KEYCODE_FORWARD_DEL",
    "space": "KEYCODE_SPACE",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
}


@register
class AndroidBackend(Backend):
    name = "android"

    def __init__(
        self,
        *,
        serial: str | None = None,
        adb: str = "adb",
        docker_image: str | None = None,
        docker_adb_port: int = 5555,
        docker_ready_log: str = "emulator: INFO: boot completed",
        docker_boot_timeout: float = 300,
    ) -> None:
        self._serial = serial
        self._adb = adb
        self._docker_image = docker_image
        self._docker_adb_port = docker_adb_port
        self._docker_ready_log = docker_ready_log
        self._docker_boot_timeout = docker_boot_timeout
        self._sandbox: Sandbox | None = None
        self._launcher: subprocess.Popen[bytes] | None = None  # optional emulator boot
        self._logcat: subprocess.Popen[bytes] | None = None
        self._logs: deque[str] = deque(maxlen=20_000)
        self._screen_size_cache: tuple[int, int] | None = None

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        if has_file(project_dir, "AndroidManifest.xml"):
            return DetectionResult(95, "AndroidManifest.xml")
        if glob_any(
            project_dir, "app/src/main/AndroidManifest.xml", "**/AndroidManifest.xml"
        ):
            return DetectionResult(90, "nested AndroidManifest.xml")
        gradle = project_dir / "build.gradle"
        gradle_kts = project_dir / "build.gradle.kts"
        for g in (gradle, gradle_kts):
            if g.is_file() and file_contains(g, "com.android.application", "com.android.library"):
                return DetectionResult(85, "build.gradle android plugin")
        if has_file(project_dir, "android/app/build.gradle"):
            return DetectionResult(80, "react-native / capacitor android dir")
        return DetectionResult(0, "")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if not shutil.which("adb"):
            return False, "adb not on PATH. Install Android platform-tools."
        return True, ""

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(can_navigate=False, can_query_dom=False)

    # ---- lifecycle -------------------------------------------------------

    def start(self, spec: LaunchSpec) -> None:
        # Option A (preferred for CI): boot an emulator inside a labeled,
        # tracked Docker sandbox. The sandbox is registered for atexit cleanup
        # so the container can't outlive this process even if verify crashes.
        if self._docker_image:
            self._sandbox = Sandbox.run(
                image=self._docker_image,
                kind="android-emulator",
                ports=[self._docker_adb_port],
                privileged=True,  # KVM passthrough; required for emulator perf
            )
            if not self._sandbox.wait_for_log(
                self._docker_ready_log, timeout=self._docker_boot_timeout
            ):
                raise TimeoutError(
                    f"emulator container never logged {self._docker_ready_log!r} "
                    f"(image={self._docker_image})"
                )
            host_port = self._sandbox.host_port_for(self._docker_adb_port)
            self._serial = f"127.0.0.1:{host_port}"
            self._adb_run(["connect", self._serial], check=False)
            self._wait_for_boot(timeout=60)
        # Option B: an external launch command (real avdmanager+emulator on host).
        elif spec.command:
            self._launcher = subprocess.Popen(
                shlex.split(spec.command) if " " in spec.command else [spec.command],
                env={**os.environ, **spec.env},
                cwd=spec.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._wait_for_boot(timeout=120)
        # Option C: attach to whatever device adb already sees (default).

        if spec.package:
            self._adb_run(["shell", "monkey", "-p", spec.package, "1"], check=False)
        self._start_logcat()
        if spec.wait_after:
            self.wait(spec.wait_after)

    def _wait_for_boot(self, timeout: float = 120) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._adb_run(
                    ["shell", "getprop", "sys.boot_completed"],
                    capture_output=True,
                    check=False,
                )
                if r.stdout.strip() == b"1":
                    return
            except FileNotFoundError:
                raise
            except Exception:
                pass
            time.sleep(2)
        raise TimeoutError("Android device never finished booting")

    def _start_logcat(self) -> None:
        self._logcat = self._adb_popen(
            ["logcat", "-v", "time"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        def drain():
            assert self._logcat is not None and self._logcat.stdout is not None
            for line in iter(self._logcat.stdout.readline, b""):
                self._logs.append(line.decode("utf-8", errors="replace").rstrip())

        threading.Thread(target=drain, daemon=True).start()

    def stop(self) -> None:
        for p in (self._logcat, self._launcher):
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
        self._logcat = None
        self._launcher = None
        if self._sandbox is not None:
            try:
                self._sandbox.stop()
            except Exception:
                pass
            self._sandbox = None

    # ---- adb plumbing ---------------------------------------------------

    def _adb_cmd(self, extra: list[str]) -> list[str]:
        base = [self._adb]
        if self._serial:
            base += ["-s", self._serial]
        return base + extra

    def _adb_run(self, args: list[str], **kw) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(self._adb_cmd(args), **kw)

    def _adb_popen(self, args: list[str], **kw) -> subprocess.Popen[bytes]:
        return subprocess.Popen(self._adb_cmd(args), **kw)

    # ---- ops -------------------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size_cache:
            return self._screen_size_cache
        r = self._adb_run(["shell", "wm", "size"], capture_output=True, check=True)
        # Output: "Physical size: 1080x2400"
        text = r.stdout.decode().strip()
        try:
            spec = text.split(":")[-1].strip()
            w, h = spec.split("x")
            self._screen_size_cache = (int(w), int(h))
        except Exception:
            self._screen_size_cache = (1080, 1920)
        return self._screen_size_cache

    def screenshot(self) -> bytes:
        # exec-out keeps the PNG bytes raw (no shell line-ending mangling).
        r = self._adb_run(
            ["exec-out", "screencap", "-p"], capture_output=True, check=True
        )
        return r.stdout

    def click(self, x: int, y: int, button: str = "left") -> None:
        # button ignored on touch.
        self._adb_run(["shell", "input", "tap", str(x), str(y)], check=True)

    def type_text(self, text: str) -> None:
        # adb shell input text can't handle spaces literally; %s is the documented escape.
        escaped = text.replace(" ", "%s")
        self._adb_run(["shell", "input", "text", escaped], check=True)

    def key(self, name: str) -> None:
        code = _KEY_MAP.get(name.lower(), name)
        self._adb_run(["shell", "input", "keyevent", code], check=True)

    def read_logs(self, lines: int = 100) -> str:
        return "\n".join(list(self._logs)[-lines:])

    def list_devices(self) -> list[str]:
        r = self._adb_run(["devices"], capture_output=True, check=True)
        out = r.stdout.decode()
        devs = []
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                devs.append(line.split("\t", 1)[0])
        return devs
