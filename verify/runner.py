"""Runner: drive a backend through a verify config and produce a report.

The runner is intentionally narrow:

  - Pick a backend (explicit `backend:` in YAML, or auto-detect).
  - Start it with the launch spec.
  - For each step, run actions in order. `click` may resolve coords via vision.
  - After actions, evaluate the step's `expect` block (vision + log + URL).
  - Collect a list of StepResult; emit overall PASS/FAIL.

Failures are recorded with the screenshot at the point of failure, the vision
rationale, and the last 100 lines of logs — so when verify reports "the
keyboard input never reached the field", you can actually see why.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verify.backends.base import Backend, LaunchSpec
from verify.backends.registry import detect_all, get
from verify.config import Action, Expect, Step, VerifyConfig
from verify.vision import (
    VisionClient,
    VisionResult,
    assert_vision,
    default_client as default_vision_client,
    locate,
)


@dataclass
class ActionResult:
    action: Action
    ok: bool
    error: str = ""


@dataclass
class ExpectResult:
    vision: VisionResult | None = None
    url_ok: bool | None = None
    url_actual: str | None = None
    log_ok: bool | None = None
    log_excerpt: str = ""

    @property
    def passed(self) -> bool:
        if self.vision is not None and not self.vision.passed:
            return False
        if self.url_ok is False:
            return False
        if self.log_ok is False:
            return False
        return True


@dataclass
class StepResult:
    step: Step
    actions: list[ActionResult] = field(default_factory=list)
    expect: ExpectResult | None = None
    screenshot_png: bytes | None = None
    error: str = ""

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        if any(not a.ok for a in self.actions):
            return False
        if self.expect is not None and not self.expect.passed:
            return False
        return True


@dataclass
class RunReport:
    backend: str
    steps: list[StepResult] = field(default_factory=list)
    setup_error: str = ""

    @property
    def passed(self) -> bool:
        if self.setup_error:
            return False
        return all(s.passed for s in self.steps)

    def summary(self) -> str:
        total = len(self.steps)
        passed = sum(1 for s in self.steps if s.passed)
        verdict = "PASS" if self.passed else "FAIL"
        return f"{verdict}: {passed}/{total} steps passed (backend={self.backend})"


# ---- main entry ----------------------------------------------------------


def run(
    config: VerifyConfig,
    project_dir: Path,
    *,
    vision: VisionClient | None = None,
    backend: Backend | None = None,
) -> RunReport:
    """Execute the config and return a RunReport.

    `backend` and `vision` are optional injection points for testing.
    """

    try:
        backend_name, backend_obj = _select_backend(config, project_dir, backend)
    except Exception as e:
        # Surface as setup_error rather than propagating — the CLI prints the
        # report and exits 1, which is the same outcome but with a structured
        # message ("backend foo unavailable: needs xdotool") rather than a
        # traceback.
        return RunReport(backend=config.backend, setup_error=f"backend selection failed: {e}")
    report = RunReport(backend=backend_name)

    # Lazy: vision client only needed if some step uses it.
    needs_vision = any(
        _step_needs_vision(s) for s in config.steps
    )
    if needs_vision and vision is None:
        try:
            vision = default_vision_client()
        except Exception as e:
            report.setup_error = f"vision unavailable: {e}"
            return report

    try:
        backend_obj.start(_to_launch_spec(config))
    except Exception as e:
        report.setup_error = f"backend start failed: {e}"
        # Ensure partial resources (e.g. a started Playwright instance) are
        # cleaned up — otherwise the leaked event loop poisons later sessions.
        try:
            backend_obj.stop()
        except Exception:
            pass
        return report

    try:
        for step in config.steps:
            sr = _run_step(backend_obj, step, vision)
            report.steps.append(sr)
    finally:
        try:
            backend_obj.stop()
        except Exception:
            pass

    return report


def _step_needs_vision(s: Step) -> bool:
    if s.expect and s.expect.vision:
        return True
    for a in s.actions:
        loc = a.args.get("locate")
        if isinstance(loc, dict) and loc.get("vision"):
            return True
    return False


def _select_backend(
    config: VerifyConfig, project_dir: Path, override: Backend | None
) -> tuple[str, Backend]:
    if override is not None:
        return override.name, override
    name = config.backend
    if name == "auto":
        for m in detect_all(project_dir):
            if m.result.confidence > 0 and m.available:
                name = m.name
                break
        else:
            raise RuntimeError(
                "auto-detect found no available backend. Run `verify backends`."
            )
    cls = get(name)
    ok, reason = cls.is_available()
    if not ok:
        raise RuntimeError(f"backend {name!r} unavailable: {reason}")
    # Pass through backend-specific options.
    obj = cls(**(config.options.get(name) or {}))
    return name, obj


def _to_launch_spec(config: VerifyConfig) -> LaunchSpec:
    lc = config.launch
    return LaunchSpec(
        command=lc.command,
        args=list(lc.args),
        url=lc.url,
        package=lc.package,
        env=dict(lc.env),
        cwd=Path(lc.cwd) if lc.cwd else None,
        ready_when=lc.ready_when,
        wait_after=lc.wait_after,
    )


def _run_step(backend: Backend, step: Step, vision: VisionClient | None) -> StepResult:
    sr = StepResult(step=step)
    try:
        for action in step.actions:
            ar = _run_action(backend, action, vision)
            sr.actions.append(ar)
            if not ar.ok:
                break
        # Even if an action failed, still try to screenshot for the report.
        try:
            sr.screenshot_png = backend.screenshot()
        except Exception:
            sr.screenshot_png = None
        if step.expect is not None:
            sr.expect = _evaluate_expect(backend, step.expect, sr.screenshot_png, vision)
    except Exception as e:
        sr.error = repr(e)
    return sr


def _run_action(
    backend: Backend, action: Action, vision: VisionClient | None
) -> ActionResult:
    try:
        if action.type == "navigate":
            target = action.args.get("target") or action.args.get("url")
            if not target:
                raise ValueError("navigate requires target")
            backend.navigate(target)
        elif action.type == "click":
            x, y = _resolve_click_coords(backend, action, vision)
            backend.click(x, y, button=action.args.get("button", "left"))
        elif action.type == "type":
            text = action.args.get("text")
            if text is None:
                raise ValueError("type requires text")
            backend.type_text(str(text))
        elif action.type == "key":
            name = action.args.get("name") or action.args.get("value")
            if not name:
                raise ValueError("key requires name")
            backend.key(str(name))
        elif action.type == "wait":
            backend.wait(float(action.args.get("seconds", 1.0)))
        elif action.type == "screenshot":
            backend.screenshot()  # discard; just a synchronization marker
        elif action.type == "shell":
            cmd = action.args.get("cmd")
            if not cmd:
                raise ValueError("shell requires cmd")
            import subprocess

            subprocess.run(cmd, shell=True, check=True)
        else:
            raise ValueError(f"runner has no handler for action {action.type!r}")
        return ActionResult(action=action, ok=True)
    except Exception as e:
        return ActionResult(action=action, ok=False, error=repr(e))


def _resolve_click_coords(
    backend: Backend, action: Action, vision: VisionClient | None
) -> tuple[int, int]:
    if "at" in action.args:
        at = action.args["at"]
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            raise ValueError("click.at must be [x, y]")
        return int(at[0]), int(at[1])
    if "selector" in action.args and hasattr(backend, "query_dom"):
        sel = action.args["selector"]
        info = backend.query_dom(sel)
        if info and info.get("bounding_box"):
            box = info["bounding_box"]
            cx = int(box["x"] + box["width"] / 2)
            cy = int(box["y"] + box["height"] / 2)
            return cx, cy
        raise RuntimeError(f"selector {sel!r} not found")
    locate_spec = action.args.get("locate")
    if isinstance(locate_spec, dict) and locate_spec.get("vision"):
        if vision is None:
            raise RuntimeError("vision locator used but no vision client available")
        png = backend.screenshot()
        coords = locate(vision, png, locate_spec["vision"], backend.screen_size())
        if coords is None:
            raise RuntimeError(
                f"vision could not locate: {locate_spec['vision']!r}"
            )
        return coords
    raise ValueError("click requires one of: at, selector, locate.vision")


def _evaluate_expect(
    backend: Backend,
    expect: Expect,
    screenshot: bytes | None,
    vision: VisionClient | None,
) -> ExpectResult:
    out = ExpectResult()
    if expect.vision and screenshot is not None and vision is not None:
        out.vision = assert_vision(vision, screenshot, expect.vision)
    if expect.url_contains is not None:
        try:
            url = backend.current_url()  # type: ignore[attr-defined]
            out.url_actual = url
            out.url_ok = expect.url_contains in url
        except Exception:
            out.url_actual = None
            out.url_ok = False
    if expect.log_contains is not None or expect.no_log_contains is not None:
        logs = backend.read_logs(lines=200)
        out.log_excerpt = logs
        ok = True
        if expect.log_contains is not None and expect.log_contains not in logs:
            ok = False
        if expect.no_log_contains is not None and expect.no_log_contains in logs:
            ok = False
        out.log_ok = ok
    return out
