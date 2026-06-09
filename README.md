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
pip install verify-cli                # core
pip install "verify-cli[web]"         # + Playwright
pip install "verify-cli[android]"     # + adbutils
pip install "verify-cli[desktop]"     # + mss
pip install "verify-cli[mcp]"         # + MCP server
pip install "verify-cli[all]"         # everything
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

## Action vocabulary

Same across every backend.

| Action | Args |
|---|---|
| `navigate` | `target: <url>` |
| `click` (alias `tap`) | `at: [x, y]` OR `selector: <css>` OR `locate: { vision: "..." }` |
| `type` (alias `type_text`) | `text: "..."` |
| `key` | `name: enter / tab / back / ...` |
| `wait` | `seconds: 1.5` |
| `shell` | `cmd: "..."` (escape hatch; runs on host) |

Shorthand: any single-arg action can be written `{verb: value}` —
`{wait: 1}`, `{type: "hello"}`, `{key: enter}`.

Step expectations:

```yaml
expect:
  vision: "natural-language description of what should be visible"
  url_contains: "/dashboard"      # web / android
  log_contains: "Server ready"    # any backend
  no_log_contains: "FATAL"        # any backend
```

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
pip install "verify-cli[all]"
playwright install chromium
pytest -q
```

The e2e suite drives a real Chromium against a sample app with an intentional
UI bug; vision catches the visible error banner that a backend-only test
would miss.

## License

MIT.
