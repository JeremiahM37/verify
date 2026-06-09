"""Tests for verify.vision — JSON extraction + assert/locate semantics."""

from __future__ import annotations

import pytest

import io
import json

from verify.vision import (
    AnthropicVisionClient,
    OllamaVisionClient,
    StubVisionClient,
    VisionResult,
    _extract_json,
    assert_vision,
    default_client,
    locate,
)


def test_extract_plain_json():
    assert _extract_json('{"pass": true, "reason": "ok"}') == {
        "pass": True,
        "reason": "ok",
    }


def test_extract_fenced_json():
    raw = '```json\n{"x": 1}\n```'
    assert _extract_json(raw) == {"x": 1}


def test_extract_embedded_json():
    raw = "here is what I see: {\"pass\": true, \"reason\": \"home page\"} ok?"
    assert _extract_json(raw) == {"pass": True, "reason": "home page"}


def test_extract_returns_none_for_garbage():
    assert _extract_json("nothing useful here") is None


def test_assert_vision_pass():
    client = StubVisionClient({"home page": '{"pass": true, "reason": "loaded"}'})
    r = assert_vision(client, b"x", "home page is loaded")
    assert r.passed is True
    assert "loaded" in r.reason


def test_assert_vision_fail():
    client = StubVisionClient(
        {"home page": '{"pass": false, "reason": "blank white screen"}'}
    )
    r = assert_vision(client, b"x", "home page is loaded")
    assert r.passed is False
    assert "blank" in r.reason


def test_assert_vision_unparseable_response_fails():
    client = StubVisionClient(default="lol the model said no json")
    r = assert_vision(client, b"x", "anything")
    assert r.passed is False
    assert "not JSON" in r.reason


def test_locate_returns_coords():
    client = StubVisionClient({"button": '{"x": 100, "y": 200, "found": true}'})
    coords = locate(client, b"x", "the submit button", (1280, 800))
    assert coords == (100, 200)


def test_locate_returns_none_when_not_found():
    client = StubVisionClient(
        {"button": '{"x": 0, "y": 0, "found": false, "reason": "not visible"}'}
    )
    assert locate(client, b"x", "the submit button", (1280, 800)) is None


def test_locate_returns_none_when_coords_out_of_bounds():
    # x=2000 > screen width 1280 — must be rejected.
    client = StubVisionClient(default='{"x": 2000, "y": 100, "found": true}')
    assert locate(client, b"x", "anywhere", (1280, 800)) is None


def test_locate_returns_none_on_garbage_response():
    client = StubVisionClient(default="not json")
    assert locate(client, b"x", "anywhere", (800, 600)) is None


def test_anthropic_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicVisionClient(api_key=None)


def test_stub_vision_client_records_calls():
    client = StubVisionClient({"foo": "{}"}, default="{}")
    client.ask(b"img", "say foo please")
    client.ask(b"img2", "different prompt")
    assert len(client.calls) == 2
    assert client.calls[0][1] == "say foo please"


# ---- OllamaVisionClient --------------------------------------------------


def test_ollama_client_posts_image_and_returns_response(monkeypatch):
    sent: dict = {}

    class FakeResp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return FakeResp({"response": '{"pass": true, "reason": "ok"}'})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaVisionClient(model="testmodel", host="http://x:1234")
    out = client.ask(b"PNG-BYTES", "is this ok?")
    assert "pass" in out
    assert sent["url"] == "http://x:1234/api/generate"
    assert sent["body"]["model"] == "testmodel"
    assert sent["body"]["stream"] is False
    assert len(sent["body"]["images"]) == 1
    # base64-encoded PNG-BYTES
    import base64

    assert sent["body"]["images"][0] == base64.b64encode(b"PNG-BYTES").decode()


def test_ollama_client_reads_env_defaults(monkeypatch):
    monkeypatch.setenv("VERIFY_OLLAMA_HOST", "http://lan-box:11434")
    monkeypatch.setenv("VERIFY_OLLAMA_MODEL", "llava:13b")
    client = OllamaVisionClient()
    assert client.host == "http://lan-box:11434"
    assert client.model == "llava:13b"


# ---- default_client provider selection ----------------------------------


def test_default_client_picks_anthropic_when_key_set(monkeypatch):
    monkeypatch.delenv("VERIFY_VISION", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    # Don't actually construct a real anthropic client — patch class.
    captured = {"called": False}

    class FakeAnthropic:
        def __init__(self, *a, **kw):
            captured["called"] = True

    monkeypatch.setattr("verify.vision.AnthropicVisionClient", FakeAnthropic)
    default_client()
    assert captured["called"]


def test_default_client_picks_ollama_when_only_ollama_present(monkeypatch):
    monkeypatch.delenv("VERIFY_VISION", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VERIFY_OLLAMA_HOST", raising=False)
    monkeypatch.setattr("verify.vision._ollama_reachable", lambda host, **kw: True)
    captured = {"called": False}

    class FakeOllama:
        def __init__(self, *a, **kw):
            captured["called"] = True

    monkeypatch.setattr("verify.vision.OllamaVisionClient", FakeOllama)
    default_client()
    assert captured["called"]


def test_default_client_raises_when_nothing_available(monkeypatch):
    monkeypatch.delenv("VERIFY_VISION", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VERIFY_OLLAMA_HOST", raising=False)
    monkeypatch.setattr("verify.vision._ollama_reachable", lambda host, **kw: False)
    with pytest.raises(RuntimeError, match="no vision provider"):
        default_client()


def test_default_client_explicit_override_ollama(monkeypatch):
    monkeypatch.setenv("VERIFY_VISION", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # ANTHROPIC_API_KEY set OR not — explicit override wins.
    captured = {"called": False}

    class FakeOllama:
        def __init__(self, *a, **kw):
            captured["called"] = True

    monkeypatch.setattr("verify.vision.OllamaVisionClient", FakeOllama)
    default_client()
    assert captured["called"]


def test_default_client_explicit_none_raises(monkeypatch):
    monkeypatch.setenv("VERIFY_VISION", "none")
    with pytest.raises(RuntimeError, match="disabled"):
        default_client()
