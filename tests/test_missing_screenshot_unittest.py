"""Offline regression for required vision evidence (standard-library runner)."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from verify.backends.base import Backend, BackendCapabilities, DetectionResult
from verify.config import load, parse
from verify.runner import RunReport, run


class IsolatedBackend(Backend):
    name = "isolated-vision-regression"

    def __init__(self, screenshot=b"image", error=None):
        self.image = screenshot
        self.capture_error = error
        self.stopped = False

    @classmethod
    def detect(cls, project_dir: Path):
        return DetectionResult(0, "test only")

    def start(self, spec):
        pass

    def stop(self):
        self.stopped = True

    def capabilities(self):
        return BackendCapabilities(has_screenshot=True, has_logs=True)

    def screen_size(self):
        return (10, 10)

    def screenshot(self):
        if self.capture_error:
            raise self.capture_error
        return self.image

    def click(self, x, y, button="left"):
        pass

    def type_text(self, text):
        pass

    def key(self, name):
        pass

    def read_logs(self, lines=100):
        return "ready"

    def current_url(self):
        return "https://example.test/ready"


class DeterministicVision:
    def __init__(self, passed):
        self.passed = passed
        self.calls = []

    def ask(self, image_png, prompt, *, max_tokens=400):
        self.calls.append((image_png, prompt))
        return '{"pass": %s, "reason": "fixed test result"}' % (
            "true" if self.passed else "false"
        )


def config(expect):
    return parse({"backend": "isolated-vision-regression", "steps": [
        {"name": "evidence", "expect": expect}
    ]})


class MissingScreenshotTests(unittest.TestCase):
    def test_expectation_values_must_be_strings_or_null_before_execution(self):
        from click.testing import CliRunner
        from verify.cli import main
        from verify.config import ConfigError

        invalid_values = ("false", "0", "1", "0.0", "[]", "{}")
        fields = ("vision", "url_contains", "log_contains", "no_log_contains")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in fields:
                for yaml_value in invalid_values:
                    with self.subTest(field=field, yaml_value=yaml_value):
                        path = root / "invalid.yaml"
                        path.write_text(
                            "backend: isolated-vision-regression\n"
                            "steps:\n  - name: invalid expectation\n"
                            f"    expect:\n      {field}: {yaml_value}\n"
                        )
                        with self.assertRaisesRegex(
                            ConfigError, rf"expect\.{field} must be a string or null"
                        ):
                            load(path)

                        for args in (["run", str(path)], ["run", str(path), "--json"]):
                            with patch("verify.runner._select_backend") as select_backend, \
                                    patch("verify.runner.default_vision_client") as vision_client:
                                result = CliRunner().invoke(main, args)
                            self.assertNotEqual(result.exit_code, 0, result.output)
                            self.assertIsInstance(result.exception, ConfigError)
                            self.assertIn(f"expect.{field}", str(result.exception))
                            select_backend.assert_not_called()
                            vision_client.assert_not_called()

    def test_expectation_string_and_null_values_remain_valid(self):
        for value in (None, "", "  ", "no"):
            with self.subTest(value=value):
                parsed = parse({"steps": [{"name": "string control", "expect": {
                    "vision": value, "url_contains": value,
                    "log_contains": value, "no_log_contains": value,
                }}]})
                self.assertEqual(parsed.steps[0].expect.vision, value)

    def test_expectation_unknown_keys_fail_through_loader_and_cli_before_execution(self):
        from click.testing import CliRunner
        from verify.cli import main
        from verify.config import ConfigError

        cases = {
            "typo-only": ["log_contians: REQUIRED"],
            "known-plus-unknown": [
                "log_contains: ready", "url_contians: REQUIRED"
            ],
            "non-string-key": ["1: unexpected"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, expect_fields in cases.items():
                with self.subTest(case=name):
                    path = root / f"{name}.yaml"
                    path.write_text(
                        "backend: isolated-vision-regression\n"
                        "steps:\n  - name: key validation\n    expect:\n"
                        + "\n".join(f"      {field}" for field in expect_fields)
                        + "\n"
                    )
                    for args in (["run", str(path)], ["run", str(path), "--json"]):
                        with patch("verify.runner._select_backend") as select_backend, \
                                patch("verify.runner.default_vision_client") as vision_client:
                            result = CliRunner().invoke(main, args)
                        self.assertNotEqual(result.exit_code, 0, result.output)
                        self.assertIsInstance(result.exception, ConfigError)
                        self.assertIn("unknown keys", str(result.exception))
                        self.assertIn("log_contains", str(result.exception))
                        select_backend.assert_not_called()
                        vision_client.assert_not_called()

    def test_supported_expectation_keys_and_action_only_steps_remain_valid(self):
        expectation = {
            "vision": "screen is ready",
            "url_contains": "/ready",
            "log_contains": "ready",
            "no_log_contains": "ERROR",
        }
        backend = IsolatedBackend()
        vision = DeterministicVision(True)
        report = run(config(expectation), Path.cwd(), backend=backend, vision=vision)
        self.assertTrue(report.passed, report.summary())
        self.assertEqual(len(vision.calls), 1)
        self.assertTrue(report.steps[0].expect.url_ok)
        self.assertTrue(report.steps[0].expect.log_ok)

        failing_expectations = (
            {"vision": "screen is ready"},
            {"url_contains": "/missing"},
            {"log_contains": "missing"},
            {"no_log_contains": "ready"},
        )
        for failed_expectation in failing_expectations:
            with self.subTest(failed_expectation=failed_expectation):
                failed = run(
                    config(failed_expectation),
                    Path.cwd(),
                    backend=IsolatedBackend(),
                    vision=DeterministicVision(False),
                )
                self.assertFalse(failed.passed, failed.summary())

        action_only = run(
            parse({"backend": "isolated-vision-regression", "steps": [
                {"name": "action only", "actions": [{"wait": 0}]}
            ]}),
            Path.cwd(),
            backend=IsolatedBackend(),
        )
        self.assertTrue(action_only.passed, action_only.summary())

    def test_omitted_null_and_empty_expectations_remain_valid(self):
        for step in (
            {"name": "omitted"},
            {"name": "null", "expect": None},
            {"name": "empty", "expect": {}},
        ):
            with self.subTest(step=step):
                report = run(
                    parse({"backend": "isolated-vision-regression", "steps": [step]}),
                    Path.cwd(),
                    backend=IsolatedBackend(),
                )
                self.assertTrue(report.passed, report.summary())

    def test_empty_reports_cannot_pass(self):
        report = RunReport(backend="fixture")
        self.assertFalse(report.passed)
        self.assertTrue(report.summary().startswith("FAIL: 0/0"))

    def test_empty_yaml_configs_fail_before_backend_or_vision_initialization(self):
        from click.testing import CliRunner
        from verify.cli import main

        cases = {
            "legacy checks": (
                "backend: auto\nchecks:\n  - name: stale-check\n"
                "    expect:\n      vision: screen is ready\n"
            ),
            "absent steps": "backend: auto\n",
            "explicit empty steps": "backend: auto\nsteps: []\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, yaml in cases.items():
                with self.subTest(case=name):
                    path = root / f"{name.replace(' ', '-')}.yaml"
                    path.write_text(yaml)
                    config = load(path)
                    self.assertEqual(config.steps, [])

                    with patch("verify.runner._select_backend") as select_backend, \
                            patch("verify.runner.default_vision_client") as vision_client:
                        report = run(config, root)
                    self.assertFalse(report.passed)
                    self.assertIn("no runnable steps", report.setup_error)
                    self.assertIn("Legacy `checks:`", report.setup_error)
                    select_backend.assert_not_called()
                    vision_client.assert_not_called()

                    for args in (["run", str(path)], ["run", str(path), "--json"]):
                        with patch("verify.runner._select_backend") as select_backend, \
                                patch("verify.runner.default_vision_client") as vision_client:
                            result = CliRunner().invoke(main, args)
                        self.assertEqual(result.exit_code, 1, result.output)
                        self.assertIn("no runnable steps", result.output)
                        select_backend.assert_not_called()
                        vision_client.assert_not_called()
                        if "--json" in args:
                            payload = json.loads(result.output)
                            self.assertFalse(payload["passed"])
                            self.assertIn("Legacy `checks:`", payload["setup_error"])

    def test_backend_selection_and_options_paths_with_nonempty_steps(self):
        from verify.backends.base import DetectionResult
        from verify.backends.registry import DetectionMatch, _REGISTRY

        unknown = run(
            parse({"backend": "repair-unknown-backend", "steps": [{"name": "selection"}]}),
            Path.cwd(),
        )
        self.assertIn("backend selection failed", unknown.setup_error)

        class UnavailableBackend(IsolatedBackend):
            name = "repair-unavailable-backend"

            @classmethod
            def is_available(cls):
                return False, "fixture dependency missing"

        with patch.dict(_REGISTRY, {UnavailableBackend.name: UnavailableBackend}):
            unavailable = run(
                parse({"backend": UnavailableBackend.name, "steps": [{"name": "selection"}]}),
                Path.cwd(),
            )
        self.assertIn("fixture dependency missing", unavailable.setup_error)

        with patch("verify.runner.detect_all", return_value=[
            DetectionMatch(
                UnavailableBackend.name,
                UnavailableBackend,
                DetectionResult(50, "fixture match"),
            )
        ]):
            auto_unavailable = run(
                parse({"backend": "auto", "steps": [{"name": "selection"}]}),
                Path.cwd(),
            )
        self.assertIn("auto-detect found no available backend", auto_unavailable.setup_error)

        class OptionsBackend(IsolatedBackend):
            name = "repair-options-backend"
            received_options = None

            def __init__(self, **options):
                super().__init__()
                type(self).received_options = options

        with patch.dict(_REGISTRY, {OptionsBackend.name: OptionsBackend}):
            options_config = parse({
                "backend": OptionsBackend.name,
                "options": {OptionsBackend.name: {"fixture_flag": "preserved"}},
                "steps": [{"name": "constructor options"}],
            })
            options_report = run(options_config, Path.cwd())
        self.assertTrue(options_report.passed, options_report.summary())
        self.assertEqual(
            OptionsBackend.received_options, {"fixture_flag": "preserved"}
        )

    def test_capture_exception_fails_required_vision_and_cleans_up(self):
        backend = IsolatedBackend(error=RuntimeError("capture device unavailable"))
        vision = DeterministicVision(True)
        report = run(config({"vision": "screen is ready"}), Path.cwd(),
                     backend=backend, vision=vision)
        self.assertFalse(report.passed, report.summary())
        self.assertIn("capture device unavailable", report.steps[0].expect.vision_error)
        self.assertEqual(vision.calls, [])
        self.assertTrue(backend.stopped)

    def test_none_capture_fails_required_vision(self):
        report = run(config({"vision": "screen is ready"}), Path.cwd(),
                     backend=IsolatedBackend(screenshot=None),
                     vision=DeterministicVision(True))
        self.assertFalse(report.passed)
        self.assertIn("returned no screenshot", report.steps[0].expect.vision_error)

    def test_successful_capture_preserves_positive_and_negative_vision(self):
        for expected, passed in ((True, True), (False, False)):
            with self.subTest(passed=passed):
                backend = IsolatedBackend()
                vision = DeterministicVision(passed)
                report = run(config({"vision": "screen is ready"}), Path.cwd(),
                             backend=backend, vision=vision)
                self.assertEqual(report.passed, expected)
                self.assertEqual(len(vision.calls), 1)
                self.assertTrue(backend.stopped)

    def test_passing_log_does_not_mask_missing_requested_vision(self):
        backend = IsolatedBackend(error=RuntimeError("capture broke"))
        report = run(config({"vision": "screen is ready", "log_contains": "ready"}),
                     Path.cwd(), backend=backend, vision=DeterministicVision(True))
        self.assertFalse(report.passed)
        self.assertTrue(report.steps[0].expect.log_ok)
        self.assertTrue(report.steps[0].expect.vision_error)

    def test_incidental_screenshot_failure_does_not_break_nonvision_expectation(self):
        report = run(config({"log_contains": "ready"}), Path.cwd(),
                     backend=IsolatedBackend(error=RuntimeError("diagnostic only")))
        self.assertTrue(report.passed, report.summary())
        self.assertTrue(report.steps[0].expect.log_ok)

    def test_cli_text_and_json_show_failure_and_nonzero_status(self):
        from click.testing import CliRunner
        from verify.cli import main

        backend = IsolatedBackend(error=RuntimeError("capture device unavailable"))
        report = run(config({"vision": "screen is ready"}), Path.cwd(),
                     backend=backend, vision=DeterministicVision(True))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verify.yaml"
            path.write_text(
                "backend: isolated-vision-regression\n"
                "steps:\n  - name: evidence\n    expect:\n"
                "      vision: screen is ready\n"
            )
            with patch("verify.runner.run", return_value=report):
                text_result = CliRunner().invoke(main, ["run", str(path)])
                json_result = CliRunner().invoke(main, ["run", str(path), "--json"])
        self.assertEqual(text_result.exit_code, 1, text_result.output)
        self.assertIn("capture device unavailable", text_result.output)
        self.assertEqual(json_result.exit_code, 1, json_result.output)
        self.assertFalse(json.loads(json_result.output)["passed"])
        self.assertIn("capture device unavailable",
                      json.loads(json_result.output)["steps"][0]["expect"]["vision_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
