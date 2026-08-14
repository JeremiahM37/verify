"""The launch.ready_when gate.

The whole point of this key is that a config which sets it gets a real wait, so
these tests care about two things: that each signal actually blocks until it is
satisfied, and that a misconfiguration is refused rather than silently ignored.
"""
from __future__ import annotations

import http.server
import socket
import subprocess
import sys
import threading
import time

import pytest

from verify import readiness
from verify.config import ConfigError, parse


class _OKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_once_after(port: int, delay: float) -> threading.Thread:
    """Start answering on `port` only after `delay`, so the gate has to wait."""
    def run():
        time.sleep(delay)
        srv = http.server.HTTPServer(("127.0.0.1", port), _OKHandler)
        srv.handle_request()
        srv.server_close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---- log_contains ----------------------------------------------------------

def test_log_contains_waits_then_returns():
    lines = []
    threading.Timer(0.3, lambda: lines.append("server ready")).start()
    started = time.monotonic()
    readiness.wait_for({"log_contains": "server ready", "timeout": 10},
                       read_logs=lambda: "\n".join(lines))
    assert time.monotonic() - started >= 0.25, "returned before the line appeared"


def test_log_contains_times_out_with_the_needle_in_the_message():
    with pytest.raises(TimeoutError, match="never-appears"):
        readiness.wait_for({"log_contains": "never-appears", "timeout": 0.4},
                           read_logs=lambda: "nothing useful here")


# ---- url -------------------------------------------------------------------

def test_url_waits_for_a_server_that_appears_late():
    port = _free_port()
    t = _serve_once_after(port, 0.4)
    started = time.monotonic()
    readiness.wait_for({"url": f"http://127.0.0.1:{port}/", "timeout": 15})
    assert time.monotonic() - started >= 0.3, "returned before the server existed"
    t.join(timeout=5)


def test_url_accepts_an_error_status():
    """A 4xx/5xx still means something is listening — and a health endpoint may
    legitimately report unhealthy while it finishes starting."""
    class _500(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.end_headers()

        def log_message(self, *a):
            pass

    port = _free_port()

    def run():
        srv = http.server.HTTPServer(("127.0.0.1", port), _500)
        srv.handle_request()
        srv.server_close()

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.1)
    readiness.wait_for({"url": f"http://127.0.0.1:{port}/", "timeout": 10})


def test_url_times_out_with_the_address_in_the_message():
    port = _free_port()
    with pytest.raises(TimeoutError, match=f"{port}"):
        readiness.wait_for({"url": f"http://127.0.0.1:{port}/", "timeout": 0.4})


# ---- both ------------------------------------------------------------------

def test_both_conditions_must_hold():
    """With both keys the gate must not return on the first one alone."""
    port = _free_port()
    lines = ["server ready"]                       # log condition already met
    with pytest.raises(TimeoutError, match="did not answer"):
        readiness.wait_for(
            {"log_contains": "server ready", "url": f"http://127.0.0.1:{port}/",
             "timeout": 0.4},
            read_logs=lambda: "\n".join(lines))

    t = _serve_once_after(port, 0.2)
    readiness.wait_for(
        {"log_contains": "server ready", "url": f"http://127.0.0.1:{port}/",
         "timeout": 15},
        read_logs=lambda: "\n".join(lines))
    t.join(timeout=5)


# ---- dead spawn ------------------------------------------------------------

def test_a_dead_spawn_fails_immediately():
    """A launched process that exits usually means the port was already taken —
    in which case the url answers from a STRANGER'S process and the run would
    silently test something it never launched."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    proc.wait(timeout=10)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="exited with code 3"):
        readiness.wait_for({"url": f"http://127.0.0.1:{port}/", "timeout": 30},
                           proc=proc)
    assert time.monotonic() - started < 5, "burned the timeout instead of failing fast"


# ---- config validation -----------------------------------------------------

def _cfg(ready: dict) -> dict:
    return {"backend": "web",
            "launch": {"command": "./serve", "ready_when": ready},
            "steps": [{"name": "s", "actions": [{"shell": "true"}]}]}


def test_config_accepts_either_key_and_both():
    for ready in ({"log_contains": "up"},
                  {"url": "http://127.0.0.1:1/"},
                  {"log_contains": "up", "url": "http://127.0.0.1:1/", "timeout": 5}):
        assert parse(_cfg(ready)).launch.ready_when == ready


def test_config_rejects_an_empty_gate():
    """A ready_when with no condition would be accepted and do nothing — the
    exact failure this key exists to prevent."""
    with pytest.raises(ConfigError, match="log_contains or url"):
        parse(_cfg({"timeout": 5}))


def test_config_rejects_unknown_and_malformed_keys():
    with pytest.raises(ConfigError, match="unknown keys"):
        parse(_cfg({"log_contains": "up", "wait_for_it": True}))
    with pytest.raises(ConfigError, match="non-empty string"):
        parse(_cfg({"url": ""}))
    with pytest.raises(ConfigError, match="timeout must be a number"):
        parse(_cfg({"url": "http://x/", "timeout": "soon"}))
