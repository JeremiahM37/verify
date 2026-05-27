"""Journalctl check — fail if any forbidden string appears in recent unit logs.

Config:
    - name: logs-clean
      type: journalctl
      units: [homelab-api]
      since: "5 min ago"
      forbid: ["ERROR", "Traceback", "Exception"]
      ignore: ["something benign"]   # optional substrings to exclude

Reports the first few offending lines so you can see what tripped it.
"""
from __future__ import annotations

import subprocess


DEFAULT_FORBID = ["ERROR", "Traceback", "CRITICAL"]


def run(cfg):
    units  = cfg.get("units") or []
    since  = cfg.get("since", "5 min ago")
    forbid = cfg.get("forbid", DEFAULT_FORBID)
    ignore = cfg.get("ignore", [])
    max_hits = int(cfg.get("max_hits", 5))
    if not units:
        return {"name": cfg.get("name", "journalctl"), "ok": False,
                "detail": "no units listed"}
    items = []
    for unit in units:
        try:
            r = subprocess.run(
                ["journalctl", "-u", unit, "--since", since,
                 "--no-pager", "--output", "short-iso"],
                capture_output=True, text=True, timeout=30,
            )
            lines = r.stdout.splitlines()
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            items.append({"unit": unit, "ok": False, "error": str(e)})
            continue
        hits = [ln for ln in lines
                if any(f in ln for f in forbid)
                and not any(i in ln for i in ignore)]
        items.append({
            "unit": unit, "ok": not hits,
            "hits": hits[:max_hits],
            "n_total_lines": len(lines),
        })
    return {
        "name": cfg.get("name", "journalctl"),
        "ok": all(i.get("ok") for i in items),
        "items": items,
    }
