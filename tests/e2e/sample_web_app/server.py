"""Tiny static server used by the e2e suite.

Started in a thread on an ephemeral port. Knows nothing about verify; just
serves files from its own directory.
"""

from __future__ import annotations

import http.server
import socket
import socketserver
import threading
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):  # quiet
        pass


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SampleServer:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(__file__).parent
        self.port = _pick_port()
        self._server: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        directory = str(self.directory)

        class Handler(_Handler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=directory, **kw)

        self._server = socketserver.TCPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self._server = None

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"
