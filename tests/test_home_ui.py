"""Playwright smoke test of the home page (#/) at phone width (412 px): the map shows a marker for a located venue,
choosing a year narrows the list, the unmapped note appears. Tile requests are intercepted and answered with a 1x1
PNG, so nothing touches the network. Same setup as test_shell_ui.py; skips without Playwright or Chromium unless
STUBS_UI_REQUIRED=1."""
import base64
import os
import threading

import pytest

import app as stubs
from conftest import mk_show, mk_venue

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PHONE = {"width": 412, "height": 915}

try:
    from playwright.sync_api import expect, sync_playwright
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
    ctx = browser.new_context(viewport=PHONE, extra_http_headers=headers or {})
    ctx.route("**/*.png", lambda r: r.fulfill(body=PNG, content_type="image/png") if "tile." in r.request.url
              else r.continue_())
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url)
    return page


def seed(con):
    hall = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.2)
    nowhere = mk_venue(con, "The Bellwether", city="Reno", region="NV")
    mk_show(con, "Cold Harbor", "2009-06-20", hall)
    mk_show(con, "Marigold Static", "2010-01-02", hall)
    mk_show(con, "The Paper Lanterns", "2010-05-05", nowhere)


def test_home_map_year_strip_and_list(server, browser, con):
    seed(con)
    page = open_page(browser, server + "/")
    page.locator(".leaflet-marker-icon, .marker-cluster").first.wait_for()
    assert page.locator(".show-row").count() == 3
    assert page.locator(".unmapped").inner_text() == "1 show not on the map"
    assert page.locator(".leaflet-control-attribution").inner_text().count("OpenStreetMap contributors") == 1

    page.locator(".year-pill", has_text="2009").click()
    page.locator(".year-pill.is-selected", has_text="2009").wait_for()
    expect(page.locator(".show-row")).to_have_count(1)
    assert "Cold Harbor" in page.locator(".show-row").inner_text()
    assert page.locator(".unmapped").count() == 0
    page.locator(".leaflet-marker-icon").first.wait_for()
    page.locator(".leaflet-marker-icon").first.click()
    page.locator(".map-popup a", has_text="Cold Harbor").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []


def test_home_deep_link_to_a_year(server, browser, con):
    seed(con)
    page = open_page(browser, server + "/#/?year=2010")
    expect(page.locator(".show-row")).to_have_count(2)
    assert page.locator(".year-pill.is-selected").inner_text() == "2010"
    assert page.locator(".unmapped").inner_text() == "1 show not on the map"
    assert page.errors == []
