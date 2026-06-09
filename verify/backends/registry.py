"""Backend registry: discovery + selection.

Importing this module registers every shipped backend. Third-party backends can
register themselves by calling `register(MyBackend)` at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verify.backends.base import Backend, DetectionResult

_REGISTRY: dict[str, type[Backend]] = {}


def register(cls: type[Backend]) -> type[Backend]:
    """Decorator/function to register a Backend subclass under its `name`."""
    if not cls.name or cls.name == "base":
        raise ValueError(f"Backend {cls!r} must override .name")
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type[Backend]:
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown backend {name!r}. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def all_backends() -> list[type[Backend]]:
    return list(_REGISTRY.values())


def names() -> list[str]:
    return sorted(_REGISTRY)


@dataclass
class DetectionMatch:
    name: str
    cls: type[Backend]
    result: DetectionResult

    @property
    def available(self) -> bool:
        ok, _ = self.cls.is_available()
        return ok

    @property
    def availability_reason(self) -> str:
        _, reason = self.cls.is_available()
        return reason


def detect_all(project_dir: Path) -> list[DetectionMatch]:
    """Return every backend's detection result, sorted by confidence desc."""
    out: list[DetectionMatch] = []
    for name, cls in _REGISTRY.items():
        try:
            result = cls.detect(project_dir)
        except Exception as e:
            result = DetectionResult(confidence=0, reason=f"detect error: {e}")
        out.append(DetectionMatch(name=name, cls=cls, result=result))
    out.sort(key=lambda m: m.result.confidence, reverse=True)
    return out


def best_match(project_dir: Path, require_available: bool = True) -> DetectionMatch | None:
    """Pick the highest-confidence backend that is available (>0 confidence).

    If `require_available` is True, skips backends whose host tools are missing
    so we don't recommend a path the user can't actually run.
    """
    for m in detect_all(project_dir):
        if m.result.confidence <= 0:
            continue
        if require_available and not m.available:
            continue
        return m
    return None


def _register_shipped() -> None:
    """Import shipped backends so their @register decorators fire.

    Wrapped in try/except per-module so missing optional deps don't kill the
    registry.
    """
    for modname in (
        "verify.backends.web",
        "verify.backends.android",
        "verify.backends.linux_desktop",
        "verify.backends.renode",
        "verify.backends.generic",
    ):
        try:
            __import__(modname)
        except Exception:
            # Backend import failure is tolerated; is_available() reports it.
            pass


_register_shipped()
