"""Systemd check — fail if any listed unit is not 'active'.

Config:
    - name: services
      type: systemd
      units: [homelab-api, web-terminals]
"""
from __future__ import annotations

import subprocess


def run(cfg):
    units = cfg.get("units") or []
    if not units:
        return {"name": cfg.get("name", "systemd"), "ok": False,
                "detail": "no units listed"}
    items = []
    for unit in units:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=10,
            )
            state = r.stdout.strip() or "(unknown)"
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            state = f"error: {e}"
        items.append({"unit": unit, "state": state, "ok": state == "active"})
    return {
        "name": cfg.get("name", "systemd"),
        "ok": all(i["ok"] for i in items),
        "items": items,
    }
