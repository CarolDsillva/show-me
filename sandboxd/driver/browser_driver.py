"""Runs inside the sandbox. Drives Chromium one validated action at a time.

Protocol: the first stdin line is {"user": ..., "password": ...}. Each later line is one
action. Every reply is one JSON line on stdout. sandboxd treats all of it as hostile.
"""

import base64
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9090"
ACTION_TIMEOUT_MS = 8000
TEXT_LIMIT = 4000
ELEMENT_LIMIT = 60

LIST_ELEMENTS = """
(limit) => {
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  const quote = (value) => value.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
  const found = [];
  for (const el of document.querySelectorAll('a, button, input, select, textarea')) {
    if (found.length >= limit || !visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || '').trim().slice(0, 80);
    const href = el.getAttribute('href');
    let selector = null;
    if (el.id) selector = `#${CSS.escape(el.id)}`;
    else if (el.name) selector = `${tag}[name="${quote(el.name)}"]`;
    else if (tag === 'a' && href) selector = `a[href="${quote(href)}"]`;
    else if (text) selector = `${tag}:has-text("${quote(text)}")`;
    found.push({tag, text, href, type: el.getAttribute('type'), selector});
  }
  return found;
}
"""


def settle(page):
    try:
        page.wait_for_load_state("load", timeout=3000)
    except Exception:
        pass
    page.wait_for_timeout(400)


def state(page, console):
    shot = page.screenshot(type="png")
    return {
        "url": page.url,
        "title": page.title()[:200],
        "text": page.inner_text("body", timeout=3000)[:TEXT_LIMIT],
        "elements": page.evaluate(LIST_ELEMENTS, ELEMENT_LIMIT),
        "console": console[-10:],
        "screenshot_b64": base64.b64encode(shot).decode(),
    }


def perform(page, action):
    name = action["action"]
    selector = action.get("selector")
    value = action.get("value")
    if name == "goto":
        page.goto(BASE + action["path"], wait_until="load", timeout=ACTION_TIMEOUT_MS)
    elif name == "click":
        page.click(selector, timeout=ACTION_TIMEOUT_MS)
    elif name == "fill":
        page.fill(selector, value or "", timeout=ACTION_TIMEOUT_MS)
    elif name == "press":
        page.press(selector, value or "", timeout=ACTION_TIMEOUT_MS)
    elif name == "select":
        page.select_option(selector, value or "", timeout=ACTION_TIMEOUT_MS)
    elif name == "wait_for":
        page.wait_for_selector(selector, timeout=ACTION_TIMEOUT_MS)
    elif name not in ("screenshot", "read_text"):
        raise ValueError(f"action not allowed: {name}")
    settle(page)


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    login = json.loads(sys.stdin.readline())
    console = []
    with sync_playwright() as playwright:
        # Chromium's own sandbox needs privileges this container doesn't have. gVisor is the boundary.
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.startswith(BASE + "/") else route.abort(),
        )
        page = context.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:300]) if m.type == "error" else None)
        page.goto(f"{BASE}/login/", wait_until="load", timeout=60000)
        page.fill('input[name="username"]', login["user"])
        page.fill('input[name="password"]', login["password"])
        page.press('input[name="password"]', "Enter")
        page.wait_for_url("**/bookmarks**", timeout=60000)
        settle(page)
        emit({"ok": True, "error": None, **state(page, console)})

        for line in sys.stdin:
            action = json.loads(line)
            try:
                perform(page, action)
                emit({"ok": True, "error": None, **state(page, console)})
            except Exception as error:
                message = f"{type(error).__name__}: {error}"[:600]
                try:
                    emit({"ok": False, "error": message, **state(page, console)})
                except Exception:
                    emit({"ok": False, "error": message})
        browser.close()


if __name__ == "__main__":
    main()
