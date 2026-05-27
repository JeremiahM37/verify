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
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        sys.stderr.write(f"verify: invalid YAML in {path}:\n  {e}\n")
        sys.exit(2)
    if data is None:
        return {}
    if not isinstance(data, dict):
        sys.stderr.write(
            f"verify: {path}: top level must be a mapping (got {type(data).__name__})\n"
        )
        sys.exit(2)
    checks = data.get("checks")
    if checks is not None and not isinstance(checks, list):
        sys.stderr.write(
            f"verify: {path}: 'checks' must be a list (got {type(checks).__name__})\n"
        )
        sys.exit(2)
    return data


def _run_check(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return {"name": "?", "ok": False,
                "detail": f"check entry must be a mapping, got {type(cfg).__name__}: {cfg!r}"}
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


STARTER_CONFIG = """\
# `.verify.yaml` — what to run when someone (or some AI) is about to claim
# this project is "done". `verify` reads this file and exits 0 only if every
# check passes. See https://github.com/JeremiahM37/verify for the full schema.

checks:
  # Unit tests
  - name: tests
    type: pytest
    # run: "pytest -q"       # uncomment to override default

  # Your service is running
  # - name: service-active
  #   type: systemd
  #   units: [my-service]

  # Recent logs are clean
  # - name: no-recent-errors
  #   type: journalctl
  #   units: [my-service]
  #   since: "1 min ago"
  #   forbid: [ERROR, Traceback, CRITICAL]

  # API endpoints respond correctly
  # - name: endpoints
  #   type: http
  #   targets:
  #     - { url: "http://127.0.0.1:8000/healthz",
  #         status: 200, contains: '"ok":true' }

  # UI actually works (drives a real headless browser through your flow)
  # - name: critical-flow
  #   type: ui
  #   url: "http://127.0.0.1:8000/"
  #   steps:
  #     - wait: 'button[type="submit"]'
  #     - click: 'button[type="submit"]'
  #     - wait: ".success"
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Explicit subcommand dispatch — keeps the default `verify` invocation
    # (no subcommand) cleanly accepting a positional config arg without
    # argparse's awkward subparser+positional collision.
    if argv and argv[0] == "init":
        return _cmd_init_main(argv[1:])
    if argv and argv[0] == "list-checks":
        return _cmd_list_checks_main(argv[1:])
    if argv and argv[0] == "list-devices":
        return _cmd_list_devices_main(argv[1:])
    return _cmd_run_main(argv)


def _cmd_list_devices_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="verify list-devices",
        description="List Playwright device presets usable in `ui` checks "
                    "as `device: <name>`.")
    p.add_argument("filter", nargs="?", default=None,
                   help="Substring to filter by (case-insensitive)")
    args = p.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.stderr.write("verify: playwright not installed. "
                         "`pip install verify-cli[ui]` first.\n")
        return 2
    with sync_playwright() as pw:
        names = sorted(pw.devices)
    if args.filter:
        f = args.filter.lower()
        names = [n for n in names if f in n.lower()]
    if not names:
        sys.stderr.write(f"no devices matched {args.filter!r}\n")
        return 1
    for n in names:
        print(n)
    return 0


def _cmd_init_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="verify init",
        description="Scaffold a starter .verify.yaml in the current project.")
    p.add_argument("path", nargs="?", default=".verify.yaml",
                   help="Where to write (default: ./.verify.yaml)")
    p.add_argument("--force", "-f", action="store_true",
                   help="Overwrite if the file already exists")
    args = p.parse_args(argv)
    target = Path(args.path)
    if target.exists() and not args.force:
        sys.stderr.write(f"verify: {target} already exists (use --force to overwrite)\n")
        return 2
    target.write_text(STARTER_CONFIG)
    print(f"wrote {target}")
    print("edit it to match your project, then run `verify`")
    return 0


def _cmd_list_checks_main(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="verify list-checks").parse_args(argv)
    print("verify check types:\n")
    for name in sorted(REGISTRY):
        mod = REGISTRY[name].__module__
        doc = sys.modules[mod].__doc__ or ""
        summary = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
        print(f"  {name:12s}  {summary}")
    print("\nFull schema for each type:")
    for name in sorted(REGISTRY):
        mod = REGISTRY[name].__module__
        doc = (sys.modules[mod].__doc__ or "").strip()
        if doc:
            print(f"\n── {name} ──")
            print(doc)
    return 0


def _cmd_run_main(argv: list[str]) -> int:
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
