"""Playwright smoke test of concert watch (post-v1 #47) at phone width (412 px): add a band by name on #/admin/watch,
see a seeded sighting on the "New near you" card, Add it as upcoming and find it under "Coming up". Nothing calls an
outside service (no check is run). Same setup as test_merge_ui.py: a live werkzeug server on a free loopback port and
the test database; skips without Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
import os
import threading

import pytest

import app as stubs
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


def no_side_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_add_a_band_then_add_a_sighting_as_upcoming(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Velvet Arcade", "2009-06-20", v)
    page = open_page(browser, f"{server}/admin#/admin/watch", {"X-API-Key": WRITE_KEY})

    form = page.locator("form[data-form=add]")
    form.get_by_label("Name").fill("Cold Harbor")
    form.get_by_role("button", name="Watch", exact=True).click()
    row = page.locator(".watch-row", has_text="Cold Harbor")
    row.wait_for()
    assert "Never seen" in row.inner_text()
    assert "Velvet Arcade" in page.locator(".watch-section", has_text="From your shows").inner_text()
    assert no_side_scroll(page)
    harbor = mk_artist(con, "Cold Harbor")
    assert con.execute("SELECT origin FROM watch WHERE artist_id = ?", (harbor,)).fetchone()[0] == "manual"

    marigold = mk_artist(con, "Marigold Static")
    con.execute("INSERT INTO watch (artist_id, origin) VALUES (?, 'manual')", (marigold,))
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city, region, country, url) "
                "VALUES (?, 'ticketmaster', 'Z7MS0001', '2099-03-14', 'Old Mill Theatre', 'Portland', 'OR', 'US', "
                "'https://concerts.example.com/tm/Z7MS0001')", (marigold,))
    page.goto(f"{server}/admin#/admin")
    card = page.locator("section.card[data-card='New near you']")
    card.get_by_text("New near you (1)").wait_for()
    assert "Mar 14, 2099 · Old Mill Theatre, Portland" in card.inner_text()
    assert no_side_scroll(page)
    card.get_by_role("button", name="Add as upcoming").click()
    card.get_by_text("Added.").wait_for()

    page.reload()
    coming = page.locator("section.card[data-card='Coming up']")
    coming.get_by_text("Marigold Static").wait_for()
    assert "Old Mill Theatre" in coming.inner_text()
    assert page.locator("section.card[data-card='New near you']").count() == 0

    page.goto(f"{server}/admin#/artist/{harbor}")
    page.get_by_role("button", name="Watching").wait_for()
    assert no_side_scroll(page)
    assert page.errors == []
