"""Strict pixel coordinate contract for vision-driven clicks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from verify.backends.base import Backend, BackendCapabilities, DetectionResult
from verify.cli import main
from verify.vision import StubVisionClient, locate


class RecordingBackend(Backend):
    name = "coordinate-fixture"

    def __init__(self):
        self.clicks = []
        self.started = self.stopped = 0

    @classmethod
    def detect(cls, project_dir): return DetectionResult(0, "fixture")
    def start(self, spec): self.started += 1
    def stop(self): self.stopped += 1
    def capabilities(self): return BackendCapabilities(has_screenshot=True, has_input=True)
    def screen_size(self): return (800, 600)
    def screenshot(self): return b"deterministic screenshot bytes"
    def click(self, x, y, button="left"): self.clicks.append((x, y, button))
    def type_text(self, text): pass
    def key(self, name): pass
    def read_logs(self, lines=100): return ""


INVALID = (
    ("bool x", {"x": True, "y": 10}),
    ("string x", {"x": "10", "y": 10}),
    ("fraction x", {"x": 10.5, "y": 10}),
    ("negative fraction x", {"x": -0.5, "y": 10}),
    ("width x", {"x": 800, "y": 10}),
    ("past width x", {"x": 801, "y": 10}),
    ("bool y", {"x": 10, "y": True}),
    ("string y", {"x": 10, "y": "10"}),
    ("fraction y", {"x": 10, "y": 10.5}),
    ("negative fraction y", {"x": 10, "y": -0.5}),
    ("height y", {"x": 10, "y": 600}),
    ("past height y", {"x": 10, "y": 601}),
    ("missing x", {"y": 10}), ("missing y", {"x": 10}),
    ("null x", {"x": None, "y": 10}), ("null y", {"x": 10, "y": None}),
    ("list x", {"x": [], "y": 10}), ("mapping y", {"x": 10, "y": {}}),
    ("false x", {"x": False, "y": 10}),
    ("integral float x", {"x": 10.0, "y": 10}),
    ("negative integer y", {"x": 10, "y": -1}),
    ("infinite x", {"x": float("inf"), "y": 10}),
    ("nan y", {"x": 10, "y": float("nan")}),
)


class VisionCoordinateTests(unittest.TestCase):
    def test_locator_rejects_invalid_types_and_bounds(self):
        for label, coords in INVALID:
            with self.subTest(case=label):
                raw = __import__("json").dumps({**coords, "found": True}, allow_nan=True)
                self.assertIsNone(locate(StubVisionClient(default=raw), b"png", "target", (800, 600)))

    def test_locator_accepts_origin_last_pixel_and_one_pixel_screen(self):
        for coords, size in (((0, 0), (800, 600)), ((799, 599), (800, 600)), ((0, 0), (1, 1)), ((321, 234), (800, 600))):
            with self.subTest(coords=coords, size=size):
                raw = f'{{"x":{coords[0]},"y":{coords[1]},"found":true}}'
                self.assertEqual(locate(StubVisionClient(default=raw), b"png", "target", size), coords)

    def test_cli_invalid_coordinates_fail_without_click_and_cleanup(self):
        # Exercise each axis type family and both exclusive upper edges in text/JSON.
        cases = ((True, 10), ("10", 10), (10.5, 10), (-0.5, 10), (800, 10), (10, 600))
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "locator.yaml"
            config.write_text(
                "backend: coordinate-fixture\nsteps:\n  - name: locate target\n"
                "    actions:\n      - click:\n          locate:\n            vision: target\n"
            )
            import json
            for x, y in cases:
                for json_mode in (False, True):
                    with self.subTest(x=x, y=y, json=json_mode):
                        backend = RecordingBackend()
                        vision = StubVisionClient(default=json.dumps({"x": x, "y": y, "found": True}, allow_nan=True))
                        args = ["run", str(config)] + (["--json"] if json_mode else [])
                        with patch("verify.runner._select_backend", return_value=(backend.name, backend)), \
                             patch("verify.runner.default_vision_client", return_value=vision):
                            result = CliRunner().invoke(main, args)
                        self.assertEqual(result.exit_code, 1, result.output)
                        self.assertEqual(backend.clicks, [])
                        self.assertEqual(backend.started, 1)
                        self.assertEqual(backend.stopped, 1)
                        self.assertEqual(len(vision.calls), 1)
                        if json_mode:
                            self.assertIn('"passed": false', result.output)
                        else:
                            self.assertIn("FAIL: 0/1", result.output)

    def test_cli_valid_edge_coordinates_click_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "locator.yaml"
            config.write_text(
                "backend: coordinate-fixture\nsteps:\n  - name: locate target\n"
                "    actions:\n      - click:\n          locate:\n            vision: target\n"
            )
            for coords in ((0, 0), (799, 599), (123, 456)):
                backend = RecordingBackend()
                vision = StubVisionClient(default=f'{{"x":{coords[0]},"y":{coords[1]},"found":true}}')
                with patch("verify.runner._select_backend", return_value=(backend.name, backend)), \
                     patch("verify.runner.default_vision_client", return_value=vision):
                    result = CliRunner().invoke(main, ["run", str(config), "--json"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(backend.clicks, [(coords[0], coords[1], "left")])
                self.assertEqual(backend.stopped, 1)


if __name__ == "__main__":
    unittest.main()
