"""The `launch.ready_when` gate.

A launched target is rarely ready the instant the process exists: a server binds
its port after loading a model, a dev server compiles first. Without a gate the
first step runs against a target that is still starting, and the run fails in a
way that looks like a broken app rather than a slow one.

Two signals, because targets advertise readiness differently:

* `log_contains` — a substring in the target's own log output. Works for
  anything that prints a ready line, including targets with no HTTP surface.
* `url` — poll an HTTP endpoint until it answers. Works for a server whose logs
  are silent, quiet, or buffered.

Both may be given, in which case both must hold. Which one you can use is a
property of the target, not a preference, so neither is the "real" one.
"""
from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT = 60.0
POLL_INTERVAL = 0.25

# Keys the schema accepts. Kept here so config validation and this module cannot
# drift apart — an accepted-but-ignored key is the failure this whole gate
# exists to prevent.
KEYS = {"log_contains", "url", "timeout"}


def wait_for(
    ready_when: dict[str, Any],
    *,
    read_logs: Callable[[], str] | None = None,
    proc: subprocess.Popen | None = None,
) -> None:
    """Block until every configured condition holds.

    `read_logs` supplies the target's log text for `log_contains`; omit it when
    the caller has no log access and only the url condition applies.

    `proc` is the launched process, when the caller has it. If it exits while we
    are waiting we fail immediately rather than burning the whole timeout — a
    dead spawn usually means the port was already taken, and in that case the
    url would answer from a STRANGER'S process and the run would silently test
    something it never launched.
    """
    needle = ready_when.get("log_contains")
    url = ready_when.get("url")
    timeout = float(ready_when.get("timeout", DEFAULT_TIMEOUT))
    deadline = time.monotonic() + timeout

    log_ok = needle is None
    url_ok = url is None
    last_url_error = ""

    while True:
        if proc is not None and proc.poll() is not None:
            raise TimeoutError(
                f"ready_when: the launched process exited with code "
                f"{proc.returncode} before becoming ready (port already in use?)"
            )
        if not log_ok and read_logs is not None and needle in read_logs():
            log_ok = True
        if not url_ok:
            try:
                with urllib.request.urlopen(url, timeout=5):
                    url_ok = True
            except urllib.error.HTTPError:
                # it answered; a 4xx/5xx still means something is listening, and
                # a health endpoint may legitimately report unhealthy at first
                url_ok = True
            except Exception as e:  # noqa: BLE001 — not up yet, keep polling
                last_url_error = str(e)
        if log_ok and url_ok:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(_describe(needle, url, log_ok, url_ok,
                                         timeout, last_url_error))
        time.sleep(POLL_INTERVAL)


def _describe(needle, url, log_ok, url_ok, timeout, last_url_error) -> str:
    """Say which condition was not met, so the failure is actionable."""
    unmet = []
    if not log_ok:
        unmet.append(f"{needle!r} did not appear in logs")
    if not url_ok:
        detail = f" ({last_url_error})" if last_url_error else ""
        unmet.append(f"{url} did not answer{detail}")
    return f"ready_when: {' and '.join(unmet)} within {timeout:g}s"
