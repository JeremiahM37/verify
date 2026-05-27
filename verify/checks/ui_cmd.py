"""Ad-hoc UI check — drives a real headless browser through a sequence of
steps declared inline, no script file required. Works for desktop and full
mobile emulation (device viewport + user agent + touch events + isMobile
rendering flag), across all three browser engines.

    - name: tabs-work
      type: ui
      url: "http://127.0.0.1:9105/term/7691"
      device: "Pixel 7"                          # Playwright preset → real
                                                   # mobile UA + viewport + touch
      engine: chromium                            # or webkit (Safari) / firefox
      steps:
        - wait: "#tabAdd"                          # wait for selector to appear
        - tap: "#tabAdd"                            # touch tap (real touch event)
        - wait: ".tab:nth-of-type(2)"               # wait for the new tab
        - expect_text: { selector: ".tab.active", contains: "claude-2" }
        - expect_count: { selector: ".tab", n: 3 }  # exact count
        - fill: { selector: "#myInput", text: "hello" }
        - long_press: ".some-thing"                 # 600ms hold (configurable)
        - swipe: { from: [200, 700], to: [200, 200] } # touch drag
        - ime_type: { selector: "input", commit: "auto-corrected!" }  # simulate
                                                                       # mobile keyboard
                                                                       # composition flow
        - eval: "() => window.PORTS"                # arbitrary JS, must be truthy
        - screenshot: "/tmp/state.png"              # debug aid
        - sleep: 0.3                                # seconds

For mobile UX:
  device:    name from Playwright's device list (e.g. "Pixel 7",
             "iPhone 13", "iPad Mini"). Sets viewport, userAgent,
             deviceScaleFactor, isMobile, hasTouch. Override individual
             fields with `viewport:`, `user_agent:`, etc.
  engine:    chromium (default), webkit (mobile Safari emulation), or
             firefox. Install each via `playwright install <engine>`.

The check also fails if any uncaught JS error fires on the page during the
run, since that's almost always a regression you wanted to catch.

Requires playwright installed (`pip install verify-cli[ui]` then
`playwright install chromium webkit firefox` for the engines you'll use).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


def _has_playwright() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


async def _drive(cfg: dict, log: list) -> tuple[bool, list[str]]:
    from playwright.async_api import async_playwright
    url = cfg["url"]
    steps = cfg.get("steps") or []
    timeout = float(cfg.get("step_timeout", 10)) * 1000  # ms per step
    headless = cfg.get("headless", True)
    engine = cfg.get("engine", "chromium")
    js_errors: list[str] = []
    console_messages: list[str] = []
    screenshot_on_failure = cfg.get("screenshot_on_failure", True)
    check_name = cfg.get("name", "ui")

    if engine not in ("chromium", "firefox", "webkit"):
        log.append(f"unknown engine {engine!r}; supported: chromium, firefox, webkit")
        return False, []

    async with async_playwright() as pw:
        launcher = getattr(pw, engine)
        try:
            browser = await launcher.launch(headless=headless)
        except Exception as e:
            log.append(f"{engine} launch failed (did you `playwright install {engine}`?): {e}")
            return False, []

        # Build context kwargs — start from device preset if given, then
        # allow individual overrides.
        ctx_kwargs: dict = {}
        device_name = cfg.get("device")
        if device_name:
            device = pw.devices.get(device_name)
            if not device:
                close_names = ", ".join(sorted(
                    n for n in pw.devices if device_name.lower() in n.lower()
                )[:5]) or "(none — try 'Pixel 7' or 'iPhone 13')"
                log.append(f"unknown device {device_name!r}. Close matches: {close_names}")
                await browser.close()
                return False, []
            ctx_kwargs.update(device)
        if cfg.get("viewport"):     ctx_kwargs["viewport"]    = cfg["viewport"]
        if cfg.get("user_agent"):   ctx_kwargs["user_agent"]  = cfg["user_agent"]
        if cfg.get("locale"):       ctx_kwargs["locale"]      = cfg["locale"]
        if cfg.get("timezone"):     ctx_kwargs["timezone_id"] = cfg["timezone"]
        if cfg.get("has_touch") is not None: ctx_kwargs["has_touch"] = cfg["has_touch"]
        if cfg.get("is_mobile")  is not None: ctx_kwargs["is_mobile"]  = cfg["is_mobile"]
        ctx_kwargs.setdefault("viewport", {"width": 1280, "height": 800})
        ctx_kwargs["permissions"] = cfg.get("permissions") or ["clipboard-read", "clipboard-write"]

        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(f"pageerror: {e}"))
        def _on_console(m):
            line = f"[{m.type}] {m.text}"
            console_messages.append(line)
            if m.type == "error":
                js_errors.append(f"console.error: {m.text}")
        page.on("console", _on_console)

        async def _shot_on_failure(step_idx: int | None = None):
            if not screenshot_on_failure:
                return
            import re, time
            safe = re.sub(r"[^a-zA-Z0-9_-]", "_", check_name)[:60]
            suffix = f"_step{step_idx}" if step_idx is not None else ""
            path = f"/tmp/verify-fail-{safe}{suffix}-{int(time.time())}.png"
            try:
                await page.screenshot(path=path, full_page=False)
                log.append(f"screenshot saved: {path}")
            except Exception as e:
                log.append(f"(couldn't capture screenshot: {e})")

        try:
            await page.goto(url, timeout=timeout)
        except Exception as e:
            log.append(f"goto {url} failed: {e}")
            await _shot_on_failure()
            _append_console_tail(log, console_messages)
            await browser.close()
            return False, js_errors

        for i, step in enumerate(steps):
            try:
                ok = await _step(page, step, timeout, log, i)
            except Exception as e:
                log.append(f"step {i} ({step!r}) raised: {e}")
                ok = False
            if not ok:
                await _shot_on_failure(step_idx=i)
                _append_console_tail(log, console_messages)
                await browser.close()
                return False, js_errors

        await browser.close()
    # JS errors during the run are a failure unless explicitly tolerated.
    if js_errors and not cfg.get("allow_js_errors"):
        log.append(f"{len(js_errors)} JS error(s) on page:")
        for e in js_errors[:5]:
            log.append(f"  - {e}")
        _append_console_tail(log, console_messages)
        return False, js_errors
    return True, js_errors


def _append_console_tail(log: list[str], console: list[str], n: int = 8) -> None:
    """Tack the last N browser-console messages onto the failure log so the
    user can see what the app was saying at the moment things broke."""
    if not console:
        return
    log.append(f"last {min(n, len(console))} console messages:")
    for line in console[-n:]:
        log.append(f"  > {line}")


async def _step(page, step, timeout, log, idx) -> bool:
    """Run one step; return True if it succeeded."""
    # Bare-string shortcuts for argument-less commands (reload / back /
    # forward). Anything else as a bare string is wait-for-selector.
    if isinstance(step, str):
        if step == "reload":  await page.reload(timeout=timeout); return True
        if step == "back":    await page.go_back(timeout=timeout); return True
        if step == "forward": await page.go_forward(timeout=timeout); return True
        await page.wait_for_selector(step, timeout=timeout)
        return True
    if not isinstance(step, dict):
        log.append(f"step {idx}: not a string or dict: {step!r}")
        return False

    # Normalize: each dict has exactly one action key
    if "goto" in step:
        await page.goto(step["goto"], timeout=timeout); return True
    if "reload" in step:
        await page.reload(timeout=timeout); return True
    if "back" in step:
        await page.go_back(timeout=timeout); return True
    if "forward" in step:
        await page.go_forward(timeout=timeout); return True
    if "wait_for_url" in step:
        # Wait until the URL matches a substring or regex.
        import re
        pat = step["wait_for_url"]
        deadline = asyncio.get_event_loop().time() + timeout / 1000
        while asyncio.get_event_loop().time() < deadline:
            u = page.url
            if re.search(pat, u):
                return True
            await asyncio.sleep(0.1)
        log.append(f"step {idx}: wait_for_url {pat!r}: timed out, url is {page.url}")
        return False
    if "expect_url" in step:
        import re
        pat = step["expect_url"]
        ok = bool(re.search(pat, page.url))
        if not ok:
            log.append(f"step {idx}: expect_url {pat!r}: actual url is {page.url}")
        return ok
    if "set_viewport" in step:
        # For rotation testing: {width: 896, height: 414} switches landscape.
        v = step["set_viewport"]
        await page.set_viewport_size({"width": int(v["width"]), "height": int(v["height"])})
        return True
    if "scroll" in step:
        # Programmatic scroll the page (vs `swipe` which fires real TouchEvents).
        # {x: 0, y: 1000} scrolls to that position; {by: [0, 500]} scrolls by delta.
        s = step["scroll"]
        if "by" in s:
            dx, dy = s["by"]
            await page.evaluate(f"() => window.scrollBy({dx}, {dy})")
        else:
            x = int(s.get("x", 0)); y = int(s.get("y", 0))
            await page.evaluate(f"() => window.scrollTo({x}, {y})")
        return True
    if "set_local_storage" in step:
        # Preload state. Useful for skipping login flows etc.
        for k, v in step["set_local_storage"].items():
            await page.evaluate(
                f"([k, v]) => localStorage.setItem(k, v)", [k, str(v)]
            )
        return True
    if "clear_local_storage" in step:
        await page.evaluate("() => localStorage.clear()")
        return True
    if "wait" in step:
        await page.wait_for_selector(step["wait"], timeout=timeout); return True
    if "click" in step:
        sel = step["click"]
        await page.wait_for_selector(sel, timeout=timeout)
        await page.click(sel, timeout=timeout); return True
    if "tap" in step:
        # Real touch tap — fires touchstart/touchend instead of mouse events.
        # Requires context with has_touch=True (set by mobile devices) or it
        # raises. Falls back to click in that case so configs work everywhere.
        sel = step["tap"]
        await page.wait_for_selector(sel, timeout=timeout)
        try:
            await page.tap(sel, timeout=timeout)
        except Exception as e:
            if "has_touch" in str(e).lower():
                await page.click(sel, timeout=timeout)
            else:
                raise
        return True
    if "long_press" in step:
        # Hold a finger on the element for `duration` ms (default 600).
        sel = step["long_press"] if isinstance(step["long_press"], str) else step["long_press"]["selector"]
        duration = 600
        if isinstance(step["long_press"], dict):
            duration = int(step["long_press"].get("duration", 600))
        await page.wait_for_selector(sel, timeout=timeout)
        box = await page.locator(sel).first.bounding_box()
        if not box:
            log.append(f"step {idx}: long_press {sel!r}: no bounding box")
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await page.mouse.move(x, y)
        await page.mouse.down()
        await asyncio.sleep(duration / 1000)
        await page.mouse.up()
        return True
    if "swipe" in step:
        # Touch drag from one point to another. Page.mouse in a mobile context
        # dispatches as pointer events, which most page-level scroll handlers
        # don't see — so we fire the raw TouchEvent sequence by JS instead,
        # which does trigger browser scrolling and any custom touch listeners.
        s = step["swipe"]
        fx, fy = s["from"]
        tx, ty = s["to"]
        n = int(s.get("steps", 12))
        await page.evaluate(
            """({fx, fy, tx, ty, n}) => {
                const el = document.elementFromPoint(fx, fy) || document.body;
                const fire = (type, x, y) => {
                    const t = new Touch({
                        identifier: 0, target: el, clientX: x, clientY: y,
                        pageX: x, pageY: y, screenX: x, screenY: y,
                        radiusX: 1, radiusY: 1, rotationAngle: 0, force: 0.5,
                    });
                    el.dispatchEvent(new TouchEvent(type, {
                        bubbles: true, cancelable: true, composed: true,
                        touches: type === 'touchend' ? [] : [t],
                        targetTouches: type === 'touchend' ? [] : [t],
                        changedTouches: [t],
                    }));
                };
                fire('touchstart', fx, fy);
                for (let i = 1; i <= n; i++) {
                    const x = fx + (tx - fx) * (i / n);
                    const y = fy + (ty - fy) * (i / n);
                    fire('touchmove', x, y);
                }
                fire('touchend', tx, ty);
            }""", {"fx": fx, "fy": fy, "tx": tx, "ty": ty, "n": n},
        )
        return True
    if "ime_type" in step:
        # Simulate Android/iOS IME (predictive keyboard) composition flow.
        # IME inputs fire compositionstart/compositionupdate/compositionend
        # instead of clean keystrokes. The exact sequence we replicate:
        #   focus → compositionstart → multiple compositionupdate (typing) →
        #   compositionend with the committed/auto-corrected text.
        # Useful for catching the Gboard duplicate-word class of bug.
        e = step["ime_type"]
        sel    = e["selector"]
        commit = e.get("commit", "")
        compose = e.get("composition", commit)
        await page.wait_for_selector(sel, timeout=timeout)
        await page.evaluate(
            """([sel, compose, commit]) => {
                const el = document.querySelector(sel);
                if (!el) throw new Error('no element');
                el.focus();
                const fire = (type, data) => {
                    el.dispatchEvent(new CompositionEvent(type, { data, bubbles: true }));
                };
                fire('compositionstart', '');
                // Step the composition in chunks so input handlers see it grow
                for (let i = 1; i <= compose.length; i++) {
                    fire('compositionupdate', compose.slice(0, i));
                    // Mirror what real browsers do: also fire `input` with the
                    // intermediate value via the value setter (works for both
                    // <input> and <textarea>).
                    el.value = compose.slice(0, i);
                    el.dispatchEvent(new InputEvent('input', {
                        inputType: 'insertCompositionText',
                        data: compose.slice(0, i), bubbles: true,
                    }));
                }
                // Commit: replace composition with `commit` (could differ if
                // autocorrect changed the word).
                el.value = commit;
                fire('compositionend', commit);
                el.dispatchEvent(new InputEvent('input', {
                    inputType: 'insertReplacementText',
                    data: commit, bubbles: true,
                }));
            }""",
            [sel, compose, commit],
        )
        return True
    if "fill" in step:
        f = step["fill"]
        sel, text = (f["selector"], f["text"]) if isinstance(f, dict) else f
        await page.fill(sel, text, timeout=timeout); return True
    if "type" in step:
        t = step["type"]
        sel, text = (t["selector"], t["text"]) if isinstance(t, dict) else t
        await page.type(sel, text, timeout=timeout); return True
    if "press" in step:
        await page.keyboard.press(step["press"]); return True
    if "sleep" in step:
        await asyncio.sleep(float(step["sleep"])); return True
    if "expect_text" in step:
        e = step["expect_text"]
        sel = e["selector"]
        want = e.get("contains") or e.get("equals")
        await page.wait_for_selector(sel, timeout=timeout)
        # For form fields the "text" the user cares about is the value, not
        # textContent (which is empty for <input>). Auto-detect either case.
        got = (await page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return '';
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return el.value || '';
                return el.textContent || '';
            }""", sel,
        ) or "").strip()
        if e.get("equals") is not None:
            ok = got == want
        else:
            ok = (want or "") in got
        if not ok:
            log.append(f"step {idx}: expect_text on {sel!r}: want {want!r}, got {got!r}")
        return ok
    if "expect_count" in step:
        e = step["expect_count"]
        sel = e["selector"]
        n = int(e["n"])
        got = await page.locator(sel).count()
        ok = got == n
        if not ok:
            log.append(f"step {idx}: expect_count {sel!r}: want {n}, got {got}")
        return ok
    if "expect_visible" in step:
        sel = step["expect_visible"]
        try:
            await page.wait_for_selector(sel, state="visible", timeout=timeout)
            return True
        except Exception as e:
            log.append(f"step {idx}: expect_visible {sel!r}: {e}")
            return False
    if "eval" in step:
        expr = step["eval"]
        # Wrap bare expression in arrow if not already a function
        if not expr.strip().startswith(("(", "function", "async ")) and "=>" not in expr:
            expr = f"() => ({expr})"
        result = await page.evaluate(expr)
        ok = bool(result)
        if not ok:
            log.append(f"step {idx}: eval returned falsy: {result!r}")
        return ok
    if "screenshot" in step:
        path = step["screenshot"]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        await page.screenshot(path=path); return True
    if "expect_status" in step:
        # Probe an HTTP endpoint from inside the browser context
        e = step["expect_status"]
        url = e["url"]; want = int(e.get("code", 200))
        resp = await page.request.fetch(url)
        ok = resp.status == want
        if not ok:
            log.append(f"step {idx}: expect_status {url}: want {want}, got {resp.status}")
        return ok

    log.append(f"step {idx}: unknown action keys {list(step.keys())}")
    return False


def run(cfg):
    name = cfg.get("name", "ui")
    if not cfg.get("url"):
        return {"name": name, "ok": False, "detail": "missing 'url'"}
    if not _has_playwright():
        return {"name": name, "ok": False,
                "detail": "playwright not installed. `pip install verify-cli[ui]` "
                          "and then `playwright install chromium`"}
    log: list[str] = []
    try:
        ok, js_errors = asyncio.run(_drive(cfg, log))
    except Exception as e:
        return {"name": name, "ok": False, "detail": f"runner error: {e}"}
    return {
        "name": name,
        "ok": ok,
        "detail": "\n".join(log) if log else None,
    }
