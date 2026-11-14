"""Playwright smoke test of #/admin/merge at phone width (412 px): pick the artist to keep and the duplicate, read the
confirm line, merge, and see the result; a visitor gets "Editors only". Same setup as test_shell_ui.py (a live
werkzeug server on a free loopback port and the test database); skips without Playwright or Chromium unless
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


def pick(section, slot, query, name):
    section.locator(f".merge-search[data-slot='{slot}']").fill(query)
    section.locator(f".merge-slot[data-slot='{slot}'] .merge-pick", has_text=name).click()


def test_merge_two_artists(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    for day in ("2010-01-02", "2011-02-03", "2012-03-04"):
        mk_show(con, "Cold Harbour", day, v)

    page = open_page(browser, server + "/admin#/admin/merge", {"X-API-Key": WRITE_KEY})
    page.locator("#nav a", has_text="Merge").wait_for()
    artists = page.locator(".merge[data-kind='artists']")
    pick(artists, "keep", "cold harbor", "Cold Harbor")
    pick(artists, "drop", "harbour", "Cold Harbour")
    question = artists.locator(".merge-question")
    question.wait_for()
    assert question.inner_text() == "Merge Cold Harbour into Cold Harbor? 3 shows move."

    artists.locator("[data-act='merge']").click()
    artists.locator(".merge-status", has_text="Merged Cold Harbour into Cold Harbor. It now has 4 shows.").wait_for()
    assert artists.locator(".merge-confirm").is_hidden()
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []


def test_same_venue_twice_is_refused_in_the_page(server, browser, con):
    mk_venue(con, "The Bellwether")
    page = open_page(browser, server + "/admin#/admin/merge", {"X-API-Key": WRITE_KEY})
    venues = page.locator(".merge[data-kind='venues']")
    venues.wait_for()
    pick(venues, "keep", "bell", "The Bellwether")
    pick(venues, "drop", "bell", "The Bellwether")
    venues.locator(".merge-status", has_text="Pick two different venues.").wait_for()
    assert venues.locator(".merge-confirm").is_hidden()
    assert page.errors == []


def test_merge_screen_for_a_visitor(server, browser):
    page = open_page(browser, server + "/#/admin/merge")
    page.wait_for_selector("text=Editors only")
    assert page.locator("#nav a", has_text="Merge").count() == 0
    assert page.errors == []
