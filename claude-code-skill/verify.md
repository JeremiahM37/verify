---
name: verify
description: End-to-end verify a project is actually in a working state — runs its tests, checks services and endpoints are healthy, and exercises UI flows in a real headless browser. Invoke after any code change before claiming "done."
---

# verify

The most useful command in the toolbox after any change. Catches the
class of bug that's hardest to spot from a diff: deployed-but-broken,
endpoint-returns-500, UI-button-doesn't-respond, log-full-of-tracebacks.

## How to use

From the project root (or wherever `.verify.yaml` lives):

```
verify
```

Reads `.verify.yaml`, runs every defined check, prints `verify: N/M passed`
plus per-check ✓/✗ with failure details. Exits 0 if all pass, 1 otherwise.

If the project doesn't have a `.verify.yaml` yet, write one (see examples
below) before running. Without it there's nothing to verify.

## When to invoke

After:
- Edit/Write on any file the user is going to ship or run
- Restarting a service
- Deploying or pushing a change
- Anything else where the next thing out of your mouth would be "deployed" /
  "done" / "should work now"

Quote the relevant part of the output back to the user — at minimum the
final `PASS` / `FAIL` line. If it fails, **don't claim done** — fix the
underlying issue and re-run.

## What goes in .verify.yaml

```yaml
checks:
  - name: tests
    type: pytest                # runs `pytest -q` by default

  - name: service-active
    type: systemd
    units: [my-api]

  - name: no-recent-errors
    type: journalctl
    units: [my-api]
    since: "1 min ago"
    forbid: [ERROR, Traceback]   # additional `ignore:` substrings supported

  - name: endpoints-healthy
    type: http
    targets:
      - { url: "http://127.0.0.1:8000/healthz", status: 200, contains: '"ok":true' }

  - name: ui-flow
    type: ui                     # ad-hoc — drives a real headless Chromium
    url: "http://127.0.0.1:8000/"
    viewport: { width: 414, height: 896 }
    steps:
      - wait: "#login"
      - fill: { selector: "#email", text: "demo@example.test" }
      - fill: { selector: "#password", text: "x" }
      - click: 'button[type="submit"]'
      - wait: ".dashboard"
      - expect_text: { selector: ".user", contains: "demo@example.test" }

  - name: e2e-script
    type: playwright             # for flows too involved for inline steps
    script: "tests/e2e_smoke.py"

  - name: lint
    type: shell                  # escape hatch — runs anything
    run: "ruff check ."
```

## ad-hoc UI step vocabulary

These are the keys recognized in a `ui` step list. Each step is either a
bare string (= wait-for-selector shorthand) or a one-key dict:

| Step                                                | Effect |
|-----------------------------------------------------|--------|
| `wait: SELECTOR`                                    | Wait for element to appear |
| `click: SELECTOR`                                   | Click it (waits first) |
| `fill: { selector: S, text: T }`                    | Set the value of an input |
| `type: { selector: S, text: T }`                    | Type into it character-by-character |
| `press: KEY`                                        | Press a key globally ("Enter", "Escape", ...) |
| `sleep: SECONDS`                                    | Pause |
| `expect_text: { selector: S, contains: T }`         | Element text must include T |
| `expect_text: { selector: S, equals: T }`           | Element text must equal T exactly |
| `expect_count: { selector: S, n: N }`               | Exactly N elements match |
| `expect_visible: SELECTOR`                          | Selector is visible |
| `expect_status: { url: U, code: 200 }`              | HTTP fetch from browser context |
| `eval: "JS expression"`                             | Must evaluate truthy |
| `screenshot: "/tmp/foo.png"`                        | Saves PNG; useful in failing steps |
| `goto: "URL"`                                       | Navigate |

Any uncaught JS error on the page fails the step (override with
`allow_js_errors: true` at check level if needed).

## Tips

- Run `verify --only ui` to focus on UI checks while iterating
- Run `verify --skip pytest` to skip slow checks during quick loops
- `verify --json` for machine-readable output
- Drop a screenshot step right before a tricky expectation; the PNG
  shows up in `/tmp/` whether the step passes or fails
- Set `viewport` to phone dimensions for mobile-mode testing

## Project examples

- `/home/admin/projects/verify/examples/fastapi-service.verify.yaml`
- `/home/admin/projects/verify/examples/web-app.verify.yaml`
- `/home/admin/homelab-api/.verify.yaml` (real-world)
