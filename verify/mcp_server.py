"""MCP server — exposes the backend's primitives to AI agents.

When a developer runs `verify mcp`, this starts an MCP stdio server. Claude
Code (or any MCP client) sees a small uniform tool surface:

    screenshot      -> image bytes (returned as ImageContent)
    click           -> takes x, y, optional button
    type_text       -> takes text
    key             -> takes name (enter, tab, back, ...)
    wait            -> takes seconds
    read_logs       -> takes lines
    navigate        -> takes url (no-op for non-web backends)
    locate          -> takes description; returns coords from vision

The MCP server reads the same `.verify.yaml` to know which backend to attach to
and how to launch it. The agent uses these tools to drive the sandbox like a
user — see what's on screen, decide where to tap, type, observe.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any


def serve(*, config_path: Path) -> None:
    """Start an MCP stdio server pinned to a single backend session.

    We use the official MCP SDK in stdio mode so Claude Code can spawn it via
    its mcp.json config.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise RuntimeError(
            "mcp SDK not installed (pip install verify-cli[mcp])"
        )
    from verify.config import load
    from verify.runner import _select_backend, _to_launch_spec
    from verify.vision import default_client as default_vision_client
    from verify.vision import locate as vision_locate

    cfg = load(config_path)
    project_dir = config_path.resolve().parent

    # Attach to the configured backend immediately. The agent gets a live
    # session — no separate "start" call.
    _, backend = _select_backend(cfg, project_dir, None)
    backend.start(_to_launch_spec(cfg))

    vision_client = None

    def _vision():
        nonlocal vision_client
        if vision_client is None:
            vision_client = default_vision_client()
        return vision_client

    mcp = FastMCP("verify")

    @mcp.tool()
    def screenshot() -> dict[str, Any]:
        """Capture the current target screen as a base64-encoded PNG."""
        png = backend.screenshot()
        return {
            "mime_type": "image/png",
            "data_base64": base64.b64encode(png).decode(),
            "width": backend.screen_size()[0],
            "height": backend.screen_size()[1],
        }

    @mcp.tool()
    def click(x: int, y: int, button: str = "left") -> str:
        """Click/tap at absolute pixel coordinates on the target screen."""
        backend.click(int(x), int(y), button=button)
        return f"clicked ({x}, {y})"

    @mcp.tool()
    def type_text(text: str) -> str:
        """Type literal text into whatever input has focus."""
        backend.type_text(text)
        return f"typed {len(text)} chars"

    @mcp.tool()
    def key(name: str) -> str:
        """Press a named key: enter, tab, escape, backspace, back, ..."""
        backend.key(name)
        return f"pressed {name}"

    @mcp.tool()
    def wait(seconds: float) -> str:
        """Sleep for `seconds` of wall-clock time."""
        backend.wait(float(seconds))
        return f"waited {seconds}s"

    @mcp.tool()
    def read_logs(lines: int = 100) -> str:
        """Return the most recent N log lines from the target."""
        return backend.read_logs(lines=int(lines))

    @mcp.tool()
    def navigate(url: str) -> str:
        """Navigate to URL (only meaningful for the web backend)."""
        backend.navigate(url)
        return f"navigated to {url}"

    @mcp.tool()
    def locate(description: str) -> dict[str, Any]:
        """Ask the vision model where a UI element is. Returns {x, y, found}."""
        png = backend.screenshot()
        coords = vision_locate(_vision(), png, description, backend.screen_size())
        if coords is None:
            return {"found": False, "x": None, "y": None}
        return {"found": True, "x": coords[0], "y": coords[1]}

    @mcp.tool()
    def screen_size() -> dict[str, int]:
        """Get current screen dimensions in pixels."""
        w, h = backend.screen_size()
        return {"width": w, "height": h}

    try:
        mcp.run(transport="stdio")
    finally:
        try:
            backend.stop()
        except Exception:
            pass
