"""Playwright smoke test of the shell at phone width (412 px): the title, the header, no console errors, no sideways
scroll, and #/admin for an editor with no admin cards registered.

Runs against a live werkzeug server on a free loopback port, using the test database. The editor is a browser
context that sends the write key on every request. Skips when Playwright or Chromium is missing, unless
STUBS_UI_REQUIRED=1 (the CI UI smoke job), where that is a failure."""
import os
import threading

import pytest

import app as stubs
from conftest import WRITE_KEY

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PHONE = {"width": 412, "height": 915}

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    if REQUIRED:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)


@pytest.fixture
def server():
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, stubs.app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)


@pytest.fixture
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - no usable Chromium here
            if REQUIRED:
                raise
            pytest.skip(f"Chromium won't launch: {e}")
        yield b
        b.close()


def open_page(browser, url, headers=None):
    """A phone-sized page at url, collecting console errors and uncaught exceptions into page.errors."""
    ctx = browser.new_context(viewport=PHONE, extra_http_headers=headers or {})
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url)
    return page


def no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_home_loads_at_phone_width(server, browser):
    page = open_page(browser, server + "/")
    page.wait_for_selector("#app .view")
    assert page.title() == "Stubs"
    assert page.locator(".brand").inner_text() == "Stubs"
    assert page.locator("#nav a", has_text="Admin").count() == 0
    assert no_sideways_scroll(page)
    assert page.errors == []


def test_admin_for_an_editor_with_no_cards(server, browser):
    page = open_page(browser, server + "/#/admin", {"X-API-Key": WRITE_KEY})
    page.wait_for_selector("text=Nothing needs you")
    assert page.title() == "Stubs"
    assert page.locator("#nav a", has_text="Admin").count() == 1
    assert no_sideways_scroll(page)
    assert page.errors == []


def test_admin_for_a_visitor_says_editors_only(server, browser):
    page = open_page(browser, server + "/#/admin")
    page.wait_for_selector("text=Editors only")
    assert page.locator("text=Nothing needs you").count() == 0
    assert page.errors == []


def test_unknown_route_and_the_admin_path(server, browser):
    page = open_page(browser, server + "/admin#/no/such/page", {"X-API-Key": WRITE_KEY})
    page.wait_for_selector("text=There's nothing at this address")
    assert page.title() == "Stubs"
    page.click("text=Go home")
    page.wait_for_selector("#app .view[data-route='']")
    assert page.errors == []
