"""Tests for verify.config — YAML parsing + validation."""

from __future__ import annotations

import pytest
import yaml

from verify.config import (
    Action,
    ConfigError,
    Expect,
    Step,
    VerifyConfig,
    load,
    parse,
)


def test_minimal_parse():
    cfg = parse({"backend": "web"})
    assert cfg.backend == "web"
    assert cfg.steps == []
    assert cfg.launch.command is None


def test_full_parse_explicit_form():
    cfg = parse(
        {
            "backend": "android",
            "launch": {
                "command": "emulator -avd Pixel_6",
                "args": ["-no-window"],
                "package": "com.foo.bar",
                "env": {"FOO": "1"},
                "cwd": "/tmp",
                "wait_after": 3.0,
                "ready_when": {"log_contains": "Boot completed"},
            },
            "options": {"android": {"serial": "emulator-5554"}},
            "steps": [
                {
                    "name": "search works",
                    "actions": [
                        {"type": "click", "at": [100, 200]},
                        {"type": "type", "text": "pizza"},
                        {"type": "key", "name": "enter"},
                    ],
                    "expect": {
                        "vision": "results visible",
                        "url_contains": "/results",
                        "log_contains": "Query: pizza",
                        "no_log_contains": "FATAL",
                    },
                }
            ],
        }
    )
    assert cfg.backend == "android"
    assert cfg.launch.package == "com.foo.bar"
    assert cfg.launch.env == {"FOO": "1"}
    assert cfg.launch.wait_after == 3.0
    assert cfg.options == {"android": {"serial": "emulator-5554"}}
    s = cfg.steps[0]
    assert s.name == "search works"
    assert [a.type for a in s.actions] == ["click", "type", "key"]
    assert s.actions[0].args == {"at": [100, 200]}
    assert s.expect.vision == "results visible"
    assert s.expect.no_log_contains == "FATAL"


def test_shorthand_action_form():
    cfg = parse(
        {
            "steps": [
                {
                    "name": "shorthand",
                    "actions": [
                        {"navigate": "http://localhost"},
                        {"wait": 1.5},
                        {"type": "hello world"},
                        {"key": "enter"},
                        {"click": {"at": [10, 10]}},
                    ],
                }
            ]
        }
    )
    s = cfg.steps[0]
    assert s.actions[0].type == "navigate"
    assert s.actions[0].args == {"target": "http://localhost"}
    assert s.actions[1].args == {"seconds": 1.5}
    assert s.actions[2].args == {"text": "hello world"}
    assert s.actions[3].args == {"name": "enter"}
    assert s.actions[4].args == {"at": [10, 10]}


def test_aliases_collapse_to_canonical():
    cfg = parse(
        {
            "steps": [
                {
                    "name": "aliases",
                    "actions": [
                        {"goto": "http://foo"},
                        {"tap": {"at": [1, 2]}},
                        {"type_text": "x"},
                    ],
                }
            ]
        }
    )
    assert [a.type for a in cfg.steps[0].actions] == ["navigate", "click", "type"]


def test_unnamed_step_gets_default_name():
    cfg = parse({"steps": [{"actions": []}]})
    assert cfg.steps[0].name == "step-1"


def test_rejects_non_mapping_top_level(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- 1\n- 2\n")
    with pytest.raises(ConfigError):
        load(p)


def test_rejects_bad_action_type():
    with pytest.raises(ConfigError, match="unknown action"):
        parse({"steps": [{"name": "x", "actions": [{"BOGUS": {}}]}]})


def test_rejects_non_string_verb():
    with pytest.raises(ConfigError):
        parse({"steps": [{"name": "x", "actions": [{1: "foo"}]}]})


def test_rejects_multikey_without_explicit_type():
    with pytest.raises(ConfigError, match="single-verb shorthand"):
        parse(
            {
                "steps": [
                    {
                        "name": "x",
                        "actions": [{"click": {"at": [1, 1]}, "wait": 1}],
                    }
                ]
            }
        )


def test_rejects_scalar_shorthand_for_click():
    # click can't accept a bare scalar — it needs `at:` coordinates.
    with pytest.raises(ConfigError, match="cannot take a scalar shorthand"):
        parse({"steps": [{"name": "x", "actions": [{"click": 5}]}]})


def test_load_from_yaml_file(tmp_path):
    p = tmp_path / ".verify.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "backend": "web",
                "launch": {"url": "http://localhost"},
                "steps": [{"name": "s", "expect": {"vision": "ok"}}],
            }
        )
    )
    cfg = load(p)
    assert cfg.backend == "web"
    assert cfg.steps[0].expect.vision == "ok"


def test_empty_expect_is_none():
    cfg = parse({"steps": [{"name": "x"}]})
    assert cfg.steps[0].expect is None


def test_explicit_type_field_with_extra_args():
    cfg = parse(
        {
            "steps": [
                {
                    "name": "x",
                    "actions": [
                        {"type": "click", "at": [100, 200], "button": "right"}
                    ],
                }
            ]
        }
    )
    a = cfg.steps[0].actions[0]
    assert a.type == "click"
    assert a.args == {"at": [100, 200], "button": "right"}
