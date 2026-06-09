"""Tests for verify.detect and per-backend detection rules."""

from __future__ import annotations

import pathlib

import pytest

from verify.backends.android import AndroidBackend
from verify.backends.generic import GenericBackend
from verify.backends.linux_desktop import LinuxDesktopBackend
from verify.backends.registry import detect_all, best_match
from verify.backends.renode import RenodeBackend
from verify.backends.web import WebBackend
from verify.detect import file_contains, glob_any, has_file, rglob_any


def test_helpers_has_and_glob(tmp_path: pathlib.Path):
    (tmp_path / "foo.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bar.gradle").write_text("x")
    assert has_file(tmp_path, "foo.txt")
    assert not has_file(tmp_path, "missing.txt")
    assert glob_any(tmp_path, "*.txt")
    assert glob_any(tmp_path, "sub/*.gradle")
    assert not glob_any(tmp_path, "*.bin")
    assert rglob_any(tmp_path, "**/*.gradle") == 1
    assert rglob_any(tmp_path, "**/*.does-not-exist") == 0


def test_file_contains(tmp_path: pathlib.Path):
    p = tmp_path / "x.txt"
    p.write_text("the quick brown fox")
    assert file_contains(p, "quick")
    assert file_contains(p, "missing", "brown")
    assert not file_contains(p, "missing")
    assert not file_contains(tmp_path / "absent.txt", "anything")


def test_web_detects_react_package_json(tmp_path: pathlib.Path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    r = WebBackend.detect(tmp_path)
    assert r.confidence == 80


def test_web_detects_index_html(tmp_path: pathlib.Path):
    (tmp_path / "index.html").write_text("<html/>")
    assert WebBackend.detect(tmp_path).confidence == 50


def test_web_low_confidence_on_plain_package_json(tmp_path: pathlib.Path):
    (tmp_path / "package.json").write_text('{"name": "x"}')
    assert WebBackend.detect(tmp_path).confidence == 40


def test_android_detects_manifest(tmp_path: pathlib.Path):
    (tmp_path / "AndroidManifest.xml").write_text("<manifest/>")
    assert AndroidBackend.detect(tmp_path).confidence == 95


def test_android_detects_nested_manifest(tmp_path: pathlib.Path):
    nested = tmp_path / "app" / "src" / "main"
    nested.mkdir(parents=True)
    (nested / "AndroidManifest.xml").write_text("<manifest/>")
    assert AndroidBackend.detect(tmp_path).confidence == 90


def test_android_detects_gradle_plugin(tmp_path: pathlib.Path):
    (tmp_path / "build.gradle").write_text(
        "plugins { id 'com.android.application' }"
    )
    assert AndroidBackend.detect(tmp_path).confidence == 85


def test_renode_detects_resc_script(tmp_path: pathlib.Path):
    (tmp_path / "blink.resc").write_text("# resc")
    assert RenodeBackend.detect(tmp_path).confidence == 95


def test_renode_detects_platformio_mcu(tmp_path: pathlib.Path):
    (tmp_path / "platformio.ini").write_text(
        "[env:nucleo]\nboard = nucleo_l476rg\nframework = stm32cube\n"
    )
    assert RenodeBackend.detect(tmp_path).confidence == 80


def test_renode_low_confidence_on_generic_platformio(tmp_path: pathlib.Path):
    (tmp_path / "platformio.ini").write_text("[env:foo]\nframework = arduino\n")
    assert RenodeBackend.detect(tmp_path).confidence == 50


def test_linux_desktop_detects_tauri(tmp_path: pathlib.Path):
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "src-tauri" / "tauri.conf.json").write_text("{}")
    assert LinuxDesktopBackend.detect(tmp_path).confidence == 75


def test_linux_desktop_detects_qt_cmake(tmp_path: pathlib.Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "find_package(Qt6 COMPONENTS Widgets)\n"
    )
    assert LinuxDesktopBackend.detect(tmp_path).confidence == 70


def test_generic_always_lowest(tmp_path: pathlib.Path):
    assert GenericBackend.detect(tmp_path).confidence == 1


def test_best_match_picks_highest_available(tmp_path: pathlib.Path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    # The web backend has Playwright installed in this venv, so it should win.
    m = best_match(tmp_path, require_available=False)
    assert m is not None
    assert m.name == "web"


def test_best_match_falls_back_when_higher_unavailable(tmp_path: pathlib.Path):
    # Renode .resc beats generic by confidence, but renode is unavailable here.
    (tmp_path / "demo.resc").write_text("# blink")
    m = best_match(tmp_path, require_available=True)
    assert m is not None
    # generic should win because renode isn't installed.
    assert m.name == "generic"


def test_detect_all_sorted_desc(tmp_path: pathlib.Path):
    (tmp_path / "AndroidManifest.xml").write_text("<manifest/>")
    matches = detect_all(tmp_path)
    confidences = [m.result.confidence for m in matches]
    assert confidences == sorted(confidences, reverse=True)
