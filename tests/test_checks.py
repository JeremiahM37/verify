"""Unit tests for each check type that can run without external state."""
from __future__ import annotations

from verify.checks import http, journalctl, shell, systemd, ui_cmd


def test_shell_passes_on_zero_exit():
    r = shell.run({"name": "t", "run": "true"})
    assert r["ok"] is True
    assert r["rc"] == 0


def test_shell_fails_on_nonzero_exit():
    r = shell.run({"name": "t", "run": "false"})
    assert r["ok"] is False
    assert r["rc"] == 1


def test_shell_captures_output_tail():
    r = shell.run({"name": "t", "run": "echo line1; echo line2; exit 1",
                   "tail": 5})
    assert r["ok"] is False
    assert "line2" in r["detail"]


def test_shell_times_out():
    r = shell.run({"name": "t", "run": "sleep 10", "timeout": 0.5})
    assert r["ok"] is False
    assert "timed out" in r["detail"]


def test_shell_missing_run_is_failure():
    r = shell.run({"name": "t"})
    assert r["ok"] is False
    assert "missing" in r["detail"]


def test_http_against_a_known_endpoint(monkeypatch):
    # We don't want to hit a real network; stub urlopen
    import urllib.request

    class FakeResp:
        def __init__(self, body, status):
            self._body = body
            self.status = status
        def read(self, n=None): return (self._body[:n] if n else self._body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout):
        return FakeResp('{"ok":true,"version":1}', 200)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = http.run({
        "name": "t",
        "targets": [
            {"url": "http://example.test/healthz", "status": 200,
             "contains": '"ok":true'},
        ],
    })
    assert r["ok"] is True
    assert r["items"][0]["status"] == 200


def test_http_fails_on_status_mismatch(monkeypatch):
    import urllib.request

    class FakeResp:
        status = 500
        def read(self, n=None): return b"oops"
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())

    r = http.run({"name": "t", "targets": [{"url": "http://x", "status": 200}]})
    assert r["ok"] is False


def test_http_fails_on_missing_contains(monkeypatch):
    import urllib.request

    class FakeResp:
        status = 200
        def read(self, n=None): return b"hello"
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())

    r = http.run({"name": "t", "targets": [
        {"url": "http://x", "status": 200, "contains": "missing"},
    ]})
    assert r["ok"] is False


def test_journalctl_passes_when_no_forbidden_strings(tmp_path, monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "INFO startup ok\nINFO request 200\n"
        stderr = ""
    def fake_run(*a, **k): return FakeProc()
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = journalctl.run({"name": "t", "units": ["fake"], "forbid": ["ERROR"]})
    assert r["ok"] is True


def test_journalctl_fails_on_traceback(monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = ("2026-05-26 INFO ok\n"
                  "2026-05-26 ERROR something exploded\n"
                  "Traceback (most recent call last):\n")
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    r = journalctl.run({"name": "t", "units": ["fake"]})
    assert r["ok"] is False
    assert any("Traceback" in h or "ERROR" in h for h in r["items"][0]["hits"])


def test_journalctl_ignore_filter(monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "ERROR(benign) deprecation warning logged\n"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    r = journalctl.run({
        "name": "t", "units": ["fake"],
        "forbid": ["ERROR"], "ignore": ["deprecation"],
    })
    assert r["ok"] is True


def test_systemd_active(monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "active\n"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    r = systemd.run({"name": "t", "units": ["fakeunit"]})
    assert r["ok"] is True
    assert r["items"][0]["state"] == "active"


def test_systemd_inactive(monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "inactive\n"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    r = systemd.run({"name": "t", "units": ["fakeunit"]})
    assert r["ok"] is False


def test_ui_check_reports_missing_url():
    r = ui_cmd.run({"name": "t"})
    assert r["ok"] is False
    assert "missing" in r["detail"]
