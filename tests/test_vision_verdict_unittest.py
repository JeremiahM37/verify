"""Strict JSON boolean verdict regressions, runnable without pytest."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from verify.backends.base import Backend, BackendCapabilities, DetectionResult
from verify.cli import main
from verify.vision import StubVisionClient, assert_vision, locate

BAD = ('"false"', '"true"', '1', '0', '[true]', 'null')


class RecordingBackend(Backend):
    name = 'verdict-fixture'
    def __init__(self): self.clicks = []
    @classmethod
    def detect(cls, project_dir): return DetectionResult(0, 'fixture')
    def start(self, spec): pass
    def stop(self): pass
    def capabilities(self): return BackendCapabilities(has_screenshot=True, has_input=True)
    def screen_size(self): return (100, 80)
    def screenshot(self): return b'deterministic screenshot bytes'
    def click(self, x, y, button='left'): self.clicks.append((x, y, button))
    def type_text(self, text): pass
    def key(self, name): pass
    def read_logs(self, lines=100): return ''


class VerdictTests(unittest.TestCase):
    def test_assertion_rejects_malformed_and_missing_verdicts(self):
        for value in BAD:
            with self.subTest(value=value):
                raw = '{"pass":' + value + ',"reason":"model reason"}'
                result = assert_vision(StubVisionClient(default=raw), b'png', 'ready')
                self.assertFalse(result.passed)
                self.assertIn("must be a JSON boolean", result.reason)
                self.assertEqual(result.raw, raw)
        result = assert_vision(StubVisionClient(default='{"reason":"missing"}'), b'png', 'ready')
        self.assertFalse(result.passed)
        self.assertIn("must be a JSON boolean", result.reason)

    def test_assertion_boolean_controls_and_json_extraction_formats(self):
        for raw in (
            '{"pass":true,"reason":"yes"}',
            '```json\n{"pass":true,"reason":"yes"}\n```',
            'Model result: {"pass":true,"reason":"yes"} done',
        ):
            with self.subTest(raw=raw):
                result = assert_vision(StubVisionClient(default=raw), b'png', 'ready')
                self.assertTrue(result.passed)
        failed = assert_vision(
            StubVisionClient(default='{"pass":false,"reason":"not ready"}'), b'png', 'ready'
        )
        self.assertFalse(failed.passed)
        self.assertEqual(failed.reason, 'not ready')

    def test_locator_rejects_malformed_and_missing_found(self):
        for value in BAD:
            with self.subTest(value=value):
                raw = '{"x":17,"y":23,"found":' + value + '}'
                self.assertIsNone(locate(StubVisionClient(default=raw), b'png', 'button', (100, 80)))
        self.assertIsNone(locate(StubVisionClient(default='{"x":17,"y":23}'), b'png', 'button', (100, 80)))

    def test_locator_boolean_controls_and_extraction_formats(self):
        for raw in (
            '{"x":17,"y":23,"found":true}',
            '```json\n{"x":17,"y":23,"found":true}\n```',
            'Found it: {"x":17,"y":23,"found":true}',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(locate(StubVisionClient(default=raw), b'png', 'button', (100, 80)), (17, 23))
        self.assertIsNone(locate(StubVisionClient(default='{"x":0,"y":0,"found":false}'), b'png', 'button', (100, 80)))

    def test_cli_assertion_negative_and_malformed_results(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / 'assert.yaml'
            config.write_text('backend: verdict-fixture\nsteps:\n  - name: visible state\n    expect:\n      vision: application is active\n')
            for value, expected_exit in (("false", 1), ("\"false\"", 1), ("true", 0)):
                backend = RecordingBackend()
                vision = StubVisionClient(default='{"pass":' + value + ',"reason":"state"}')
                with patch('verify.runner._select_backend', return_value=('verdict-fixture', backend)), patch('verify.runner.default_vision_client', return_value=vision):
                    result = CliRunner().invoke(main, ['run', str(config), '--json'])
                self.assertEqual(result.exit_code, expected_exit, result.output)
                self.assertEqual(len(vision.calls), 1)

    def test_cli_locator_malformed_fails_without_click_and_valid_clicks_once(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / 'click.yaml'
            config.write_text('backend: verdict-fixture\nsteps:\n  - name: locate action\n    actions:\n      - click:\n          locate:\n            vision: submit button\n')
            for verdict, expected_exit, expected_clicks in (( '"false"', 1, []), ('true', 0, [(17, 23, 'left')])):
                backend = RecordingBackend()
                vision = StubVisionClient(default='{"x":17,"y":23,"found":' + verdict + '}')
                with patch('verify.runner._select_backend', return_value=('verdict-fixture', backend)), patch('verify.runner.default_vision_client', return_value=vision):
                    result = CliRunner().invoke(main, ['run', str(config), '--json'])
                self.assertEqual(result.exit_code, expected_exit, result.output)
                self.assertEqual(backend.clicks, expected_clicks)
                self.assertEqual(len(vision.calls), 1)
                self.assertEqual('"passed": true' in result.output, expected_exit == 0)


if __name__ == '__main__':
    unittest.main()
