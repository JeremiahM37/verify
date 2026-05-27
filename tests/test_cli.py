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
