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


def test_backend_unavailable_setup_error(tmp_project):
    # backend=auto on empty dir -> only generic available -> picks generic.
    # Force a backend that doesn't exist.
    cfg = parse({"backend": "fake", "steps": []})
    with pytest.raises(Exception):
        # The runner will fail because "fake" is not registered.
        run(cfg, tmp_project)  # no fake_backend override


def test_setup_error_when_backend_start_throws(tmp_project, fake_backend):
    def boom(spec):
        raise RuntimeError("emulator boot failed")

    fake_backend.start = boom
    cfg = parse({"backend": "fake", "steps": [{"name": "x"}]})
    report = run(cfg, tmp_project, backend=fake_backend)
    assert report.setup_error
    assert "emulator boot failed" in report.setup_error
    assert not report.passed


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
