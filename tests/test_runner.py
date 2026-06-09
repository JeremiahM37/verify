"""Tests for verify.runner — happy paths, failure paths, vision flow."""

from __future__ import annotations

import pathlib

import pytest

from verify.config import VerifyConfig, parse
from verify.runner import (
    _resolve_click_coords,
    _step_needs_vision,
    run,
)
from verify.vision import StubVisionClient


def _cfg(**overrides):
    base = {"backend": "fake", "steps": []}
    base.update(overrides)
    return parse(base)


def test_run_no_steps_passes(fake_backend, tmp_project):
    cfg = _cfg()
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    assert report.steps == []
    assert fake_backend.stopped


def test_run_passes_actions_to_backend(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {
                    "name": "drive",
                    "actions": [
                        {"navigate": "http://x"},
                        {"click": {"at": [10, 20]}},
                        {"type": "hi"},
                        {"key": "enter"},
                        {"wait": 0.01},
                    ],
                }
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    types = [name for name, *_ in fake_backend.events if name in {"navigate", "click", "type_text", "key"}]
    assert types == ["navigate", "click", "type_text", "key"]


def test_action_failure_short_circuits_step(fake_backend, tmp_project):
    # Missing target on navigate -> action fails.
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {
                    "name": "broken",
                    "actions": [
                        {"navigate": {}},  # no target — raises in handler
                        {"click": {"at": [1, 1]}},  # should NOT run
                    ],
                }
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    step = report.steps[0]
    assert step.actions[0].ok is False
    assert "navigate requires target" in step.actions[0].error
    # second action never ran
    assert len(step.actions) == 1
    # screenshot still attempted for the report
    assert step.screenshot_png == b"\x89PNG\r\n\x1a\nFAKE"


def test_vision_expectation_pass(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "look", "expect": {"vision": "dashboard is visible"}}],
        }
    )
    vision = StubVisionClient(
        {"dashboard": '{"pass": true, "reason": "looks like the dashboard"}'}
    )
    report = run(cfg, tmp_project, vision=vision, backend=fake_backend)
    assert report.passed
    assert report.steps[0].expect.vision.passed
    assert "dashboard" in report.steps[0].expect.vision.reason


def test_vision_expectation_fail(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "look", "expect": {"vision": "no error visible"}}],
        }
    )
    vision = StubVisionClient(
        {"error": '{"pass": false, "reason": "red banner: Network unavailable"}'}
    )
    report = run(cfg, tmp_project, vision=vision, backend=fake_backend)
    assert not report.passed
    assert not report.steps[0].expect.vision.passed


def test_url_contains_expectation(fake_backend, tmp_project):
    fake_backend.url = "http://localhost/dashboard"
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "url", "expect": {"url_contains": "/dashboard"}}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    assert report.steps[0].expect.url_ok is True
    assert "/dashboard" in report.steps[0].expect.url_actual


def test_url_contains_failure(fake_backend, tmp_project):
    fake_backend.url = "http://localhost/login"
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "url", "expect": {"url_contains": "/dashboard"}}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert report.steps[0].expect.url_ok is False


def test_log_contains_pass_and_fail(fake_backend, tmp_project):
    fake_backend.logs = "Server ready\nGET /\nFATAL: db crashed\n"

    pass_cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "log", "expect": {"log_contains": "Server ready"}}],
        }
    )
    assert run(pass_cfg, tmp_project, backend=fake_backend).passed

    fail_cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {"name": "no fatal", "expect": {"no_log_contains": "FATAL"}}
            ],
        }
    )
    assert not run(fail_cfg, tmp_project, backend=fake_backend).passed


def test_resolve_click_with_at(fake_backend):
    from verify.config import Action

    coords = _resolve_click_coords(fake_backend, Action(type="click", args={"at": [50, 60]}), None)
    assert coords == (50, 60)


def test_resolve_click_with_selector(fake_backend):
    from verify.config import Action

    fake_backend.dom["#btn"] = {
        "bounding_box": {"x": 100, "y": 200, "width": 40, "height": 20}
    }
    coords = _resolve_click_coords(
        fake_backend, Action(type="click", args={"selector": "#btn"}), None
    )
    assert coords == (120, 210)


def test_resolve_click_with_vision_locate(fake_backend):
    from verify.config import Action

    vision = StubVisionClient(
        {"login button": '{"x": 400, "y": 500, "found": true}'}
    )
    coords = _resolve_click_coords(
        fake_backend,
        Action(type="click", args={"locate": {"vision": "login button"}}),
        vision,
    )
    assert coords == (400, 500)


def test_resolve_click_vision_locate_failure(fake_backend):
    from verify.config import Action

    vision = StubVisionClient(default='{"x": 0, "y": 0, "found": false}')
    with pytest.raises(RuntimeError, match="vision could not locate"):
        _resolve_click_coords(
            fake_backend,
            Action(type="click", args={"locate": {"vision": "missing"}}),
            vision,
        )


def test_resolve_click_with_no_target_raises(fake_backend):
    from verify.config import Action

    with pytest.raises(ValueError, match="click requires"):
        _resolve_click_coords(fake_backend, Action(type="click", args={}), None)


def test_step_needs_vision_detection():
    from verify.config import Step, Action, Expect

    assert _step_needs_vision(Step(name="x", expect=Expect(vision="something")))
    assert _step_needs_vision(
        Step(
            name="x",
            actions=[Action(type="click", args={"locate": {"vision": "thing"}})],
        )
    )
    assert not _step_needs_vision(Step(name="x", expect=Expect(log_contains="hi")))
    assert not _step_needs_vision(Step(name="x"))


def test_unknown_backend_lands_in_setup_error(tmp_project):
    # "fake" is not registered; selection failure becomes setup_error.
    cfg = parse({"backend": "fake", "steps": []})
    report = run(cfg, tmp_project)
    assert not report.passed
    assert "backend selection failed" in report.setup_error


def test_setup_error_when_backend_start_throws(tmp_project, fake_backend):
    def boom(spec):
        raise RuntimeError("emulator boot failed")

    fake_backend.start = boom
    cfg = parse({"backend": "fake", "steps": [{"name": "x"}]})
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.setup_error
    assert "emulator boot failed" in report.setup_error
    assert not report.passed


def test_shell_action_runs_subprocess(fake_backend, tmp_project, tmp_path):
    """The `shell` action runs an arbitrary host command. Sentinel = file created."""
    marker = tmp_path / "marker.txt"
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {
                    "name": "shell",
                    "actions": [
                        {"shell": {"cmd": f"touch {marker}"}},
                    ],
                }
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    assert marker.exists()


def test_shell_action_failure_marks_step_failed(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {
                    "name": "shell-broken",
                    "actions": [{"shell": {"cmd": "false"}}],
                }
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    err = report.steps[0].actions[0].error
    assert "returned non-zero" in err or "CalledProcessError" in err


def test_shell_action_without_cmd_raises(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "actions": [{"shell": {}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "shell requires cmd" in report.steps[0].actions[0].error


def test_screenshot_action_is_synchronization_marker(fake_backend, tmp_project):
    """The `screenshot` action just takes a shot and discards. Used for syncing."""
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "sync", "actions": [{"screenshot": {}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    # 1 for the explicit action + 1 the runner takes for the report.
    shot_count = sum(1 for e in fake_backend.events if e[0] == "screenshot")
    assert shot_count >= 2


def test_type_action_without_text_raises(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "actions": [{"type": {}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "type requires text" in report.steps[0].actions[0].error


def test_key_action_without_name_raises(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "actions": [{"key": {}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "key requires name" in report.steps[0].actions[0].error


def test_navigate_action_accepts_url_alias(fake_backend, tmp_project):
    """`url:` also works as a synonym for `target:` on navigate."""
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {
                    "name": "navigate-with-url",
                    "actions": [{"type": "navigate", "url": "http://x"}],
                }
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.passed
    assert fake_backend.url == "http://x"


def test_click_at_must_be_two_element_list(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "actions": [{"click": {"at": [1, 2, 3]}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "must be [x, y]" in report.steps[0].actions[0].error


def test_click_selector_with_no_match_raises(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "actions": [{"click": {"selector": "#nope"}}]}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "not found" in report.steps[0].actions[0].error


def test_auto_detect_with_no_available_backend_raises(tmp_project, monkeypatch):
    """auto + nothing detectable => setup_error explaining how to debug."""

    # Patch the registry so detect_all returns only zero-confidence + unavailable
    # backends. Without this, the GenericBackend always reports 1 + available.
    from verify.backends.registry import DetectionMatch
    from verify.backends.base import DetectionResult

    class _UnavailBackend:
        name = "fake-unavail"

        @classmethod
        def is_available(cls):
            return False, "no host tools"

    monkeypatch.setattr(
        "verify.runner.detect_all",
        lambda d: [
            DetectionMatch(
                name="x", cls=_UnavailBackend, result=DetectionResult(50, "matched")
            )
        ],
    )
    cfg = parse({"backend": "auto", "steps": []})
    report = run(cfg, tmp_project)
    assert report.setup_error
    assert "auto-detect" in report.setup_error or "available" in report.setup_error


def test_configured_backend_unavailable(tmp_project, monkeypatch):
    """Explicit `backend: foo` but foo's host tools are missing => clean error."""
    from verify.backends.base import Backend as BaseBackend, DetectionResult
    from verify.backends.registry import _REGISTRY

    class _MissingBackend(BaseBackend):
        name = "missing"

        @classmethod
        def detect(cls, project_dir):
            return DetectionResult(0, "")

        @classmethod
        def is_available(cls):
            return False, "needs frobulator 9000"

        def start(self, spec):
            pass

        def stop(self):
            pass

        def screen_size(self):
            return (0, 0)

        def screenshot(self):
            return b""

        def click(self, x, y, button="left"):
            pass

        def type_text(self, text):
            pass

        def key(self, name):
            pass

        def read_logs(self, lines=100):
            return ""

    _REGISTRY["missing"] = _MissingBackend
    try:
        cfg = parse({"backend": "missing", "steps": []})
        report = run(cfg, tmp_project)
        assert report.setup_error
        assert "frobulator" in report.setup_error
    finally:
        _REGISTRY.pop("missing", None)


def test_setup_error_when_vision_provider_unavailable(fake_backend, tmp_project, monkeypatch):
    """If the step requires vision but no provider is configured, setup_error
    is set and the runner stops cleanly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VERIFY_VISION", raising=False)
    monkeypatch.delenv("VERIFY_OLLAMA_HOST", raising=False)
    monkeypatch.setattr("verify.vision._ollama_reachable", lambda *a, **kw: False)

    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "v", "expect": {"vision": "ok"}}],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    assert not report.passed
    assert "vision unavailable" in report.setup_error


def test_options_are_passed_through_to_backend_constructor(tmp_project, monkeypatch):
    """options.<backend-name> kwargs reach the backend's __init__."""
    captured: dict = {}

    from verify.backends.base import Backend as BaseBackend, DetectionResult
    from verify.backends.registry import _REGISTRY

    class _OptsBackend(BaseBackend):
        name = "opts-bk"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        @classmethod
        def detect(cls, project_dir):
            return DetectionResult(0, "")

        def start(self, spec):
            pass

        def stop(self):
            pass

        def screen_size(self):
            return (1, 1)

        def screenshot(self):
            return b""

        def click(self, x, y, button="left"):
            pass

        def type_text(self, text):
            pass

        def key(self, name):
            pass

        def read_logs(self, lines=100):
            return ""

    _REGISTRY["opts-bk"] = _OptsBackend
    try:
        cfg = parse(
            {
                "backend": "opts-bk",
                "options": {"opts-bk": {"foo": 1, "bar": "two"}},
                "steps": [],
            }
        )
        report = run(cfg, tmp_project)
        assert report.passed
        assert captured == {"foo": 1, "bar": "two"}
    finally:
        _REGISTRY.pop("opts-bk", None)


def test_screenshot_failure_does_not_crash_step(fake_backend, tmp_project):
    """If backend.screenshot() throws (rare), runner records None and continues."""

    def boom():
        raise RuntimeError("screen capture failed")

    fake_backend.screenshot = boom
    cfg = parse(
        {
            "backend": "fake",
            "steps": [{"name": "x", "expect": {"log_contains": "anything"}}],
        }
    )
    fake_backend.logs = "anything"
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.steps[0].screenshot_png is None
    # log_contains succeeded -> step passes despite screenshot failure
    assert report.steps[0].passed


def test_report_summary_string(fake_backend, tmp_project):
    cfg = parse(
        {
            "backend": "fake",
            "steps": [
                {"name": "a"},
                {"name": "b"},
            ],
        }
    )
    report = run(cfg, tmp_project, backend=fake_backend)
    s = report.summary()
    assert "PASS" in s and "2/2" in s
