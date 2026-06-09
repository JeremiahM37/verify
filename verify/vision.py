"""Vision checks against Claude (or any vision LLM).

Two operations:

  assert_vision(png, expectation) -> VisionResult(passed, reason)
      "Looking at this screenshot, is <expectation> true?"

  locate(png, description, screen_size) -> (x, y) | None
      "Where on this screen is <description>?"

Both go through a `VisionClient` protocol so tests can swap in a deterministic
fake without making real API calls (or paying for tokens). The default client
hits the Anthropic API; the runner caches the client per session.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


# Model picked for speed + cost; Opus when you really want forensic vision.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Local-vision default — small enough to run on most homelab boxes.
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


@dataclass
class VisionResult:
    passed: bool
    reason: str
    raw: str = ""


class VisionClient(Protocol):
    """Minimal contract: take an image and a prompt, return text."""

    def ask(self, image_png: bytes, prompt: str, *, max_tokens: int = 400) -> str: ...


class AnthropicVisionClient:
    """Real client. Uses the Anthropic SDK if ANTHROPIC_API_KEY is set."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Either export it or pass an explicit "
                "VisionClient (see verify.vision.StubVisionClient for tests)."
            )
        from anthropic import Anthropic

        self._client = Anthropic(api_key=self._api_key)

    def ask(self, image_png: bytes, prompt: str, *, max_tokens: int = 400) -> str:
        b64 = base64.standard_b64encode(image_png).decode()
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        # Extract text from first content block.
        parts: list[str] = []
        for blk in msg.content:
            if getattr(blk, "type", None) == "text":
                parts.append(blk.text)
        return "".join(parts)


class OllamaVisionClient:
    """Local-model client. Hits Ollama's /api/generate with the image inline.

    Works with any vision-capable model in the Ollama registry: gemma4:e4b
    (small, fast), llava:13b (more accurate), qwen2.5vl:7b, llama3.2-vision, etc.

    Configure via env or constructor:
        VERIFY_OLLAMA_HOST   default http://127.0.0.1:11434
        VERIFY_OLLAMA_MODEL  default gemma4:e4b
    """

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        *,
        timeout: float = 120,
    ) -> None:
        self.model = model or os.environ.get("VERIFY_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.host = (host or os.environ.get("VERIFY_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)).rstrip("/")
        self.timeout = timeout

    def ask(self, image_png: bytes, prompt: str, *, max_tokens: int = 400) -> str:
        # Local models (esp. small ones) occasionally return an empty completion.
        # One retry is cheap and turns flaky into reliable.
        last = ""
        for attempt in range(2):
            body = json.dumps(
                {
                    "model": self.model,
                    "prompt": prompt,
                    "images": [base64.b64encode(image_png).decode()],
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0},
                }
            ).encode()
            req = urllib.request.Request(
                f"{self.host}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read())
            last = resp.get("response", "")
            if last.strip():
                return last
        return last


class StubVisionClient:
    """Deterministic test stub. Returns canned responses keyed by prompt substring.

    Usage:
        client = StubVisionClient({"dashboard": '{"pass": true, "reason": "ok"}'})
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = "{}"):
        self.responses = responses or {}
        self.default = default
        self.calls: list[tuple[bytes, str]] = []

    def ask(self, image_png: bytes, prompt: str, *, max_tokens: int = 400) -> str:
        self.calls.append((image_png, prompt))
        for needle, resp in self.responses.items():
            if needle in prompt:
                return resp
        return self.default


# ---- provider selection --------------------------------------------------


def default_client() -> VisionClient:
    """Pick a vision client based on environment.

    Resolution order:
      1. VERIFY_VISION=anthropic | ollama | none — explicit override
      2. ANTHROPIC_API_KEY set                    -> Anthropic
      3. VERIFY_OLLAMA_HOST or local 11434 reachable -> Ollama
      4. raise

    The runner only calls this lazily, so projects whose steps have no vision
    expectations never trigger client creation.
    """
    explicit = os.environ.get("VERIFY_VISION", "").lower().strip()
    if explicit == "anthropic":
        return AnthropicVisionClient()
    if explicit == "ollama":
        return OllamaVisionClient()
    if explicit == "none":
        raise RuntimeError("VERIFY_VISION=none — vision disabled")

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicVisionClient()
    if os.environ.get("VERIFY_OLLAMA_HOST") or _ollama_reachable(DEFAULT_OLLAMA_HOST):
        return OllamaVisionClient()
    raise RuntimeError(
        "no vision provider configured. Set ANTHROPIC_API_KEY, or run Ollama "
        "with a vision model (default gemma4:e4b) and set VERIFY_OLLAMA_HOST, "
        "or set VERIFY_VISION explicitly."
    )


def _ollama_reachable(host: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


# ---- high-level ops ------------------------------------------------------


_ASSERT_PROMPT = """You are inspecting a screenshot of an application UI under test.

Decide whether this statement is TRUE based on what is actually visible on the screen:

    {expectation}

Return ONLY a JSON object on a single line, no prose, no markdown:

    {{"pass": true|false, "reason": "<one sentence quoting what you see>"}}

Be strict. If you see any visible error message, modal, toast, or banner that
contradicts the statement, return pass=false even if the rest of the UI looks
right. If the screen is blank, white, or shows a stack trace, return pass=false.
"""


_LOCATE_PROMPT = """You are inspecting a screenshot of an application UI under test.

Find the center pixel coordinates of: {description}

The screenshot is {width} pixels wide and {height} pixels tall, with (0,0) at the
top-left. Return ONLY a JSON object on a single line, no prose, no markdown:

    {{"x": <int>, "y": <int>, "found": true}}

If the element is not visible, return:

    {{"x": 0, "y": 0, "found": false, "reason": "<why>"}}
"""


def assert_vision(client: VisionClient, image_png: bytes, expectation: str) -> VisionResult:
    raw = client.ask(image_png, _ASSERT_PROMPT.format(expectation=expectation))
    parsed = _extract_json(raw)
    if parsed is None:
        return VisionResult(
            passed=False, reason=f"vision response not JSON: {raw[:200]}", raw=raw
        )
    return VisionResult(
        passed=bool(parsed.get("pass")),
        reason=str(parsed.get("reason", "")),
        raw=raw,
    )


def locate(
    client: VisionClient,
    image_png: bytes,
    description: str,
    screen_size: tuple[int, int],
) -> tuple[int, int] | None:
    w, h = screen_size
    raw = client.ask(
        image_png, _LOCATE_PROMPT.format(description=description, width=w, height=h)
    )
    parsed = _extract_json(raw)
    if not parsed or not parsed.get("found"):
        return None
    try:
        x = int(parsed["x"])
        y = int(parsed["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= w and 0 <= y <= h):
        return None
    return (x, y)


_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Be lenient: strip code fences, find the first {...} block, parse it."""
    text = text.strip()
    if text.startswith("```"):
        # Drop fenced block markers.
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    # Try direct.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Try greediest matching {...}.
    matches = list(re.finditer(r"\{.*\}", text, re.DOTALL))
    for m in reversed(matches):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None
