"""Renode backend — embedded MCU emulation.

Renode is Antmicro's open-source full-system simulator. It boots a board (STM32,
nRF52, ESP32, ...), wires up peripherals, and exposes a Monitor port (telnet) on
which you can drive the simulation, watch UART output, write GPIOs, etc.

verify drives Renode by:
  1. Launching `renode --console --disable-gui --port <P>` in subprocess.
  2. Connecting to the Monitor TCP port (default 1234) and issuing commands.
  3. Tailing UART output from a known analyzer.
  4. For boards with a framebuffer (LCD): `analyzer.DumpFrame <path>.png` -> file.

This is the universe where "user-style" testing is firmware sending the right
bytes on a UART, the right pixels on the LCD, or the right GPIO pulses. We model
"screen" as the framebuffer when present; otherwise screenshot raises and tests
are expected to use the `log_contains` expect (UART).
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import tempfile
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


@register
class RenodeBackend(Backend):
    name = "renode"

    def __init__(self, *, monitor_port: int = 1234, frame_analyzer: str | None = None) -> None:
        self._monitor_port = monitor_port
        # If the board has an LCD, the user (or .resc) creates an analyzer for it.
        # We need its machine path (e.g. "sysbus.lcd") to call DumpFrame on it.
        self._frame_analyzer = frame_analyzer
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._uart_logs: deque[str] = deque(maxlen=20_000)
        self._monitor_logs: deque[str] = deque(maxlen=5_000)
        self._reader_thread: threading.Thread | None = None
        self._screen_size_cache: tuple[int, int] = (240, 320)  # cheap default

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        if glob_any(project_dir, "*.resc", "**/*.resc"):
            return DetectionResult(95, ".resc Renode script found")
        if has_file(project_dir, "platformio.ini"):
            pio = project_dir / "platformio.ini"
            if file_contains(
                pio,
                "stm32",
                "STM32",
                "nrf52",
                "esp32",
                "atmelavr",
                "atmelsam",
                "espressif",
            ):
                return DetectionResult(80, "PlatformIO with MCU target")
            return DetectionResult(50, "PlatformIO project")
        if glob_any(project_dir, "*.ioc"):
            return DetectionResult(75, "STM32CubeMX .ioc")
        cml = project_dir / "CMakeLists.txt"
        if cml.is_file() and file_contains(cml, "arm-none-eabi", "cortex-m"):
            return DetectionResult(70, "ARM Cortex-M CMake")
        return DetectionResult(0, "")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        for name in ("renode", "renode-test"):
            if shutil.which(name):
                return True, ""
        return False, "renode binary not on PATH. https://renode.io/#downloads"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_navigate=False, can_query_dom=False, has_screenshot=bool(self._frame_analyzer)
        )

    # ---- lifecycle -------------------------------------------------------

    def start(self, spec: LaunchSpec) -> None:
        if not spec.command:
            raise ValueError(
                "renode backend needs launch.command (path to a .resc script)"
            )
        self._launch_renode(spec)
        self._connect_monitor()
        # Resource script likely does the boot already (start command at end of .resc).
        # If user wants to defer, they leave `start` out and call _send("start") manually.
        if spec.wait_after:
            self.wait(spec.wait_after)

    def _launch_renode(self, spec: LaunchSpec) -> None:
        resc = spec.command
        renode = shutil.which("renode") or "renode"
        cmd = [
            renode,
            "--console",
            "--disable-gui",
            "--port",
            str(self._monitor_port),
            resc,
        ]
        cmd.extend(spec.args)
        env = os.environ.copy()
        env.update(spec.env)
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
                self._uart_logs.append(line.decode("utf-8", errors="replace").rstrip())

        threading.Thread(target=drain, daemon=True).start()

    def _connect_monitor(self, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", self._monitor_port), timeout=2)
                s.settimeout(0.5)
                self._sock = s
                return
            except OSError as e:
                last_err = e
                time.sleep(0.5)
        raise RuntimeError(f"could not connect to Renode monitor: {last_err}")

    def stop(self) -> None:
        try:
            if self._sock:
                try:
                    self._sock.sendall(b"quit\n")
                except Exception:
                    pass
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        if self._proc:
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

    def send(self, command: str) -> str:
        """Send one Monitor command, return whatever bytes drain immediately."""
        if not self._sock:
            raise RuntimeError("renode monitor not connected")
        self._monitor_logs.append(f"> {command}")
        self._sock.sendall((command + "\n").encode())
        chunks: list[bytes] = []
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        except (socket.timeout, BlockingIOError):
            pass
        out = b"".join(chunks).decode("utf-8", errors="replace")
        for line in out.splitlines():
            self._monitor_logs.append(line)
        return out

    def screen_size(self) -> tuple[int, int]:
        return self._screen_size_cache

    def screenshot(self) -> bytes:
        if not self._frame_analyzer:
            raise RuntimeError(
                "renode backend has no framebuffer configured. Set frame_analyzer "
                "to e.g. 'sysbus.lcd' or use log_contains instead of vision."
            )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = Path(f.name)
        try:
            self.send(f"{self._frame_analyzer} DumpFrame @{tmp}")
            time.sleep(0.05)
            data = tmp.read_bytes()
            return data
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def click(self, x: int, y: int, button: str = "left") -> None:
        # MCUs don't have a mouse. If user has a virtual touch peripheral, they
        # can override this with a Monitor command via launch.options.
        raise NotImplementedError(
            "renode backend has no click. Drive GPIO/SPI peripherals via UART input "
            "or override with a custom Monitor command."
        )

    def type_text(self, text: str) -> None:
        # Pipe to UART. Most .resc scripts set up an analyzer with .WriteChar.
        if not self._frame_analyzer:
            pass  # frame_analyzer not relevant here; uart path is separate
        # Heuristic: send via the Monitor `sysbus.uart0 WriteLine` if present.
        # Users override via options if they have a different UART name.
        self.send(f'sysbus.uart0 WriteLine "{text}"')

    def key(self, name: str) -> None:
        # Map a few standard names to ASCII control chars on UART.
        mapped = {
            "enter": "\r",
            "tab": "\t",
            "escape": "\x1b",
            "backspace": "\x08",
            "space": " ",
        }.get(name.lower(), name)
        self.send(f'sysbus.uart0 WriteChar 0x{ord(mapped[0]):02x}')

    def read_logs(self, lines: int = 100) -> str:
        return "\n".join(list(self._uart_logs)[-lines:])
