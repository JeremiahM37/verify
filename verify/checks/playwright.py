"""Playwright check — runs a Python script you've written. Pass if exit 0.

Used for full end-to-end browser-driven flows that are too involved for the
inline `ui` check. The script should print its own pass/fail summary and
exit non-zero on failure.

Config:
    - name: e2e
      type: playwright
      script: "tests/e2e_smoke.py"
      python: ".venv/bin/python"      # optional, default sys.executable
      cwd: "."
      timeout: 300

This check does not install playwright for you; either install it in your
project's venv (`pip install playwright && playwright install chromium`)
or use the `[ui]` extra on verify-cli.
"""
from __future__ import annotations

import sys

from . import shell


def run(cfg):
    script = cfg.get("script")
    if not script:
        return {"name": cfg.get("name", "playwright"), "ok": False,
                "detail": "missing 'script' path"}
    py = cfg.get("python", sys.executable)
    cmd = f"{py} {script}"
    new_cfg = dict(cfg)
    new_cfg["run"] = cmd
    new_cfg.setdefault("name", "playwright")
    new_cfg.setdefault("tail", 50)
    new_cfg.setdefault("timeout", 300)
    return shell.run(new_cfg)
