"""Tests for verify.sandbox — Docker provisioning + cleanup contract."""

from __future__ import annotations

import shutil
import subprocess
import types

import pytest

from verify import sandbox as sb


class FakeRun:
    """Records all subprocess.run invocations and returns canned results."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.handlers: list = []  # list of (predicate, response)
        self.default = types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def add(self, predicate, *, returncode=0, stdout=b"", stderr=b""):
        self.handlers.append(
            (predicate, types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr))
        )

    def __call__(self, args, **kwargs):
        self.calls.append(args if isinstance(args, list) else list(args))
        for pred, resp in self.handlers:
            if pred(args):
                # Mimic text=True if asked.
                if kwargs.get("text") or kwargs.get("capture_output") and isinstance(resp.stdout, bytes):
                    if kwargs.get("text"):
                        return types.SimpleNamespace(
                            returncode=resp.returncode,
                            stdout=resp.stdout.decode() if isinstance(resp.stdout, bytes) else resp.stdout,
                            stderr=resp.stderr.decode() if isinstance(resp.stderr, bytes) else resp.stderr,
                        )
                return resp
        # Default: success
        if kwargs.get("text"):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return self.default


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(subprocess, "run", fr)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name if name == "docker" else None)
    return fr


# ---- docker_available ----------------------------------------------------


def test_docker_available_true(fake_run):
    fake_run.add(
        lambda a: a[:2] == ["docker", "version"],
        returncode=0,
        stdout=b"24.0.7",
    )
    ok, reason = sb.docker_available()
    assert ok is True


def test_docker_available_no_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, reason = sb.docker_available()
    assert ok is False
    assert "not on PATH" in reason


def test_docker_available_server_unreachable(fake_run):
    fake_run.add(
        lambda a: a[:2] == ["docker", "version"],
        returncode=1,
        stderr=b"Cannot connect to the Docker daemon",
    )
    ok, reason = sb.docker_available()
    assert ok is False
    assert "unreachable" in reason


# ---- Sandbox.run lifecycle ----------------------------------------------


def test_sandbox_run_invokes_docker_run_with_label(fake_run, monkeypatch):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "run", "-d"],
        returncode=0,
        stdout=b"abc123def456\n",
    )
    monkeypatch.setattr(sb, "_pick_free_port", lambda: 49999)

    s = sb.Sandbox.run(
        "myimage:latest", kind="android-emulator", ports=[5555]
    )
    # Check docker run call shape.
    run_call = [c for c in fake_run.calls if c[:3] == ["docker", "run", "-d"]][0]
    assert "--label" in run_call
    label_idx = run_call.index("--label") + 1
    assert run_call[label_idx].startswith(f"{sb.SESSION_LABEL}=")
    # Ports were published with auto-assigned host port.
    assert "-p" in run_call
    p_idx = run_call.index("-p") + 1
    assert run_call[p_idx] == "127.0.0.1:49999:5555"
    # Container id stored.
    assert s.container_id == "abc123def456"
    assert s.kind == "android-emulator"
    assert s.host_port_for(5555) == 49999
    # Registered for atexit cleanup.
    assert s in sb._LIVE


def test_sandbox_run_raises_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="docker unavailable"):
        sb.Sandbox.run("x", kind="x")


def test_sandbox_run_raises_on_docker_failure(fake_run):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "run", "-d"],
        returncode=1,
        stderr=b"no such image",
    )
    with pytest.raises(RuntimeError, match="no such image"):
        sb.Sandbox.run("x", kind="x")


def test_sandbox_stop_idempotent(fake_run):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "run", "-d"],
        returncode=0,
        stdout=b"abcd\n",
    )
    s = sb.Sandbox.run("img", kind="x")
    s.stop()
    # Second stop is a no-op (no exception).
    s.stop()
    stops = [c for c in fake_run.calls if c[:2] == ["docker", "stop"]]
    assert len(stops) == 1
    # Removed from live registry.
    assert s not in sb._LIVE


def test_sandbox_context_manager_stops_on_exit(fake_run):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "run", "-d"], returncode=0, stdout=b"cid\n"
    )
    with sb.Sandbox.run("img", kind="x") as s:
        cid = s.container_id
    assert any(c[:2] == ["docker", "stop"] and cid in c for c in fake_run.calls)


def test_atexit_cleans_up_live_sandboxes(fake_run):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "run", "-d"], returncode=0, stdout=b"cid\n"
    )
    s = sb.Sandbox.run("img", kind="x")
    assert s in sb._LIVE
    sb._atexit_cleanup()
    assert s._stopped


# ---- port utilities ------------------------------------------------------


def test_pick_free_port_returns_valid_port():
    p = sb._pick_free_port()
    assert 1024 < p < 65536


# ---- orphan listing + pruning -------------------------------------------


def test_list_orphans_parses_docker_ps(fake_run, monkeypatch):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    rows = [
        b'{"ID": "111", "Image": "x:1", "Labels": "verify.session=abc,verify.kind=android-emulator,verify.created=100"}',
        b'{"ID": "222", "Image": "y:2", "Labels": "verify.session=def,verify.kind=linux-desktop,verify.created=200"}',
    ]
    fake_run.add(
        lambda a: a[:3] == ["docker", "ps", "-a"],
        returncode=0,
        stdout=b"\n".join(rows),
    )
    monkeypatch.setattr("time.time", lambda: 500.0)
    orphans = sb.list_orphans()
    assert {o.container_id for o in orphans} == {"111", "222"}
    by_id = {o.container_id: o for o in orphans}
    assert by_id["111"].kind == "android-emulator"
    assert by_id["111"].age_seconds == 400
    assert by_id["222"].age_seconds == 300


def test_list_orphans_respects_older_than(fake_run, monkeypatch):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    rows = [
        b'{"ID": "young", "Image": "x", "Labels": "verify.session=a,verify.created=400"}',
        b'{"ID": "old", "Image": "x", "Labels": "verify.session=b,verify.created=100"}',
    ]
    fake_run.add(
        lambda a: a[:3] == ["docker", "ps", "-a"],
        returncode=0,
        stdout=b"\n".join(rows),
    )
    monkeypatch.setattr("time.time", lambda: 500.0)
    # 500-400=100s old; 500-100=400s old. Cutoff 200 -> only "old".
    orphans = sb.list_orphans(older_than_seconds=200)
    assert [o.container_id for o in orphans] == ["old"]


def test_prune_orphans_removes_them(fake_run, monkeypatch):
    fake_run.add(lambda a: a[:2] == ["docker", "version"], returncode=0, stdout=b"24")
    fake_run.add(
        lambda a: a[:3] == ["docker", "ps", "-a"],
        returncode=0,
        stdout=b'{"ID": "doomed", "Image": "x", "Labels": "verify.session=z,verify.created=100"}\n',
    )
    monkeypatch.setattr("time.time", lambda: 1000.0)
    killed = sb.prune_orphans(older_than_seconds=0)
    assert killed == ["doomed"]
    rms = [c for c in fake_run.calls if c[:3] == ["docker", "rm", "-f"]]
    assert rms and rms[0][-1] == "doomed"


def test_prune_orphans_returns_empty_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert sb.prune_orphans() == []


# ---- helpers -------------------------------------------------------------


def test_parse_labels():
    out = sb._parse_labels(" verify.session=abc, verify.kind=android , other=x ")
    assert out == {"verify.session": "abc", "verify.kind": "android", "other": "x"}
