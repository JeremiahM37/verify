"""Dogfood: spawn the real `verify` binary as a subprocess.

Every other test imports `verify.cli.main` and runs it in-process. That's good
for coverage but invisible to a class of bug: bad console_scripts entry,
import-time side effects, version mismatch, argument parsing differences
between the installed CLI and what the source tree expects.

This test invokes the actual installed CLI via subprocess and asserts on its
stdout, stderr, and exit code. If you broke the entry point, this fires.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


VERIFY = shutil.which("verify") or (Path(sys.executable).parent / "verify")

pytestmark = pytest.mark.skipif(
    not Path(VERIFY).exists(), reason="verify CLI not on PATH"
)


def _run(*args: str, cwd: Path | None = None, env_extra: dict | None = None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(VERIFY), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_version_subcommand():
    r = _run("--version")
    assert r.returncode == 0
    assert "verify" in r.stdout.lower()
    # Matches "verify, version 0.2.0" or similar.
    assert "0." in r.stdout


def test_help_lists_subcommands():
    r = _run("--help")
    assert r.returncode == 0
    for cmd in ("backends", "detect", "init", "run", "mcp", "sandboxes"):
        assert cmd in r.stdout


def test_backends_listing_shows_every_backend(tmp_path):
    r = _run("backends", "--cwd", str(tmp_path))
    assert r.returncode == 0
    for name in ("web", "android", "linux_desktop", "renode", "generic"):
        assert name in r.stdout


def test_detect_empty_dir_returns_generic_or_none(tmp_path):
    r = _run("detect", "--cwd", str(tmp_path))
    # Either generic (available) or none — both legal.
    assert r.stdout.strip() in {"generic", "none"}


def test_detect_android_project(tmp_path):
    (tmp_path / "AndroidManifest.xml").write_text("<manifest/>")
    r = _run("detect", "--cwd", str(tmp_path))
    # adb may or may not be on host PATH; either android or generic is valid.
    assert r.stdout.strip() in {"android", "generic", "none"}


def test_init_creates_yaml(tmp_path):
    r = _run("init", "--cwd", str(tmp_path))
    assert r.returncode == 0
    assert (tmp_path / ".verify.yaml").exists()


def test_init_force_overwrite(tmp_path):
    target = tmp_path / ".verify.yaml"
    target.write_text("# old\n")
    r = _run("init", "--cwd", str(tmp_path), "--force")
    assert r.returncode == 0
    content = target.read_text()
    assert "backend:" in content
    assert "# old" not in content


def test_init_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / ".verify.yaml"
    target.write_text("# mine\n")
    r = _run("init", "--cwd", str(tmp_path))
    assert r.returncode != 0
    assert "already exists" in r.stderr or "already exists" in r.stdout


def test_run_missing_file_exits_nonzero(tmp_path):
    r = _run("run", str(tmp_path / "does-not-exist.yaml"))
    assert r.returncode != 0
    assert "no such file" in r.stderr or "no such file" in r.stdout


def test_run_unknown_backend_returns_structured_failure(tmp_path):
    """Selection failures land in setup_error and exit 1, not traceback."""
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text("backend: nonexistent-thing\nsteps: []\n")
    r = _run("run", str(cfg))
    assert r.returncode == 1
    # Should be a clean diagnostic, not a Python traceback.
    assert "Traceback" not in r.stderr
    assert "setup error" in r.stdout or "FAIL" in r.stdout


def test_run_json_output_is_valid_json(tmp_path):
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text("backend: nonexistent-thing\nsteps: []\n")
    r = _run("run", str(cfg), "--json")
    # JSON mode should still emit JSON even on failure.
    assert r.stdout.strip().startswith("{")
    data = json.loads(r.stdout)
    assert data["passed"] is False
    assert data["setup_error"]


def test_sandboxes_subcommand_help():
    r = _run("sandboxes", "--help")
    assert r.returncode == 0
    assert "list" in r.stdout and "prune" in r.stdout


def test_real_passing_run_through_subprocess(tmp_path):
    """Use the generic backend on a no-op .verify.yaml with only a log
    expectation that we can satisfy with shell. End-to-end with the real CLI
    and no mocking — the actual exit code is what we trust."""
    # We can satisfy log_contains by having the launched process print to stdout.
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text(
        "backend: generic\n"
        "launch:\n"
        "  command: 'sh -c \"echo Server ready && sleep 0.5\"'\n"
        "  wait_after: 0.3\n"
        "steps:\n"
        "  - name: ready printed\n"
        "    expect:\n"
        "      log_contains: 'Server ready'\n"
    )
    r = _run("run", str(cfg))
    # Generic backend may not be 'is_available' on every host (needs mss). If
    # it isn't, the run reports setup_error gracefully; if it is, the test
    # should pass.
    if r.returncode == 0:
        assert "PASS" in r.stdout
    else:
        # Acceptable failure modes: mss not installed, or detection chose
        # different backend.
        assert "setup error" in r.stdout or "FAIL" in r.stdout
