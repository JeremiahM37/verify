"""Docker sandbox: provision throwaway containers backends can hide behind.

Why this module exists
----------------------

Some targets need a sandboxed environment that doesn't pollute the host:

  - Android emulator: budtmo/docker-android, etc. — gigantic image, opens an
    adb port, runs a real emulator.
  - Linux desktop in CI: an isolated Xvfb + the app + xdotool, in a container
    so multiple runs don't fight over `:99`.
  - Anything else you might want to swap in.

Each Sandbox starts a `docker run -d` with a unique label
`verify.session=<uuid>` and registers an atexit hook that stops the container
when the verify process exits — including SIGINT/SIGTERM. If the process
crashes hard (kill -9, power loss), the next verify run that imports this
module calls `prune_orphans()` which removes any verify.session=* container
older than 30 minutes by default, so the host doesn't slowly fill up with
zombies.

The sandbox layer is intentionally narrow: start, stop, host_port_for, env.
Backends use it as plumbing; the user-facing surface stays the same uniform
screenshot/click/type interface.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SESSION_LABEL = "verify.session"
KIND_LABEL = "verify.kind"
CREATED_LABEL = "verify.created"


def docker_available() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "docker not on PATH"
    try:
        r = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"docker not responsive: {e}"
    if r.returncode != 0:
        return False, f"docker server unreachable: {r.stderr.decode(errors='replace').strip()}"
    return True, ""


# Global registry of every sandbox started in this process. The atexit hook
# walks it and stops each container. Weakref to allow GC of explicitly stopped
# sandboxes.
_LIVE: weakref.WeakSet["Sandbox"] = weakref.WeakSet()
_LIVE_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def _ensure_atexit() -> None:
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    _ATEXIT_REGISTERED = True
    atexit.register(_atexit_cleanup)


def _atexit_cleanup() -> None:
    with _LIVE_LOCK:
        sandboxes = list(_LIVE)
    for s in sandboxes:
        try:
            s.stop()
        except Exception:
            pass


@dataclass(eq=False)
class Sandbox:
    """One running container, owned by this verify process.

    Don't instantiate directly — use Sandbox.run().
    """

    image: str
    kind: str  # "android-emulator" / "linux-desktop" / ... — for label only
    session_id: str
    container_id: str
    port_map: dict[int, int] = field(default_factory=dict)  # container_port -> host_port
    _stopped: bool = False

    @classmethod
    def run(
        cls,
        image: str,
        *,
        kind: str,
        ports: list[int] | None = None,
        env: dict[str, str] | None = None,
        volumes: dict[str, str] | None = None,
        cmd: list[str] | None = None,
        privileged: bool = False,
        devices: list[str] | None = None,
    ) -> "Sandbox":
        """Start a container. Returns a Sandbox whose .stop() removes it.

        ports: list of container ports to publish; host port is auto-assigned
               and returned in `port_map`.
        """
        ok, reason = docker_available()
        if not ok:
            raise RuntimeError(f"docker unavailable: {reason}")

        session_id = uuid.uuid4().hex[:12]
        created_at = str(int(time.time()))

        port_map: dict[int, int] = {}
        port_args: list[str] = []
        for cp in ports or []:
            hp = _pick_free_port()
            port_map[cp] = hp
            port_args += ["-p", f"127.0.0.1:{hp}:{cp}"]

        env_args: list[str] = []
        for k, v in (env or {}).items():
            env_args += ["-e", f"{k}={v}"]

        vol_args: list[str] = []
        for host, ctr in (volumes or {}).items():
            vol_args += ["-v", f"{host}:{ctr}"]

        dev_args: list[str] = []
        for d in devices or []:
            dev_args += ["--device", d]

        priv_args = ["--privileged"] if privileged else []

        full_cmd = [
            "docker", "run", "-d",
            "--rm",  # auto-remove on stop
            "--label", f"{SESSION_LABEL}={session_id}",
            "--label", f"{KIND_LABEL}={kind}",
            "--label", f"{CREATED_LABEL}={created_at}",
            *port_args, *env_args, *vol_args, *dev_args, *priv_args,
            image,
            *(cmd or []),
        ]

        r = subprocess.run(full_cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"docker run failed: {r.stderr.strip()}")
        container_id = r.stdout.strip()

        sb = cls(
            image=image,
            kind=kind,
            session_id=session_id,
            container_id=container_id,
            port_map=port_map,
        )
        _ensure_atexit()
        with _LIVE_LOCK:
            _LIVE.add(sb)
        return sb

    # ---- lifecycle -------------------------------------------------------

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop and remove the container. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        try:
            subprocess.run(
                ["docker", "stop", "-t", str(int(timeout)), self.container_id],
                capture_output=True,
                timeout=timeout + 5,
            )
        except Exception:
            # Best effort; --rm should clean it up regardless.
            try:
                subprocess.run(
                    ["docker", "kill", self.container_id],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
        with _LIVE_LOCK:
            _LIVE.discard(self)

    def host_port_for(self, container_port: int) -> int:
        if container_port not in self.port_map:
            raise KeyError(f"port {container_port} was not published")
        return self.port_map[container_port]

    def logs(self, lines: int = 100) -> str:
        try:
            r = subprocess.run(
                ["docker", "logs", "--tail", str(lines), self.container_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return (r.stdout or "") + (r.stderr or "")
        except Exception:
            return ""

    def exec(self, cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["docker", "exec", self.container_id, *cmd],
            capture_output=True,
        )

    def wait_for_log(self, needle: str, *, timeout: float = 120) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.logs(lines=2000):
                return True
            time.sleep(1.5)
        return False

    # ---- context manager ------------------------------------------------

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# ---- prune orphans -------------------------------------------------------


@dataclass
class OrphanInfo:
    container_id: str
    image: str
    session: str
    kind: str
    age_seconds: int


def list_orphans(*, older_than_seconds: int = 0) -> list[OrphanInfo]:
    """Return any container labeled verify.session=* older than the cutoff."""
    ok, _ = docker_available()
    if not ok:
        return []
    r = subprocess.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={SESSION_LABEL}",
            "--format", "{{json .}}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return []
    now = int(time.time())
    out: list[OrphanInfo] = []
    for line in r.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        labels = _parse_labels(row.get("Labels", ""))
        try:
            created = int(labels.get(CREATED_LABEL, "0"))
        except ValueError:
            created = 0
        age = now - created if created else 0
        if age < older_than_seconds:
            continue
        out.append(
            OrphanInfo(
                container_id=row.get("ID", ""),
                image=row.get("Image", ""),
                session=labels.get(SESSION_LABEL, ""),
                kind=labels.get(KIND_LABEL, ""),
                age_seconds=age,
            )
        )
    return out


def prune_orphans(*, older_than_seconds: int = 0) -> list[str]:
    """Stop+remove orphan sandbox containers. Returns IDs killed."""
    killed: list[str] = []
    for o in list_orphans(older_than_seconds=older_than_seconds):
        try:
            subprocess.run(
                ["docker", "rm", "-f", o.container_id],
                capture_output=True,
                timeout=10,
            )
            killed.append(o.container_id)
        except Exception:
            pass
    return killed


# ---- helpers -------------------------------------------------------------


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_labels(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in raw.split(","):
        kv = kv.strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out
