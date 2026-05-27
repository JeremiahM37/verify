"""Pytest check — wraps shell, but with sensible defaults.

Config:
    - name: tests
      type: pytest
      cwd: "."
      run: "pytest -q"            # override the default command
"""
from __future__ import annotations

from . import shell


def run(cfg):
    cfg = dict(cfg)
    cfg.setdefault("run", "pytest -q")
    cfg.setdefault("tail", 40)
    cfg.setdefault("name", "pytest")
    return shell.run(cfg)
