"""verify command-line interface.

Subcommands:

  verify backends         List backends, detection score for the current dir,
                          and whether the host has the tools to run each.
  verify detect           Just the detection table for the current dir.
  verify init             Write a starter .verify.yaml shaped for the detected
                          backend.
  verify run [PATH]       Execute .verify.yaml (or PATH).
  verify mcp              Launch an MCP server exposing screenshot/click/etc.
                          to Claude Code or any MCP client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from verify import __version__
from verify.backends.registry import detect_all, get, names as backend_names
from verify.config import load


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="verify")
@click.pass_context
def main(ctx: click.Context) -> None:
    """verify — universal end-to-end verification with vision."""
    if ctx.invoked_subcommand is None:
        # Default action: `verify` with no args runs `.verify.yaml` in cwd.
        ctx.invoke(run)


@main.command("backends")
@click.option(
    "--cwd",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=Path.cwd,
    help="Project directory to detect against.",
)
def list_backends(cwd: Path) -> None:
    """List backends with detection score and availability."""
    rows = detect_all(cwd)
    click.echo(f"{'BACKEND':<16} {'SCORE':>5}  {'AVAILABLE':<10}  REASON")
    for m in rows:
        avail_ok, avail_reason = m.cls.is_available()
        avail = "yes" if avail_ok else "no"
        reason = m.result.reason or (avail_reason if not avail_ok else "")
        click.echo(f"{m.name:<16} {m.result.confidence:>5}  {avail:<10}  {reason}")


@main.command("detect")
@click.option(
    "--cwd",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=Path.cwd,
    help="Project directory to detect against.",
)
def detect_cmd(cwd: Path) -> None:
    """Print the auto-detected backend (or 'none')."""
    for m in detect_all(cwd):
        if m.result.confidence > 0 and m.available:
            click.echo(m.name)
            return
    click.echo("none")
    sys.exit(2)


@main.command("init")
@click.option(
    "--cwd",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=Path.cwd,
    help="Project directory to scaffold inside.",
)
@click.option(
    "--backend",
    "backend_override",
    type=click.Choice(backend_names()),
    default=None,
    help="Force a specific backend instead of auto-detection.",
)
@click.option("--force", is_flag=True, help="Overwrite existing .verify.yaml.")
def init_cmd(cwd: Path, backend_override: str | None, force: bool) -> None:
    """Create a starter .verify.yaml in the project directory."""
    dest = cwd / ".verify.yaml"
    if dest.exists() and not force:
        click.echo(f".verify.yaml already exists at {dest}. Use --force to overwrite.", err=True)
        sys.exit(2)

    if backend_override:
        backend = backend_override
    else:
        backend = None
        for m in detect_all(cwd):
            if m.result.confidence > 0 and m.available:
                backend = m.name
                break
        backend = backend or "generic"

    dest.write_text(_starter_template(backend))
    click.echo(f"wrote {dest} (backend={backend})")


@main.command("run")
@click.argument(
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".verify.yaml"),
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a JSON report instead of text."
)
def run(config_path: Path, as_json: bool) -> None:
    """Run a .verify.yaml against its target."""
    if not config_path.is_file():
        click.echo(f"no such file: {config_path}", err=True)
        sys.exit(2)
    from verify.runner import run as run_verify

    cfg = load(config_path)
    project_dir = config_path.resolve().parent
    report = run_verify(cfg, project_dir)

    if as_json:
        click.echo(_report_to_json(report))
    else:
        _print_report(report)
    sys.exit(0 if report.passed else 1)


@main.group("sandboxes")
def sandboxes_grp() -> None:
    """Manage labeled Docker sandboxes started by verify."""


@sandboxes_grp.command("list")
@click.option(
    "--older-than",
    type=int,
    default=0,
    help="Only show containers older than N seconds.",
)
def sandboxes_list(older_than: int) -> None:
    """List every verify.session=* container on this host."""
    from verify.sandbox import docker_available, list_orphans

    ok, reason = docker_available()
    if not ok:
        click.echo(f"docker unavailable: {reason}", err=True)
        sys.exit(2)
    rows = list_orphans(older_than_seconds=older_than)
    if not rows:
        click.echo("no verify sandboxes running")
        return
    click.echo(f"{'ID':<14} {'KIND':<20} {'AGE':>6}  IMAGE")
    for r in rows:
        click.echo(
            f"{r.container_id[:12]:<14} {r.kind:<20} {r.age_seconds:>5}s  {r.image}"
        )


@sandboxes_grp.command("prune")
@click.option(
    "--older-than",
    type=int,
    default=1800,
    help="Only prune containers older than N seconds (default 1800 = 30min).",
)
@click.option(
    "--all", "all_", is_flag=True, help="Prune every verify sandbox regardless of age."
)
def sandboxes_prune(older_than: int, all_: bool) -> None:
    """Remove orphaned verify sandboxes left behind by crashed runs."""
    from verify.sandbox import docker_available, prune_orphans

    ok, reason = docker_available()
    if not ok:
        click.echo(f"docker unavailable: {reason}", err=True)
        sys.exit(2)
    killed = prune_orphans(older_than_seconds=0 if all_ else older_than)
    if not killed:
        click.echo("no orphans to prune")
        return
    for cid in killed:
        click.echo(f"removed {cid[:12]}")


@main.command("mcp")
@click.option(
    "--config",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=Path(".verify.yaml"),
    help="Config that defines the target environment to attach to.",
)
def mcp_cmd(config: Path) -> None:
    """Run an MCP server exposing the backend's primitives to AI agents."""
    from verify.mcp_server import serve

    serve(config_path=config)


# ---- helpers --------------------------------------------------------------


def _starter_template(backend: str) -> str:
    templates = {
        "web": """\
# .verify.yaml — universal end-to-end verification with vision.
backend: web

launch:
  # Either point at an existing URL or start a dev server.
  url: http://localhost:3000
  # command: npm run dev
  wait_after: 2

steps:
  - name: home page loads
    actions:
      - navigate: "http://localhost:3000"
    expect:
      vision: "the home page loaded with no error banner or stack trace visible"

  - name: login button visible
    expect:
      vision: "there is a visible Log in / Sign in button or link"
""",
        "android": """\
backend: android

launch:
  package: com.example.app
  wait_after: 2

steps:
  - name: app opens to main screen
    expect:
      vision: "the app's main screen is visible — no crash, no ANR dialog"

  - name: typing into search field works
    actions:
      - click: { locate: { vision: "the search input field at the top" } }
      - type: "hello"
      - key: enter
      - wait: 1
    expect:
      vision: "the search query 'hello' has been entered and a result is shown"
""",
        "renode": """\
backend: renode

launch:
  command: ./demo.resc
  wait_after: 2

steps:
  - name: boot prints banner
    expect:
      log_contains: "Booting"
      no_log_contains: "HardFault"

  - name: led toggles
    actions:
      - wait: 2
    expect:
      log_contains: "LED on"
""",
        "linux_desktop": """\
backend: linux_desktop

launch:
  command: ./build/myapp
  wait_after: 2

steps:
  - name: window opens
    expect:
      vision: "the application's main window is visible with menu bar and contents drawn"
""",
        "generic": """\
backend: generic

launch:
  command: ./run.sh
  wait_after: 2

steps:
  - name: process starts and shows expected output
    expect:
      log_contains: "Ready"
""",
    }
    return templates.get(backend, templates["generic"])


def _print_report(report) -> None:
    click.echo(report.summary())
    for s in report.steps:
        marker = "PASS" if s.passed else "FAIL"
        click.echo(f"  [{marker}] {s.step.name}")
        for ar in s.actions:
            if not ar.ok:
                click.echo(f"      action {ar.action.type}: {ar.error}")
        if s.expect:
            if s.expect.vision is not None:
                v = s.expect.vision
                click.echo(
                    f"      vision: {'PASS' if v.passed else 'FAIL'} — {v.reason}"
                )
            if s.expect.url_ok is False:
                click.echo(f"      url: {s.expect.url_actual}")
            if s.expect.log_ok is False:
                click.echo("      log mismatch")
        if s.error:
            click.echo(f"      error: {s.error}")
    if report.setup_error:
        click.echo(f"setup error: {report.setup_error}")


def _report_to_json(report) -> str:
    return json.dumps(
        {
            "passed": report.passed,
            "backend": report.backend,
            "setup_error": report.setup_error,
            "steps": [
                {
                    "name": s.step.name,
                    "passed": s.passed,
                    "error": s.error,
                    "actions": [
                        {"type": ar.action.type, "ok": ar.ok, "error": ar.error}
                        for ar in s.actions
                    ],
                    "expect": (
                        {
                            "vision": (
                                {
                                    "passed": s.expect.vision.passed,
                                    "reason": s.expect.vision.reason,
                                }
                                if s.expect.vision
                                else None
                            ),
                            "url_ok": s.expect.url_ok,
                            "url_actual": s.expect.url_actual,
                            "log_ok": s.expect.log_ok,
                        }
                        if s.expect
                        else None
                    ),
                }
                for s in report.steps
            ],
        },
        indent=2,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
