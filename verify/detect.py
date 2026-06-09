"""Project type detection.

Every backend owns its own `.detect()`. This module just orchestrates them and
exposes shared file-spotting helpers so backends don't reimplement
"is there a package.json with React in it".
"""

from __future__ import annotations

from pathlib import Path

from verify.backends.registry import DetectionMatch, best_match, detect_all


def detect(project_dir: Path) -> DetectionMatch | None:
    """Top-level: pick the best available backend for this project."""
    return best_match(project_dir, require_available=True)


def detect_report(project_dir: Path) -> list[DetectionMatch]:
    """All backends ranked, including ones whose host tools aren't installed."""
    return detect_all(project_dir)


# ---- shared file/glob helpers used by backend .detect() methods --------------


def has_file(project_dir: Path, *names: str) -> bool:
    return any((project_dir / n).is_file() for n in names)


def glob_any(project_dir: Path, *patterns: str) -> bool:
    for pat in patterns:
        try:
            if next(project_dir.glob(pat), None) is not None:
                return True
        except OSError:
            continue
    return False


def rglob_any(project_dir: Path, *patterns: str, limit: int = 1) -> int:
    """Count rglob matches up to `limit`. Returns 0..limit."""
    found = 0
    for pat in patterns:
        try:
            for _ in project_dir.rglob(pat):
                found += 1
                if found >= limit:
                    return found
        except OSError:
            continue
    return found


def file_contains(path: Path, *needles: str) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return any(n in text for n in needles)
