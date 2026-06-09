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
