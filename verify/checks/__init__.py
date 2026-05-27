"""Built-in check types. Each module exposes a `run(check_cfg) -> dict` function
returning a CheckResult shape:

    { "name": str, "ok": bool, "detail": str|None, "items": list|None }

`ok` is the overall pass/fail. `detail` is free-form text shown on failure.
`items` is an optional list of sub-results for checks that probe multiple
targets (e.g. multiple HTTP endpoints) so the report can pinpoint which one
failed without dumping everything.
"""
from . import http, journalctl, playwright, pytest, shell, systemd, ui_cmd

REGISTRY = {
    "http":       http.run,
    "journalctl": journalctl.run,
    "playwright": playwright.run,
    "pytest":     pytest.run,
    "shell":      shell.run,
    "systemd":    systemd.run,
    "ui":         ui_cmd.run,
}
