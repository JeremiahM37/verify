"""HTTP check — hit one or more endpoints, assert status and body content.

Config:
    - name: api-healthy
      type: http
      targets:
        - url: "http://127.0.0.1:9105/healthz"
          status: 200
          contains: '"ok":true'
        - url: "http://127.0.0.1:9105/term/7691"
          status: 200
          matches: "@xterm/xterm"        # substring or regex
          timeout: 5
          method: GET                    # default
          headers: { Authorization: "..." }
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request


def _probe(target: dict) -> dict:
    url     = target["url"]
    method  = target.get("method", "GET").upper()
    timeout = float(target.get("timeout", 8))
    headers = target.get("headers") or {}
    body    = target.get("body")
    expected_status = target.get("status", 200)
    status, text = 0, ""
    try:
        req = urllib.request.Request(
            url, method=method,
            data=body.encode() if isinstance(body, str) else body,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            text = resp.read(50_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            text = e.read(10_000).decode("utf-8", errors="replace")
        except Exception:
            text = ""
    except Exception as e:
        return {"url": url, "ok": False, "status": 0, "error": str(e)[:200]}

    ok = status == expected_status
    if ok and "contains" in target:
        ok = target["contains"] in text
    if ok and "matches" in target:
        ok = re.search(target["matches"], text) is not None
    if ok and "not_contains" in target:
        ok = target["not_contains"] not in text
    return {
        "url": url, "ok": ok, "status": status,
        "expected_status": expected_status,
        "preview": text[:200] if not ok else None,
    }


def run(cfg):
    targets = cfg.get("targets") or []
    if not targets:
        return {"name": cfg.get("name", "http"), "ok": False,
                "detail": "no targets listed"}
    items = [_probe(t) for t in targets]
    return {
        "name": cfg.get("name", "http"),
        "ok": all(i["ok"] for i in items),
        "items": items,
    }
