"""End-to-end CLI tests: write a config, run `verify`, check exit + output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from verify.cli import main


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".verify.yaml"
    p.write_text(body)
    return p


def test_all_passing_exits_zero(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - name: t1
            type: shell
            run: "true"
          - name: t2
            type: shell
            run: "echo hello"
    """.replace("        ", ""))
    rc = main([str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "2/2 passed" in out


def test_any_failing_exits_one(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - name: good
            type: shell
            run: "true"
          - name: bad
            type: shell
            run: "false"
    """.replace("        ", ""))
    rc = main([str(cfg)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "1/2 passed" in out
    assert "[✓] good" in out
    assert "[✗] bad" in out


def test_only_filter(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - { name: a, type: shell, run: "true" }
          - { name: b, type: pytest, run: "true" }
    """.replace("        ", ""))
    rc = main([str(cfg), "--only", "pytest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1/1" in out
    assert "[✓] b" in out
    assert "[✓] a" not in out


def test_skip_filter(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - { name: a, type: shell, run: "true" }
          - { name: b, type: pytest, run: "false" }
    """.replace("        ", ""))
    rc = main([str(cfg), "--skip", "pytest"])
    assert rc == 0   # the failing one is skipped


def test_json_output(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - { name: t, type: shell, run: "true" }
    """.replace("        ", ""))
    rc = main([str(cfg), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["checks"][0]["name"] == "t"


def test_unknown_check_type_reported(tmp_path, capsys):
    cfg = _write(tmp_path, """
        checks:
          - { name: weird, type: doesnotexist }
    """.replace("        ", ""))
    rc = main([str(cfg)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "unknown check type" in out


def test_missing_config_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(tmp_path / "no-such-file.yaml")])
    assert e.value.code == 2


def test_invalid_yaml_exits_two_with_clean_error(tmp_path, capsys):
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text("this: is: not: valid: yaml: at: all\n")
    with pytest.raises(SystemExit) as e:
        main([str(cfg)])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "invalid YAML" in err


def test_top_level_must_be_mapping(tmp_path, capsys):
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text("- just a list at top level\n")
    with pytest.raises(SystemExit) as e:
        main([str(cfg)])
    assert e.value.code == 2
    assert "must be a mapping" in capsys.readouterr().err


def test_checks_must_be_a_list(tmp_path, capsys):
    cfg = tmp_path / ".verify.yaml"
    cfg.write_text("checks: not a list\n")
    with pytest.raises(SystemExit) as e:
        main([str(cfg)])
    assert e.value.code == 2
    assert "'checks' must be a list" in capsys.readouterr().err


def test_init_scaffolds_a_starter_config(tmp_path, capsys):
    target = tmp_path / ".verify.yaml"
    rc = main(["init", str(target)])
    assert rc == 0
    text = target.read_text()
    assert "checks:" in text
    assert "pytest" in text


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    target = tmp_path / ".verify.yaml"
    target.write_text("existing content\n")
    rc = main(["init", str(target)])
    assert rc == 2
    assert "already exists" in capsys.readouterr().err
    # --force overwrites
    rc = main(["init", str(target), "--force"])
    assert rc == 0
    assert "existing content" not in target.read_text()


def test_list_checks_shows_all_types(capsys):
    rc = main(["list-checks"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("pytest", "shell", "systemd", "journalctl", "http", "ui", "playwright"):
        assert name in out
