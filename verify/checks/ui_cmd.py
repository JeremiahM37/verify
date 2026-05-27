"""Ad-hoc UI check — drives a real headless browser through a sequence of
steps declared inline, no script file required. The model people will reach
for 90% of the time:

    - name: tabs-work
      type: ui
      url: "http://127.0.0.1:9105/term/7691"
      viewport: { width: 414, height: 896 }       # phone-ish, optional
      steps:
        - wait: "#tabAdd"                          # wait for selector to appear
        - click: "#tabAdd"                          # click it
        - wait: ".tab:nth-of-type(2)"               # wait for the new tab
        - expect_text: { selector: ".tab.active", contains: "claude-2" }
        - expect_count: { selector: ".tab", n: 3 }  # exact count
        - fill: { selector: "#myInput", text: "hello" }
        - eval: "() => window.PORTS"                # arbitrary JS, must be truthy
        - screenshot: "/tmp/state.png"              # optional debug aid
        - sleep: 0.3                                # seconds

The check also fails if any uncaught JS error fires on the page during the
run, since that's almost always a regression you wanted to catch.

Requires playwright installed (`pip install verify-cli[ui]` then
`playwright install chromium`).
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
    viewport = cfg.get("viewport") or {"width": 1280, "height": 800}
    timeout = float(cfg.get("step_timeout", 10)) * 1000  # ms per step
    headless = cfg.get("headless", True)
    js_errors: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            viewport=viewport,
            permissions=cfg.get("permissions") or ["clipboard-read", "clipboard-write"],
        )
        page = await ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and js_errors.append(f"console: {m.text}"))

        try:
            await page.goto(url, timeout=timeout)
        except Exception as e:
            log.append(f"goto {url} failed: {e}")
            await browser.close()
            return False, js_errors

        for i, step in enumerate(steps):
            try:
                ok = await _step(page, step, timeout, log, i)
            except Exception as e:
                log.append(f"step {i} ({step!r}) raised: {e}")
                ok = False
            if not ok:
                await browser.close()
                return False, js_errors

        await browser.close()
    # JS errors during the run are a failure unless explicitly tolerated.
    if js_errors and not cfg.get("allow_js_errors"):
        log.append(f"{len(js_errors)} JS error(s) on page:")
        for e in js_errors[:5]:
            log.append(f"  - {e}")
        return False, js_errors
    return True, js_errors


async def _step(page, step, timeout, log, idx) -> bool:
    """Run one step; return True if it succeeded."""
    if isinstance(step, str):
        # Bare-string shorthand for "wait for this selector"
        await page.wait_for_selector(step, timeout=timeout)
        return True
    if not isinstance(step, dict):
        log.append(f"step {idx}: not a string or dict: {step!r}")
        return False

    # Normalize: each dict has exactly one action key
    if "goto" in step:
        await page.goto(step["goto"], timeout=timeout); return True
    if "wait" in step:
        await page.wait_for_selector(step["wait"], timeout=timeout); return True
    if "click" in step:
        sel = step["click"]
        await page.wait_for_selector(sel, timeout=timeout)
        await page.click(sel, timeout=timeout); return True
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
        got = (await page.text_content(sel) or "").strip()
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
