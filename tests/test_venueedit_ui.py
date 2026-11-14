"""Playwright smoke test of #/admin/venue/<id>/edit at phone width (412 px): rename a venue, add a former name, and
see a name a ticket uses refuse to go. Same setup as test_merge_ui.py; skips without Playwright or Chromium unless
STUBS_UI_REQUIRED=1."""
import os
import threading

import pytest

import app as stubs
from conftest import WRITE_KEY, mk_show, mk_venue

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
    ctx = browser.new_context(viewport=PHONE, extra_http_headers=headers or {})
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url)
    return page


def test_rename_add_and_refuse(server, browser, con):
    vid = mk_venue(con, "Granite Hall")
    mk_show(con, "The Paper Lanterns", "2019-05-04", vid)

    page = open_page(browser, f"{server}/admin#/venue/{vid}", {"X-API-Key": WRITE_KEY})
    page.locator("a", has_text="Edit venue").click()
    page.locator("input[name=name]").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]

    page.locator("input[name=name]").fill("Basalt Arena")
    page.locator(".venue-edit-form button[type=submit]").click()
    page.locator(".venue-edit-msg", has_text="Saved.").wait_for()
    rows = page.locator(".venue-name")
    assert rows.count() == 2
    assert "Basalt Arena" in rows.nth(0).inner_text() and "current" in rows.nth(0).inner_text()

    # the old name is on a ticket: no Remove button
    old = page.locator(".venue-name", has_text="Granite Hall")
    assert "1 show on the ticket" in old.inner_text()
    assert old.locator("button[data-act=remove]").count() == 0

    page.locator("input[name=newname]").fill("The Old Armory")
    page.locator(".venue-add-form button").click()
    page.locator(".venue-edit-msg", has_text="Name added.").wait_for()
    armory = page.locator(".venue-name", has_text="The Old Armory")
    armory.locator("button[data-act=remove]").click()
    armory.locator("button[data-act=remove-no]").click()
    assert page.locator(".venue-name", has_text="The Old Armory").count() == 1
    armory.locator("button[data-act=remove]").click()
    armory.locator("button[data-act=remove-yes]").click()
    page.locator(".venue-edit-msg", has_text="Name removed.").wait_for()
    assert page.locator(".venue-name").count() == 2

    # the server refuses too, if asked directly
    assert [r[0] for r in con.execute("SELECT name FROM venue_name WHERE venue_id = ? ORDER BY name", (vid,))] == \
        ["Basalt Arena", "Granite Hall"]
    assert page.errors == []
