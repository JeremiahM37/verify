# verify

Stop claiming code works without actually testing it.

`verify` is a tiny CLI that reads a `.verify.yaml` from your project root
and runs every check declared there: unit tests, service health, endpoint
probes, log scans, and — the killer feature — a real headless Chromium that
clicks through your UI like a user would. Exit code 0 iff everything passes,
so you can wire it into pre-commit, CI, or your AI coding assistant's "am I
done?" hook.

## Why

AI coding assistants (and humans!) routinely claim "deployed" / "fixed" /
"should work now" without actually verifying. The result is the regression
loop: ship, get a bug report, claim fix, get another bug report. `verify`
is the cheap, project-local antidote: a single command that exercises the
thing as a user does and reports back.

It's deliberately tiny — no daemon, no service, no SaaS. One Python CLI,
one YAML file per project, ~600 lines total.

## Install

```bash
pip install verify-cli           # core (pytest / systemd / http / journalctl / shell)
pip install verify-cli[ui]       # + playwright for UI flow checks
playwright install chromium      # if you want the UI check to work
```

Or from source:
```bash
git clone https://github.com/JeremiahM37/verify
cd verify && pip install -e '.[ui]'
playwright install chromium
```

## Quick start

In your project root, write `.verify.yaml`:

```yaml
checks:
  - name: tests
    type: pytest

  - name: api-healthy
    type: http
    targets:
      - { url: "http://127.0.0.1:8000/healthz", status: 200, contains: '"ok":true' }

  - name: login-works
    type: ui
    url: "http://127.0.0.1:8000/"
    steps:
      - wait: 'input[name="email"]'
      - fill: { selector: 'input[name="email"]',    text: "demo@example.test" }
      - fill: { selector: 'input[name="password"]', text: "demo-password" }
      - click: 'button[type="submit"]'
      - wait: ".dashboard"
      - expect_text: { selector: ".user-greeting", contains: "Welcome" }
```

Run:
```
$ verify
────────────────────────────────────────────────────────────
verify: 3/3 passed
────────────────────────────────────────────────────────────
  [✓] tests
  [✓] api-healthy
  [✓] login-works
────────────────────────────────────────────────────────────
PASS
```

## Check types

| Type        | What it does | Key fields |
|-------------|--------------|------------|
| `pytest`    | Runs pytest, pass if exit 0 | `run`, `cwd` |
| `shell`     | Runs an arbitrary command | `run`, `cwd`, `env`, `timeout` |
| `systemd`   | Every listed unit must be `active` | `units` |
| `journalctl`| No forbidden strings in recent unit logs | `units`, `since`, `forbid`, `ignore` |
| `http`      | One or more endpoints with status + body assertions | `targets` |
| `ui`        | Drives headless Chromium through inline steps | `url`, `steps`, `viewport`, `step_timeout` |
| `playwright`| Runs an existing Playwright Python script | `script`, `python` |

See [`examples/`](examples/) for full configs covering a FastAPI service and
a generic web app.

## The `ui` step vocabulary

Each step is either a bare string (= "wait for this selector") or a
single-key dict. Available actions:

```yaml
steps:
  - wait: "#new-item"
  - click: "#new-item"
  - fill: { selector: 'input[name="title"]', text: "hello" }
  - type: { selector: '#chatbox',          text: "slow typing" }
  - press: "Enter"
  - sleep: 0.5
  - expect_text:    { selector: ".item:first-child", contains: "hello" }
  - expect_count:   { selector: ".item", n: 1 }
  - expect_visible: ".dashboard"
  - expect_status:  { url: "http://localhost:8000/api/items", code: 200 }
  - eval: "() => sessions.length === 3"
  - screenshot: "/tmp/state.png"
  - goto: "/another-page"
```

Any uncaught JS error fires during the run fails the step (set
`allow_js_errors: true` to disable).

## CLI flags

```
verify [config]                  # path to .verify.yaml (default: ./.verify.yaml)
verify --only pytest,http        # run only these check types
verify --skip ui                 # skip these types
verify --json                    # machine-readable output
```

Exit code is `0` if all checks pass, `1` otherwise. Anything wired to
`verify` as a pre-condition (CI, pre-commit, AI assistant hook) gets clean
go/no-go semantics.

## Wiring it into a Claude Code workflow

Drop the included skill into `~/.claude/skills/verify.md` (copy from
`claude-code-skill/verify.md` in this repo) and add a line to your
project's `CLAUDE.md`:

> Before claiming "done", run `verify` from the affected project's
> directory and quote the PASS/FAIL line. If it fails, fix the issue and
> re-run — don't declare done while red.

Claude Code will read both, run `verify` after changes, and not claim
completion until the suite is green.

## Tests

```bash
pip install -e '.[test]'
pytest -q
```

## License

[MIT](LICENSE)
