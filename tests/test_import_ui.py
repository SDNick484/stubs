"""Playwright smoke test of the import screens at phone width (412 px): upload tests/fixtures/concerts.csv, read the
review, untick a row, apply, and see the card go; a 409 on Apply shows as a message; Discard asks inline. Same setup as
test_merge_ui.py (a live werkzeug server and the test database); skips without Playwright or Chromium unless
STUBS_UI_REQUIRED=1."""
import os
import pathlib
import threading

import pytest

import app as stubs
from conftest import WRITE_KEY

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PHONE = {"width": 412, "height": 915}
CSV = pathlib.Path(__file__).parent / "fixtures" / "concerts.csv"

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


def open_page(browser, url):
    ctx = browser.new_context(viewport=PHONE, extra_http_headers={"X-API-Key": WRITE_KEY})
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url)
    return page


def upload(page, base):
    page.goto(base + "/admin#/admin/import")
    page.locator("input[type=file]").set_input_files(str(CSV))
    page.locator(".import-form button").click()
    page.locator("h2", has_text="New (11)").wait_for()


def test_upload_review_untick_apply(server, browser, con):
    page = open_page(browser, server + "/admin#/admin")
    page.locator(".empty", has_text="Nothing needs you.").wait_for()
    upload(page, server)
    assert page.locator("h2", has_text="Already have (1)").is_visible()
    assert page.locator("h2", has_text="Skipped (3)").is_visible()
    assert page.locator("[data-card]").count() == 0

    # the dashboard now has the waiting import
    page.goto(server + "/admin#/admin")
    page.locator("[data-card='Imports waiting']", has_text="Imports waiting (1)").wait_for()
    page.locator("[data-card='Imports waiting'] a", has_text="CSV import").click()
    page.locator("h2", has_text="New (11)").wait_for()

    page.locator(".import-group[data-verdict=new] .import-include").first.uncheck()
    page.locator("h2", has_text="New (11)").wait_for()  # still 11 rows: untick keeps the row in its group
    assert page.locator(".import-group[data-verdict=new] .import-include:checked").count() == 10

    # the Apply button is disabled while the request is in flight
    gate = []
    page.route("**/apply", lambda route: gate.append(route))
    page.locator("[data-act=apply]").click()
    page.wait_for_function("document.querySelector('[data-act=apply]').disabled")
    while not gate:
        page.wait_for_timeout(20)
    gate[0].continue_()
    page.locator(".import-added", has_text="Added 10 shows.").wait_for()
    assert page.locator("[data-act=apply]").is_disabled()
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 10

    page.goto(server + "/admin#/admin")
    # the applied shows include upcoming ones, so other cards may show; this one must be gone
    page.locator("[data-card='Coming up']").wait_for()
    page.locator("[data-card='Imports waiting']").wait_for(state="detached")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []


def test_second_apply_is_a_message_not_an_error_page(server, browser, con):
    page = open_page(browser, server + "/admin#/admin/import")
    upload(page, server)
    bid = con.execute("SELECT id FROM import_batch").fetchone()[0]
    done = page.request.post(f"{server}/admin/api/imports/{bid}/apply")  # "the other tab"
    assert done.status == 200
    page.locator("[data-act=apply]").click()
    page.locator(".import-status", has_text="This import was already applied.").wait_for()
    assert page.locator("h2", has_text="New (11)").is_visible()  # the screen is still there
    assert page.locator(".notice.error").count() == 0
    assert page.locator("[data-act=apply]").is_disabled()


def test_discard_asks_inline(server, browser, con):
    page = open_page(browser, server + "/admin#/admin/import")
    upload(page, server)
    page.locator("[data-act=discard]").click()
    page.locator("[data-act=discard-no]").click()
    assert page.locator(".import-confirm").is_hidden()
    page.locator("[data-act=discard]").click()
    page.locator("[data-act=discard-yes]").click()
    page.locator(".import-status", has_text="Discarded this import.").wait_for()
    assert con.execute("SELECT status FROM import_batch").fetchone()[0] == "discarded"
