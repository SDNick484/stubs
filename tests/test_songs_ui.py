"""Playwright smoke test of the song pages (post-v1 #44) at phone width (412 px): a visitor goes from the artist page's
"Songs heard" to a song page and its shows; an editor pastes a catalog (the never-heard list appears) and merges one song
into another with the inline "Merge 'X' into 'Y'? 2 plays move." Same setup as test_pages_ui.py; skips without
Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
import os
import threading

import pytest

import app as stubs
from conftest import WRITE_KEY, mk_artist, mk_show, mk_venue
from test_songs import play

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


@pytest.fixture
def seeded(con):
    v = mk_venue(con, "Granite Hall")
    a = mk_show(con, "The Paper Lanterns", "2019-05-04", v)
    b = mk_show(con, "The Paper Lanterns", "2021-06-05", v)
    play(con, a, "The Paper Lanterns", ["Lantern Light", "Lantern Lite", "Night Ferry"])
    play(con, b, "The Paper Lanterns", [("Lantern Light", None, 1, "with strings")])
    return mk_artist(con, "The Paper Lanterns")


def test_a_visitor_goes_from_the_artist_page_to_a_song_page(server, browser, con, seeded):
    page = open_page(browser, f"{server}/#/artist/{seeded}")
    page.wait_for_selector(".artist-songs .song-list")
    heard = page.locator(".artist-songs .song-list li")
    assert heard.first.inner_text().startswith("Lantern Light")
    assert "Heard twice" in heard.first.inner_text()
    assert page.locator(".artist-songs h2").all_inner_texts() == ["Songs heard", "Never heard"]
    assert page.locator(".artist-songs button, .artist-songs form").count() == 0  # the public gets no tools
    heard.first.locator("a").click()
    page.wait_for_selector("h1:has-text('Lantern Light')")
    assert "Heard twice" in page.locator(".summary").inner_text()
    assert "first May 4, 2019" in page.locator(".summary").inner_text()
    rows = page.locator(".song-play")
    assert rows.count() == 2
    assert "Encore · with strings" in rows.first.inner_text()
    assert page.locator("button").count() == 0
    page.locator(".song-play a").first.click()
    page.wait_for_selector(".show-lineup, h1")
    assert page.url.split("#")[1].startswith("/show/")
    clean(page)


def test_an_editor_pastes_a_catalog_and_merges_two_songs(server, browser, con, seeded):
    page = open_page(browser, f"{server}/admin#/artist/{seeded}", {"X-API-Key": WRITE_KEY})
    page.wait_for_selector(".song-catalog-form")
    page.fill(".song-catalog-form textarea", "Album: Low Tide\n1. Moth Orchard\n2. Lantern Light\n")
    page.click(".song-catalog-form button[type=submit]")
    page.wait_for_selector(".artist-songs li:has-text('Moth Orchard')")
    never = page.locator(".artist-songs h2:has-text('Never heard') + ul li")
    assert never.all_inner_texts() == ["Moth Orchard Remove"]
    page.locator(".artist-songs a:has-text('Lantern Lite')").click()
    page.wait_for_selector("h1:has-text('Lantern Lite')")
    page.click("button:has-text('Merge into')")
    page.fill(".merge-search", "light")
    page.locator(".merge-pick:has-text('Lantern Light')").click()
    assert page.locator("[data-slot=confirm]").inner_text().startswith("Merge 'Lantern Lite' into 'Lantern Light'? 1 play moves.")
    page.click("button[data-act=merge-no]")
    assert page.locator("[data-slot=confirm]").is_hidden()
    page.locator(".merge-pick:has-text('Lantern Light')").click()
    page.click("button[data-act=merge-yes]")
    page.wait_for_selector("h1:has-text('Lantern Light')")
    page.wait_for_selector(".song-play >> nth=2")
    assert "Heard 3 times" in page.locator(".summary").inner_text()
    assert "Also known as lantern lite" in page.locator(".aka.muted").inner_text()
    clean(page)
