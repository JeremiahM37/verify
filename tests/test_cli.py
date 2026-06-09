"""Tests for the `verify` CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from verify import __version__
from verify.cli import main


def test_version():
    r = CliRunner().invoke(main, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.output


def test_backends_lists_all_registered():
    r = CliRunner().invoke(main, ["backends"])
    assert r.exit_code == 0
    for name in ("web", "android", "linux_desktop", "renode", "generic"):
        assert name in r.output


def test_detect_empty_dir_returns_generic(tmp_path):
    r = CliRunner().invoke(main, ["detect", "--cwd", str(tmp_path)])
    # Generic always wins on an empty dir.
    assert r.exit_code == 0
    assert r.output.strip() == "generic"


def test_detect_android_project(tmp_path):
    (tmp_path / "AndroidManifest.xml").write_text("<manifest/>")
    r = CliRunner().invoke(main, ["detect", "--cwd", str(tmp_path)])
    # adb missing on host -> falls back to generic.
    assert r.output.strip() in {"android", "generic"}


def test_init_creates_starter_yaml(tmp_path):
    r = CliRunner().invoke(main, ["init", "--cwd", str(tmp_path)])
    assert r.exit_code == 0
    p = tmp_path / ".verify.yaml"
    assert p.exists()
    text = p.read_text()
    assert "backend:" in text
    assert "steps:" in text


def test_init_refuses_to_overwrite(tmp_path):
    p = tmp_path / ".verify.yaml"
    p.write_text("# mine")
    r = CliRunner().invoke(main, ["init", "--cwd", str(tmp_path)])
    assert r.exit_code != 0
    assert "already exists" in r.output


def test_init_force_overwrites(tmp_path):
    p = tmp_path / ".verify.yaml"
    p.write_text("# mine")
    r = CliRunner().invoke(main, ["init", "--cwd", str(tmp_path), "--force"])
    assert r.exit_code == 0
    assert "backend:" in p.read_text()


def test_init_with_backend_override(tmp_path):
    r = CliRunner().invoke(
        main, ["init", "--cwd", str(tmp_path), "--backend", "android"]
    )
    assert r.exit_code == 0
    assert "backend: android" in (tmp_path / ".verify.yaml").read_text()


def test_run_missing_file_errors(tmp_path):
    r = CliRunner().invoke(main, ["run", str(tmp_path / "missing.yaml")])
    assert r.exit_code != 0


def test_run_with_fake_backend_through_runner(tmp_path, monkeypatch):
    """Patch run_verify so we exercise the CLI plumbing, not the real runner."""
    from verify import cli as cli_mod

    p = tmp_path / ".verify.yaml"
    p.write_text("backend: web\nsteps: []\n")

    class FakeReport:
        passed = True
        backend = "web"
        setup_error = ""
        steps: list = []

        def summary(self):
            return "PASS: 0/0 steps passed (backend=web)"

    monkeypatch.setattr(
        "verify.runner.run", lambda *a, **kw: FakeReport()
    )
    r = CliRunner().invoke(main, ["run", str(p)])
    assert r.exit_code == 0
    assert "PASS" in r.output


def test_detect_no_match_exits_2(tmp_path, monkeypatch):
    """When no backend has confidence > 0 AND is available, print 'none' + exit 2."""
    # Patch the registry so even generic isn't returned.
    from verify.backends.registry import DetectionMatch
    from verify.backends.base import DetectionResult

    class _Unavail:
        name = "phantom"

        @classmethod
        def is_available(cls):
            return False, "missing"

    monkeypatch.setattr(
        "verify.cli.detect_all",
        lambda d: [DetectionMatch("phantom", _Unavail, DetectionResult(50, "matched"))],
    )
    r = CliRunner().invoke(main, ["detect", "--cwd", str(tmp_path)])
    assert r.exit_code == 2
    assert r.output.strip() == "none"


def test_run_prints_setup_error(tmp_path, monkeypatch):
    """Runner failures should be printed to stdout and exit non-zero."""
    p = tmp_path / ".verify.yaml"
    p.write_text("backend: nonexistent\nsteps: []\n")
    # Don't monkeypatch — let the real runner produce setup_error via the
    # backend-selection failure path we fixed earlier.
    r = CliRunner().invoke(main, ["run", str(p)])
    assert r.exit_code != 0
    assert "setup error" in r.output or "FAIL" in r.output


def test_init_prints_what_was_written(tmp_path):
    r = CliRunner().invoke(main, ["init", "--cwd", str(tmp_path)])
    assert r.exit_code == 0
    assert "wrote" in r.output and ".verify.yaml" in r.output


def test_mcp_subcommand_invokes_serve(tmp_path, monkeypatch):
    p = tmp_path / ".verify.yaml"
    p.write_text("backend: web\nsteps: []\n")
    captured: dict = {}

    def fake_serve(*, config_path):
        captured["config_path"] = config_path

    monkeypatch.setattr("verify.mcp_server.serve", fake_serve)
    r = CliRunner().invoke(main, ["mcp", "--config", str(p)])
    assert r.exit_code == 0
    assert captured["config_path"] == p


def test_no_args_runs_default_yaml(tmp_path, monkeypatch):
    """`verify` with no subcommand should run .verify.yaml in cwd."""
    # Click's CliRunner doesn't easily switch cwd. Verify via the error case
    # (no .verify.yaml in working dir).
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, [])
    assert r.exit_code != 0
    assert "no such file" in r.output


def test_full_passing_run_through_cli_with_yaml_and_fake_backend(tmp_path, monkeypatch):
    """End-to-end CLI invocation with a tiny YAML and a stub backend in the
    registry. Proves the whole CLI -> config -> runner -> exit path."""
    from verify.backends.base import Backend, DetectionResult, LaunchSpec
    from verify.backends.registry import _REGISTRY

    class _NoopBackend(Backend):
        name = "noop-cli-test"

        @classmethod
        def detect(cls, project_dir):
            return DetectionResult(0, "")

        def start(self, spec):
            pass

        def stop(self):
            pass

        def screen_size(self):
            return (10, 10)

        def screenshot(self):
            return b"\x89PNG\r\n\x1a\nDATA"

        def click(self, x, y, button="left"):
            pass

        def type_text(self, text):
            pass

        def key(self, name):
            pass

        def read_logs(self, lines=100):
            return "Server ready\n"

    _REGISTRY["noop-cli-test"] = _NoopBackend
    try:
        p = tmp_path / ".verify.yaml"
        p.write_text(
            "backend: noop-cli-test\n"
            "steps:\n"
            "  - name: smoke\n"
            "    expect:\n"
            "      log_contains: 'Server ready'\n"
        )
        r = CliRunner().invoke(main, ["run", str(p)])
        assert r.exit_code == 0
        assert "PASS" in r.output
        assert "smoke" in r.output
    finally:
        _REGISTRY.pop("noop-cli-test", None)


def test_sandboxes_list_no_docker(monkeypatch):
    monkeypatch.setattr("verify.sandbox.docker_available", lambda: (False, "not on PATH"))
    r = CliRunner().invoke(main, ["sandboxes", "list"])
    assert r.exit_code != 0
    assert "docker unavailable" in r.output


def test_sandboxes_list_empty(monkeypatch):
    monkeypatch.setattr("verify.sandbox.docker_available", lambda: (True, ""))
    monkeypatch.setattr("verify.sandbox.list_orphans", lambda **kw: [])
    r = CliRunner().invoke(main, ["sandboxes", "list"])
    assert r.exit_code == 0
    assert "no verify sandboxes" in r.output


def test_sandboxes_list_shows_rows(monkeypatch):
    from verify.sandbox import OrphanInfo

    monkeypatch.setattr("verify.sandbox.docker_available", lambda: (True, ""))
    monkeypatch.setattr(
        "verify.sandbox.list_orphans",
        lambda **kw: [
            OrphanInfo(
                container_id="abcdef1234567890",
                image="budtmo/docker-android:emulator_14.0",
                session="sess1",
                kind="android-emulator",
                age_seconds=42,
            )
        ],
    )
    r = CliRunner().invoke(main, ["sandboxes", "list"])
    assert r.exit_code == 0
    assert "abcdef123456" in r.output
    assert "android-emulator" in r.output


def test_sandboxes_prune_calls_prune_orphans(monkeypatch):
    captured = {}
    monkeypatch.setattr("verify.sandbox.docker_available", lambda: (True, ""))

    def fake_prune(*, older_than_seconds):
        captured["older_than"] = older_than_seconds
        return ["abc12345", "def67890"]

    monkeypatch.setattr("verify.sandbox.prune_orphans", fake_prune)
    r = CliRunner().invoke(main, ["sandboxes", "prune", "--older-than", "60"])
    assert r.exit_code == 0
    assert captured["older_than"] == 60
    assert "removed abc12345" in r.output
    assert "removed def67890" in r.output


def test_sandboxes_prune_all_flag_sets_zero(monkeypatch):
    captured = {}
    monkeypatch.setattr("verify.sandbox.docker_available", lambda: (True, ""))

    def fake_prune(*, older_than_seconds):
        captured["older_than"] = older_than_seconds
        return []

    monkeypatch.setattr("verify.sandbox.prune_orphans", fake_prune)
    r = CliRunner().invoke(main, ["sandboxes", "prune", "--all"])
    assert r.exit_code == 0
    assert captured["older_than"] == 0
    assert "no orphans" in r.output


def test_run_json_output(tmp_path, monkeypatch):
    p = tmp_path / ".verify.yaml"
    p.write_text("backend: web\nsteps: []\n")

    class FakeReport:
        passed = True
        backend = "web"
        setup_error = ""
        steps: list = []

        def summary(self):
            return "PASS"

    monkeypatch.setattr("verify.runner.run", lambda *a, **kw: FakeReport())
    r = CliRunner().invoke(main, ["run", str(p), "--json"])
    assert r.exit_code == 0
    import json as J

    data = J.loads(r.output)
    assert data["passed"] is True
    assert data["backend"] == "web"
