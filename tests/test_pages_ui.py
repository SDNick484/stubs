"""Playwright smoke test of the artist, venue, year and city pages at phone width (412 px). Same setup as
test_merge_ui.py; skips without Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
import os
import threading

import pytest

import app as stubs
import common
from conftest import WRITE_KEY, mk_artist, mk_show, mk_venue

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


def clean(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []


def test_artist_page_shows_the_opener_role(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2005-04-02", v, lineup=(("Cold Harbor", "headliner"), ("Marigold Static", "opener")))
    mk_show(con, "Marigold Static", "2016-12-01", v)
    aid = mk_artist(con, "Marigold Static")
    page = open_page(browser, f"{server}/#/artist/{aid}")
    page.wait_for_selector(".show-row")
    assert page.locator("h1").inner_text() == "Marigold Static"
    assert page.locator(".summary").inner_text().startswith("Seen twice")
    rows = page.locator(".show-row")
    assert rows.count() == 2
    assert rows.nth(1).locator(".show-role").inner_text() == "Opener"
    assert rows.nth(0).locator(".show-role").inner_text() == "Headliner"
    clean(page)


def test_venue_page_shows_a_former_name(server, browser, con):
    v = mk_venue(con, "Granite Hall", city="Oakland", region="CA")
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)",
                (v, "Old Armory", common.venue_norm("Old Armory")))
    mk_show(con, "Cold Harbor", "2009-06-20", v, venue_label="Old Armory")
    page = open_page(browser, f"{server}/#/venue/{v}")
    page.wait_for_selector(".show-row")
    assert page.locator("h1").inner_text() == "Granite Hall"
    assert "Also known as Old Armory" in page.locator(".aka").inner_text()
    assert page.locator(".summary a[href='#/city/US/CA/Oakland']").count() == 1
    clean(page)


def test_year_page_lists_that_year(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Marigold Static", "2010-01-02", v)
    page = open_page(browser, f"{server}/#/year/2009")
    page.wait_for_selector(".show-row")
    assert page.locator("h1").inner_text() == "2009"
    assert page.locator(".show-row").count() == 1
    assert "Cold Harbor" in page.locator(".show-row").inner_text()
    clean(page)


def test_city_page_with_no_region(server, browser, con):
    v = mk_venue(con, "Salle Rose", city="Lyon", region="", country="FR")
    mk_show(con, "The Paper Lanterns", "2012-05-05", v)
    page = open_page(browser, f"{server}/#/city/FR/-/Lyon")
    page.wait_for_selector(".show-row")
    assert page.locator("h1").inner_text() == "Lyon, FR"
    assert page.locator(".show-row").count() == 1
    clean(page)


def test_editor_sees_upcoming_in_admin(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Cold Harbor", "2027-01-02", v, status="upcoming")
    aid = mk_artist(con, "Cold Harbor")
    page = open_page(browser, f"{server}/admin#/artist/{aid}", {"X-API-Key": WRITE_KEY})
    page.wait_for_selector(".show-row")
    assert page.locator(".show-row.is-upcoming").count() == 1
    public = open_page(browser, f"{server}/#/artist/{aid}")
    public.wait_for_selector(".show-row")
    assert public.locator(".show-row").count() == 1


def test_unknown_artist_is_not_found(server, browser):
    page = open_page(browser, f"{server}/#/artist/999")
    page.wait_for_selector("text=There's nothing at this address.")
