"""Runs inside the sandbox. Logs in to linkding with Chromium and takes a screenshot.

Prints one JSON line on stdout. Everything it does stays on localhost.
"""

import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9090"
started = time.monotonic()
result = {"steps": []}


def step(name):
    result["steps"].append({"step": name, "at_s": round(time.monotonic() - started, 2)})


with sync_playwright() as playwright:
    # Chromium's own sandbox needs privileges this container doesn't have. gVisor is the boundary.
    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    step("chromium_launched")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{BASE}/login/", wait_until="load", timeout=60000)
    step("login_page_loaded")
    page.fill('input[name="username"]', os.environ["SPIKE_USER"])
    page.fill('input[name="password"]', os.environ["SPIKE_PASSWORD"])
    page.press('input[name="password"]', "Enter")
    page.wait_for_url("**/bookmarks**", timeout=60000)
    step("logged_in")
    result["title"] = page.title()
    result["url"] = page.url
    page.screenshot(path="/tmp/linkding.png")
    step("screenshot_taken")
    try:
        page.goto("http://203.0.113.10/", timeout=5000)
        result["external_navigation"] = "reached"
    except Exception as error:
        result["external_navigation"] = f"blocked: {type(error).__name__}"
    step("external_navigation_attempted")
    browser.close()

json.dump(result, sys.stdout)
