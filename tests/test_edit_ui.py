"""Playwright smoke test of the show editor (#/admin/show/<id>/edit and #/admin/show/new) at phone width (412 px): add
an opener and save; two pages on one show, where the second save is stale and keeps what was typed under the message
and a Reload button; create a show at a new venue; sources; delete with the inline "Delete? Yes / No". Same setup as
test_merge_ui.py (a live werkzeug server on a free loopback port and the test database); skips without Playwright or
Chromium unless STUBS_UI_REQUIRED=1."""
import os
import threading
import time

import pytest

import app as stubs
from conftest import WRITE_KEY, mk_show, mk_venue

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PHONE = {"width": 412, "height": 915}
EDITOR = {"X-API-Key": WRITE_KEY}
STALE = "Someone else just changed this show. Reload to see it."

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


def open_editor(browser, server, sid):
    page = open_page(browser, f"{server}/admin#/admin/show/{sid}/edit", EDITOR)
    page.locator(".edit-form").wait_for()
    return page


def fits(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.fixture
def show(con):
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "Marigold Static", "2019-05-04", v)


def settled(fn, want, timeout=5.0):
    """fn() once it returns want (the server thread may still be committing), else its last answer."""
    end = time.monotonic() + timeout
    got = fn()
    while got != want and time.monotonic() < end:
        time.sleep(0.05)
        got = fn()
    return got


def lineup(con, sid):
    return [tuple(r) for r in con.execute(
        "SELECT a.name, l.role FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? "
        "ORDER BY l.position", (sid,))]


def test_add_an_opener_and_save(server, browser, con, show):
    page = open_editor(browser, server, show)
    assert page.locator("#nav a", has_text="+ Show").count() == 1
    assert page.locator("input[name=billing]").input_value() == "Marigold Static"
    page.locator("[data-act=add-act]").click()
    added = page.locator(".lineup-row").nth(1)
    added.locator("input[name=name]").fill("Cold Harbor")
    added.locator("select[name=role]").select_option("opener")
    added.locator("input[name=likely]").check()
    added.locator("input[name=note]").fill("only on the poster")
    page.locator(".edit-form button[type=submit]").click()
    page.locator(".edit-status", has_text="Saved.").wait_for()
    assert lineup(con, show) == [("Marigold Static", "headliner"), ("Cold Harbor", "opener")]
    assert con.execute("SELECT confidence, confidence_note FROM lineup WHERE position = 2").fetchone()[:] == \
        ("likely", "only on the poster")
    assert con.execute("SELECT version FROM show WHERE id = ?", (show,)).fetchone()[0] == 2
    # The saved entry now carries its id, so moving it up and saving again keeps it (no new entry).
    page.locator(".lineup-row").nth(1).locator("[data-act=up]").click()
    page.locator(".edit-form button[type=submit]").click()
    want = [("Cold Harbor", "opener"), ("Marigold Static", "headliner")]
    assert settled(lambda: lineup(con, show), want) == want
    page.locator(".edit-status", has_text="Saved.").wait_for()
    assert con.execute("SELECT COUNT(*) FROM lineup").fetchone()[0] == 2
    assert fits(page)
    assert page.errors == []


def test_stale_save_keeps_the_form_and_offers_reload(server, browser, con, show):
    first = open_editor(browser, server, show)
    second = open_editor(browser, server, show)
    first.locator("input[name=billing]").fill("Marigold Static live")
    first.locator(".edit-form button[type=submit]").click()
    first.locator(".edit-status", has_text="Saved.").wait_for()

    second.locator("input[name=billing]").fill("Typed in the second page")
    second.locator("textarea[name=notes]").fill("with Riley")
    second.locator(".edit-form button[type=submit]").click()
    second.locator(".edit-error", has_text=STALE).wait_for()
    assert second.locator("input[name=billing]").input_value() == "Typed in the second page"
    assert second.locator("textarea[name=notes]").input_value() == "with Riley"
    assert con.execute("SELECT billing, notes FROM show WHERE id = ?", (show,)).fetchone()[:] == \
        ("Marigold Static live", None)

    second.locator(".edit-error [data-act=reload]").click()
    billing = second.locator("input[name=billing]")
    assert settled(billing.input_value, "Marigold Static live") == "Marigold Static live"
    assert second.locator(".edit-error").count() == 0 or second.locator(".edit-error").is_hidden()
    assert fits(second)
    assert first.errors == []
    # The browser itself logs the refused request; nothing else may go wrong.
    assert [e for e in second.errors if "409 (CONFLICT)" not in e] == []


def test_create_a_show_at_a_new_venue(server, browser, con):
    mk_venue(con, "Granite Hall")
    page = open_page(browser, f"{server}/admin#/admin/show/new", EDITOR)
    page.locator("#nav a", has_text="+ Show").wait_for()
    form = page.locator(".edit-form")
    form.wait_for()
    form.locator("input[name=date]").fill("2024-08-17")
    form.locator("input[name=billing]").fill("Cold Harbor")
    form.locator("[data-act=new-venue]").click()
    form.locator("input[name=new_venue]").fill("The Rio Theatre")
    form.locator("input[name=city]").fill("Santa Cruz")
    form.locator("input[name=region]").fill("CA")
    form.locator("input[name=country]").fill("US")
    form.locator(".lineup-row").first.locator("input[name=name]").fill("Cold Harbor")
    form.locator("button[type=submit]").click()
    page.wait_for_url("**/admin#/show/*")
    sid = int(page.evaluate("location.hash").split("/")[-1])
    row = con.execute("SELECT s.billing, s.date, s.status, s.venue_label, v.city FROM show s "
                      "JOIN venue v ON v.id = s.venue_id WHERE s.id = ?", (sid,)).fetchone()
    assert tuple(row) == ("Cold Harbor", "2024-08-17", "attended", "The Rio Theatre", "Santa Cruz")
    assert lineup(con, sid) == [("Cold Harbor", "headliner")]
    assert page.errors == []


def test_pick_an_existing_venue(server, browser, con, show):
    mk_venue(con, "The Bellwether", city="Portland", region="OR")
    page = open_editor(browser, server, show)
    page.locator(".venue-search").fill("bellw")
    page.locator(".venue-option", has_text="The Bellwether").click()
    assert page.locator("input[name=venue_label]").input_value() == "The Bellwether"
    page.locator(".edit-form button[type=submit]").click()
    page.locator(".edit-status", has_text="Saved.").wait_for()
    assert con.execute("SELECT v.name FROM show s JOIN venue v ON v.id = s.venue_id WHERE s.id = ?",
                       (show,)).fetchone()[0] == "The Bellwether"
    assert page.errors == []


def test_sources_and_delete(server, browser, con, show):
    page = open_editor(browser, server, show)
    sources = page.locator(".edit-sources")
    sources.locator("input[name=label]").fill("Ticketmaster")
    sources.locator("input[name=url]").fill("https://concerts.example.com/t/1")
    sources.locator("button[type=submit]").click()
    sources.locator(".source-row", has_text="Ticketmaster").wait_for()
    assert con.execute("SELECT label, lineup_id FROM source").fetchone()[:] == ("Ticketmaster", None)

    sources.locator(".source-row [data-act=delete]").click()
    sources.locator(".source-row", has_text="Delete?").wait_for()
    sources.locator("[data-act=delete-yes]").click()
    sources.locator(".source-row").first.wait_for(state="detached")
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0

    zone = page.locator(".edit-delete")
    zone.locator("[data-act=delete]").click()
    zone.locator("text=Delete?").wait_for()
    zone.locator("[data-act=delete-no]").click()
    assert zone.locator("[data-act=delete-yes]").count() == 0
    zone.locator("[data-act=delete]").click()
    zone.locator("[data-act=delete-yes]").click()
    page.wait_for_url("**/admin#/")
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 0
    assert page.errors == []


def test_editor_screens_for_a_visitor(server, browser, show):
    for path in (f"/#/admin/show/{show}/edit", "/#/admin/show/new"):
        page = open_page(browser, server + path)
        page.wait_for_selector("text=Editors only")
        assert page.locator("#nav a", has_text="+ Show").count() == 0
        assert page.errors == []
