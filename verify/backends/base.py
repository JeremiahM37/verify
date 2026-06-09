"""Backend abstract interface.

Every target environment (web, Android, Linux desktop, Renode, ...) implements
this same surface. The runner and MCP server only see a Backend; they never know
or care what's underneath. That's the whole point — Claude looks at pixels and
acts, regardless of whether they came from a browser, an ADB emulator, or an
LCD framebuffer in a simulated STM32.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DetectionResult:
    """Backend's self-assessment of how well it matches a project directory.

    confidence: 0 (does not match) up to 100 (perfect match).
    reason:     short human-readable explanation, surfaced in `verify backends`.
    """

    confidence: int
    reason: str = ""


@dataclass
class BackendCapabilities:
    """What this backend can do. The runner uses these to validate steps."""

    can_navigate: bool = False
    can_query_dom: bool = False
    has_logs: bool = True
    has_screenshot: bool = True
    has_input: bool = True


@dataclass
class LaunchSpec:
    """What to launch under the backend.

    Backends interpret this loosely:
      - web:     `url` is required; `command` may start a dev server.
      - android: `package` is the app id; `command` may start an emulator.
      - renode:  `command` is the .resc script path or platform name.
      - linux/generic: `command` is the binary; `args` extends it.
    """

    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    package: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    ready_when: dict[str, Any] | None = None
    wait_after: float = 0.0


class Backend(ABC):
    """Common verb set every target must support."""

    name: str = "base"

    # ---- discovery -------------------------------------------------------

    @classmethod
    @abstractmethod
    def detect(cls, project_dir: Path) -> DetectionResult:
        """Score how well this backend matches the project directory."""

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        """Whether host has the tools to actually run this backend.

        Returns (ok, reason). When ok=False, `reason` should tell the user
        what they're missing (e.g. "playwright not installed: pip install
        verify-cli[web]").
        """
        return True, ""

    # ---- lifecycle -------------------------------------------------------

    @abstractmethod
    def start(self, spec: LaunchSpec) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    # ---- introspection ---------------------------------------------------

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    @abstractmethod
    def screen_size(self) -> tuple[int, int]: ...

    # ---- vision ----------------------------------------------------------

    @abstractmethod
    def screenshot(self) -> bytes:
        """Return a PNG of the current target screen."""

    # ---- input -----------------------------------------------------------

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left") -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def key(self, name: str) -> None:
        """Press a named key. Names normalize across backends:

        enter, escape, tab, backspace, space, up, down, left, right,
        home, end, pageup, pagedown, delete, back (android only).
        """

    # ---- logs ------------------------------------------------------------

    @abstractmethod
    def read_logs(self, lines: int = 100) -> str: ...

    # ---- optional: navigate (web only by default) ------------------------

    def navigate(self, url: str) -> None:
        raise NotImplementedError(f"{self.name} cannot navigate")

    def query_dom(self, selector: str) -> dict[str, Any] | None:
        raise NotImplementedError(f"{self.name} has no DOM")

    def wait(self, seconds: float) -> None:
        import time

        time.sleep(seconds)

    # ---- context manager sugar ------------------------------------------

    def __enter__(self) -> "Backend":
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.stop()
        except Exception:
            pass
