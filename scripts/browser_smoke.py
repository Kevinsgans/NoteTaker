"""Run with uv run --with playwright scripts/browser_smoke.py --browser PATH."""

import argparse
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from notetaker.auth import set_password
from notetaker.cli import demo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", required=True)
    parser.add_argument("--output", default="data/browser-smoke")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="notetaker-ui-") as temp:
        root = Path(temp)
        demo(root / "replay")
        (root / "data").mkdir()
        password = secrets.token_urlsafe(24)
        set_password(root / "data" / "auth.json", password)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = root / "config.toml"
        config.write_text(
            f'data_dir={json.dumps(str(root / "data"))}\nreplay_dir={json.dumps(str(root / "replay"))}\nprovider="fake"\nport={port}\n'
        )
        with open(output / "server.log", "w") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "notetaker.cli", "--config", str(config), "serve"],
                stdout=log,
                stderr=log,
            )
            try:
                address = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        urllib.request.urlopen(address, timeout=0.2)
                    except urllib.error.HTTPError as error:
                        if error.code == 401:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=args.browser, headless=True
                    )
                    context = browser.new_context(
                        http_credentials={"username": "notetaker", "password": password},
                        viewport={"width": 1440, "height": 1100},
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(address)
                    expect(page.locator("#camera-status")).to_have_text("ready")
                    page.locator("#course").fill("Linear algebra")
                    page.locator("#capture").click()
                    page.locator("#detail").wait_for(state="visible")
                    expect(page.locator("#page-state")).to_contain_text("completed", timeout=15000)
                    page.locator("#edit-title").fill("Eigenvectors and linear maps")
                    page.locator("#edit-tags").fill("eigenvectors, linear algebra")
                    page.locator("#metadata button").click()
                    expect(page.locator("#page-state")).to_contain_text("Reviewed")
                    page.locator("#query").fill("eigenvectors")
                    page.locator("#search button").click()
                    expect(page.locator(".card")).to_have_count(1)
                    page.locator("#crop").click()
                    canvas = page.locator("#crop-canvas")
                    canvas.wait_for(state="visible")
                    rect = canvas.bounding_box()
                    for x, y in [
                        (230 / 1280, 65 / 960),
                        (1030 / 1280, 65 / 960),
                        (1030 / 1280, 895 / 960),
                        (230 / 1280, 895 / 960),
                    ]:
                        canvas.click(position={"x": rect["width"] * x, "y": rect["height"] * y})
                    page.locator("#save-crop").click()
                    expect(page.locator("#crop-canvas")).to_be_hidden()
                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(output / "mobile.png"), full_page=True)
                    assert not errors, errors
                    browser.close()
                    print(
                        "Browser smoke passed: capture, inference, edit, search, recrop, desktop/mobile, no JavaScript errors"
                    )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
