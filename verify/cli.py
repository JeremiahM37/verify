"""`verify` — runs all checks from a YAML config and prints a structured report.

Usage:
    verify                          # reads .verify.yaml from CWD
    verify path/to/checks.yaml      # custom config path
    verify --only systemd,http      # run only checks of these types
    verify --json                   # machine-readable output

Exit code: 0 if all checks pass, 1 otherwise. So you can wire it into hooks,
CI, pre-commit, or wrap it in another tool.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import __version__
from .checks import REGISTRY


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.stderr.write(f"verify: config not found: {path}\n")
        sys.exit(2)
    return yaml.safe_load(p.read_text()) or {}


def _run_check(cfg: dict) -> dict:
    ctype = cfg.get("type", "shell")
    if ctype not in REGISTRY:
        return {"name": cfg.get("name", ctype), "ok": False,
                "detail": f"unknown check type {ctype!r}; "
                          f"known: {sorted(REGISTRY)}"}
    return REGISTRY[ctype](cfg)


def _format_report(results: list[dict]) -> str:
    out = []
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    bar = "─" * 60
    out.append(bar)
    out.append(f"verify: {passed}/{total} passed")
    out.append(bar)
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        out.append(f"  [{mark}] {r['name']}")
        if not r["ok"]:
            if r.get("detail"):
                for line in r["detail"].splitlines()[-12:]:
                    out.append(f"      {line}")
            if r.get("items"):
                for it in r["items"]:
                    if not it.get("ok", True):
                        out.append(f"      - {it}")
    out.append(bar)
    out.append("PASS" if passed == total else "FAIL")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="verify",
        description="End-to-end verify your project before claiming done.")
    p.add_argument("config", nargs="?", default=".verify.yaml",
                   help="YAML config path (default: ./.verify.yaml)")
    p.add_argument("--only", default=None,
                   help="Comma-separated list of check types to run, e.g. 'pytest,http'")
    p.add_argument("--skip", default=None,
                   help="Comma-separated list of check types to skip")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of human-readable report")
    p.add_argument("--version", action="version", version=f"verify {__version__}")
    args = p.parse_args(argv)

    # cd to the config's directory so relative paths in checks resolve
    cfg_path = Path(args.config).resolve()
    if cfg_path.parent.exists():
        os.chdir(cfg_path.parent)

    cfg = _load(cfg_path.name if cfg_path.parent == Path.cwd() else str(cfg_path))
    checks = cfg.get("checks") or []
    only = set(filter(None, (args.only or "").split(",")))
    skip = set(filter(None, (args.skip or "").split(",")))
    selected = [
        c for c in checks
        if (not only or c.get("type", "shell") in only)
        and (c.get("type", "shell") not in skip)
    ]

    results = [_run_check(c) for c in selected]
    if args.json:
        print(json.dumps({"ok": all(r["ok"] for r in results),
                          "checks": results}, indent=2, default=str))
    else:
        print(_format_report(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
