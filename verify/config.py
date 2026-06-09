"""`.verify.yaml` schema, loader, and validator.

A verify config has three pieces:

  1. `backend` — which target environment ("web", "android", "auto", ...).
  2. `launch`  — how to start the thing under test.
  3. `steps`   — ordered list of named steps. Each step has `actions`
                 (deterministic input) and `expect` (a vision-assert + any
                 deterministic checks).

The schema is intentionally small and uniform across backends: a step is just
"do this, then check that what you see matches this description". Backends
interpret `goto` / `click` / `tap` semantically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---- action types ----------------------------------------------------------


@dataclass
class Action:
    """One imperative step against the backend.

    `type` is the verb. Common verbs:
      navigate    target: url
      click       at: [x, y]   OR   locate: { vision: "...", selector: "..." }
      tap         (alias for click on touch backends)
      type        text: "hello"
      key         name: "enter"
      wait        seconds: 1.5
      screenshot  (no args; primarily useful as a diagnostic marker)
      shell       cmd: "echo hi"     # arbitrary host shell, escape hatch
    """

    type: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Expect:
    """Per-step assertions.

    vision:     natural-language description of what the screen should show.
                Checked by Claude vision model. Pass/fail + reason recorded.
    url_contains: substring that must appear in current URL (web/android).
    log_contains: substring that must appear in recent logs.
    """

    vision: str | None = None
    url_contains: str | None = None
    log_contains: str | None = None
    no_log_contains: str | None = None


@dataclass
class Step:
    name: str
    actions: list[Action] = field(default_factory=list)
    expect: Expect | None = None


@dataclass
class LaunchConfig:
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    package: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    ready_when: dict[str, Any] | None = None
    wait_after: float = 0.0


@dataclass
class VerifyConfig:
    backend: str = "auto"
    launch: LaunchConfig = field(default_factory=LaunchConfig)
    steps: list[Step] = field(default_factory=list)
    # Backend-specific options (browser size, android device id, renode platform).
    options: dict[str, Any] = field(default_factory=dict)


# ---- loading & validation --------------------------------------------------


class ConfigError(ValueError):
    """Raised when .verify.yaml is malformed."""


def load(path: Path) -> VerifyConfig:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(raw).__name__}")
    return parse(raw)


def parse(raw: dict[str, Any]) -> VerifyConfig:
    backend = raw.get("backend", "auto")
    if not isinstance(backend, str):
        raise ConfigError(f"backend must be a string, got {type(backend).__name__}")

    launch = _parse_launch(raw.get("launch") or {})
    steps = [_parse_step(s, i) for i, s in enumerate(raw.get("steps") or [])]
    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise ConfigError("options must be a mapping")

    return VerifyConfig(backend=backend, launch=launch, steps=steps, options=options)


def _parse_launch(raw: dict[str, Any]) -> LaunchConfig:
    if not isinstance(raw, dict):
        raise ConfigError("launch must be a mapping")
    args = raw.get("args") or []
    if not isinstance(args, list):
        raise ConfigError("launch.args must be a list")
    env = raw.get("env") or {}
    if not isinstance(env, dict):
        raise ConfigError("launch.env must be a mapping")
    ready = raw.get("ready_when")
    if ready is not None and not isinstance(ready, dict):
        raise ConfigError("launch.ready_when must be a mapping or null")
    return LaunchConfig(
        command=raw.get("command"),
        args=list(args),
        url=raw.get("url"),
        package=raw.get("package"),
        env=dict(env),
        cwd=raw.get("cwd"),
        ready_when=ready,
        wait_after=float(raw.get("wait_after", 0.0)),
    )


def _parse_step(raw: Any, idx: int) -> Step:
    if not isinstance(raw, dict):
        raise ConfigError(f"step #{idx}: must be a mapping")
    name = raw.get("name") or f"step-{idx + 1}"
    actions_raw = raw.get("actions") or []
    if not isinstance(actions_raw, list):
        raise ConfigError(f"step {name!r}: actions must be a list")
    actions = [_parse_action(a, name, i) for i, a in enumerate(actions_raw)]
    expect = _parse_expect(raw.get("expect"))
    return Step(name=name, actions=actions, expect=expect)


_KNOWN_ACTIONS = {
    "navigate",
    "goto",  # alias
    "click",
    "tap",  # alias
    "type",
    "type_text",  # alias
    "key",
    "wait",
    "screenshot",
    "shell",
}

# For shorthand `{verb: scalar}`, which arg name the scalar fills in.
_SCALAR_KEY = {
    "wait": "seconds",
    "type": "text",
    "type_text": "text",
    "key": "name",
    "navigate": "target",
    "goto": "target",
    "shell": "cmd",
}


def _parse_action(raw: Any, step_name: str, idx: int) -> Action:
    if not isinstance(raw, dict):
        raise ConfigError(f"step {step_name!r} action #{idx}: must be a mapping")
    # Two forms:
    #   1. {"type": "click", "at": [10, 20]}              -- explicit (multi-key)
    #   2. {"click": {"at": [10, 20]}} or {"wait": 1}     -- shorthand (single-key)
    # Disambiguate purely on dict size: single-key is always shorthand. This
    # lets `{"type": "hi"}` mean the `type` action with text "hi", not an
    # explicit "hi" verb.
    if len(raw) == 1:
        ((atype, value),) = raw.items()
        if not isinstance(atype, str):
            raise ConfigError(
                f"step {step_name!r} action #{idx}: action verb must be a string"
            )
        if value is None:
            args: dict[str, Any] = {}
        elif isinstance(value, dict):
            args = dict(value)
        else:
            scalar_key = _SCALAR_KEY.get(atype)
            if scalar_key is None:
                raise ConfigError(
                    f"step {step_name!r} action #{idx}: {atype!r} cannot take a scalar shorthand"
                )
            args = {scalar_key: value}
    elif "type" in raw and isinstance(raw["type"], str):
        atype = raw["type"]
        args = {k: v for k, v in raw.items() if k != "type"}
    else:
        raise ConfigError(
            f"step {step_name!r} action #{idx}: need either 'type:' key or single-verb shorthand"
        )
    if atype not in _KNOWN_ACTIONS:
        raise ConfigError(
            f"step {step_name!r} action #{idx}: unknown action {atype!r}. "
            f"Known: {sorted(_KNOWN_ACTIONS)}"
        )
    return Action(type=_canonical_action(atype), args=args)


def _canonical_action(name: str) -> str:
    return {
        "goto": "navigate",
        "tap": "click",
        "type_text": "type",
    }.get(name, name)


def _parse_expect(raw: Any) -> Expect | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("expect must be a mapping or null")
    return Expect(
        vision=raw.get("vision"),
        url_contains=raw.get("url_contains"),
        log_contains=raw.get("log_contains"),
        no_log_contains=raw.get("no_log_contains"),
    )
