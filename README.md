# verify

Stop claiming code works without actually testing it.

`verify` reads a `.verify.yaml` from your project root, picks the right backend
for what you're building, drives the app like a user would, and uses a vision
model to read the screen. Exit code 0 iff every step passes. Web app, Android
app, Linux desktop app, STM32 firmware, or anything else — same CLI, same YAML.

## Why

Backend tests pass. Logs look clean. The binary builds. The actual rendered UI
shows a stack trace, a red error banner, or an input field that silently
swallowed the keystrokes. `verify` catches that class of bug: it screenshots,
sends the image + a natural-language expectation to a vision model, and reports
pass/fail with the model's own reasoning quoted back.

## Install

```bash
pip install git+https://github.com/JeremiahM37/verify                          # core
pip install "verify-cli[web] @ git+https://github.com/JeremiahM37/verify"      # + Playwright
pip install "verify-cli[android] @ git+https://github.com/JeremiahM37/verify"  # + adbutils
pip install "verify-cli[desktop] @ git+https://github.com/JeremiahM37/verify"  # + mss
pip install "verify-cli[mcp] @ git+https://github.com/JeremiahM37/verify"      # + MCP server
pip install "verify-cli[all] @ git+https://github.com/JeremiahM37/verify"      # everything
```

Vision provider (pick one):

```bash
export ANTHROPIC_API_KEY=sk-ant-...                       # Claude
# or
ollama pull gemma4:e4b                                     # local, free
export VERIFY_OLLAMA_HOST=http://127.0.0.1:11434
```

`verify backends` lists what's installed and what each backend needs.

## Backends

| Backend | Drives | Host needs |
|---|---|---|
| `web` | Playwright + headless Chromium | `playwright install chromium` |
| `android` | `adb` (real device, emulator, or docker-android image) | Android platform-tools |
| `linux_desktop` | Xvfb + xdotool + xwd | `apt install xvfb xdotool x11-apps imagemagick` |
| `renode` | Renode Monitor + UART + framebuffer | [renode.io](https://renode.io/#downloads) |
| `generic` | `mss` host capture + native input | — (fallback) |

## Quickstart

```bash
cd my-project
verify init                # writes .verify.yaml for the detected backend
verify run                 # executes it
```

A typical config:

```yaml
backend: web

launch:
  command: npm run dev
  url: http://localhost:3000
  wait_after: 2

steps:
  - name: home loads cleanly
    actions:
      - navigate: http://localhost:3000
    expect:
      vision: "home page rendered; no error banner, stack trace, or 404"

  - name: sign in works
    actions:
      - click: { locate: { vision: "the email input field" } }
      - type:  test@example.com
      - key:   tab
      - type:  hunter2
      - click: { locate: { vision: "the log in button" } }
      - wait:  2
    expect:
      vision: "user is on the dashboard; no error toast or modal visible"
      url_contains: /dashboard
```

## The `launch` block

How to start the thing under test. All keys are optional; which ones matter
depends on the backend.

| Key | Meaning |
|---|---|
| `command` | Process to start: dev server (`web`), emulator boot (`android`), `.resc` script (`renode`), the binary itself (`linux_desktop` / `generic`) |
| `args` | Extra argv appended to `command` |
| `url` | Page to open after launch (`web`) |
| `package` | App id to start on the device (`android`) |
| `env` | Extra environment variables for `command` |
| `cwd` | Working directory for `command` |
| `wait_after` | Unconditional sleep (seconds) after launch |
| `ready_when` | Readiness gate: `{ log_contains: "Server ready", timeout: 60 }` — polls the target's logs until the substring appears; the run fails with a setup error if it never does (`timeout` defaults to 60s). Prefer this over guessing a `wait_after`. |

## Backend `options`

Backend-specific tuning lives under `options.<backend>` and is passed to the
backend's constructor.

| Backend | Key | Default | Meaning |
|---|---|---|---|
| `web` | `headless` | `true` | Run Chromium headless |
| `web` | `viewport` | `[1280, 800]` | Browser viewport size |
| `android` | `serial` | first device | `adb -s` device serial |
| `android` | `adb` | `adb` | Path to the adb binary |
| `android` | `docker_image` | — | Boot the emulator in a labeled Docker sandbox (e.g. `budtmo/docker-android:emulator_14.0`) |
| `android` | `docker_adb_port` | `5555` | adb port published from the container |
| `android` | `docker_ready_log` | `emulator: INFO: boot completed` | Container log line that marks boot completion |
| `android` | `docker_boot_timeout` | `300` | Seconds to wait for that log line |
| `linux_desktop` | `display` | `:99` | Xvfb display to create |
| `linux_desktop` | `screen_size` | `[1280, 800]` | Xvfb screen size |
| `renode` | `monitor_port` | `1234` | Renode Monitor telnet port |
| `renode` | `frame_analyzer` | — | Machine path of the LCD analyzer (e.g. `sysbus.lcd`); required for screenshots/vision on embedded targets |
| `generic` | — | — | No options |

## Action vocabulary

Same across every backend.

| Action | Args |
|---|---|
| `navigate` | `target: <url>` |
| `click` (alias `tap`) | `at: [x, y]` OR `selector: <css>` OR `locate: { vision: "..." }` |
| `type` (alias `type_text`) | `text: "..."` |
| `key` | `name: enter / tab / back / ...` |
| `wait` | `seconds: 1.5` |
| `shell` | `cmd: "..."`, `timeout: 120` (escape hatch; runs on host) |

Shorthand: any single-arg action can be written `{verb: value}` —
`{wait: 1}`, `{type: "hello"}`, `{key: enter}`.

> **Trust model**: `.verify.yaml` is executable configuration, like a Makefile
> or an npm script. The `shell` action runs arbitrary commands on your host,
> and `launch.command` starts whatever it names — only run configs you trust.
> `shell` commands are killed after 120 seconds unless the step sets its own
> `timeout:`.

Steps are validated against the backend's capabilities before anything is
launched — e.g. a `navigate` action against the `renode` backend fails
immediately with `backend renode does not support navigate`.

Step expectations:

```yaml
expect:
  vision: "natural-language description of what should be visible"
  url_contains: "/dashboard"      # web / android
  log_contains: "Server ready"    # any backend
  no_log_contains: "FATAL"        # any backend
```

When a step fails, its screenshot is written to `.verify-artifacts/` next to
the config (override with `verify run --artifacts-dir`), and the path is
included in both the text and `--json` reports.

## Docker sandboxes

Backends that need an isolated target environment (Android emulator, sandboxed
Linux desktop) can run it inside Docker. Every container is labeled
`verify.session=<uuid>` and torn down on backend stop, normal exit, and SIGINT.

```yaml
backend: android
launch:
  package: com.example.app
  wait_after: 3
options:
  android:
    docker_image: budtmo/docker-android:emulator_14.0
    docker_adb_port: 5555
```

Containers left over from a hard crash:

```bash
verify sandboxes list                       # show every verify container
verify sandboxes prune                      # remove orphans > 30min old
verify sandboxes prune --all                # remove every verify container
verify sandboxes prune --older-than 600
```

## MCP server

`verify mcp` starts an MCP stdio server pinned to one backend session, exposing
`screenshot`, `click`, `type_text`, `key`, `wait`, `read_logs`, `navigate`,
`locate`, and `screen_size` to Claude Code or any MCP client. Useful for
exploratory testing where the agent picks the next action itself.

```json
{
  "mcpServers": {
    "verify": {
      "command": "verify",
      "args": ["mcp", "--config", "./.verify.yaml"]
    }
  }
}
```

## Adding a backend

Subclass `verify.backends.base.Backend`, implement six primitives
(`screenshot`, `click`, `type_text`, `key`, `read_logs`, plus `start`/`stop`),
decorate with `@register`. The runner and MCP server pick it up. See
`verify/backends/web.py` for the simplest example.

## Examples

- `examples/web.verify.yaml` — React app + login flow
- `examples/android.verify.yaml` — Android keyboard / search
- `examples/stm32.verify.yaml` — blink firmware via Renode
- `examples/linux_desktop.verify.yaml` — Qt app window
- `examples/generic.verify.yaml` — any process + host screen

## Test suite

```bash
pip install "verify-cli[all] @ git+https://github.com/JeremiahM37/verify"
playwright install chromium
pytest -q
```

The e2e suite drives a real Chromium against a sample app with an intentional
UI bug; vision catches the visible error banner that a backend-only test
would miss.

## License

MIT.
