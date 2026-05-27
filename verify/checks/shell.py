"""Shell check — run any command, pass if exit 0.

Config:
    - name: lint
      type: shell
      run: "ruff check ."
      cwd: "."                    # optional
      env: { FOO: bar }           # optional
      timeout: 120                # seconds, default 300
      tail: 30                    # how many output lines to include on fail
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def run(cfg: dict[str, Any]) -> dict:
    cmd = cfg.get("run") or cfg.get("cmd")
    if not cmd:
        return {"name": cfg.get("name", "shell"), "ok": False,
                "detail": "missing 'run' command"}
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (cfg.get("env") or {}).items()})
    timeout = float(cfg.get("timeout", 300))
    try:
        proc = subprocess.run(
            cmd, shell=isinstance(cmd, str),
            capture_output=True, text=True,
            cwd=cfg.get("cwd"), env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"name": cfg.get("name", "shell"), "ok": False,
                "detail": f"timed out after {timeout:.0f}s"}
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = int(cfg.get("tail", 30))
    return {
        "name": cfg.get("name", "shell"),
        "ok": proc.returncode == 0,
        "detail": "\n".join(out.splitlines()[-tail:]),
        "rc": proc.returncode,
    }
