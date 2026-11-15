"""Playwright smoke test of the Setlists panel (post-v1 #43) at phone width (412 px): an editor pastes a setlist for one
act, previews it (the songs new to that act are tagged), saves it with a source and sees it grouped into the set and
the encore; a visitor sees the same songs without the source or any button; the editor then deletes it with the
inline "Delete? Yes / No". Same setup as test_show_ui.py; skips without Playwright or Chromium unless
STUBS_UI_REQUIRED=1."""
import os
import threading

import pytest

import app as stubs
import records
from conftest import WRITE_KEY, mk_artist, mk_show, mk_venue

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PHONE = {"width": 412, "height": 915}
PASTE = "1. Lantern Light >\n2. Paper Boats (with a guest)\n\nEncore:\n3. Night Ferry\n"

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
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url)
    return page


def assert_the_setlist(act):
    expect(act.locator(".setlists-set")).to_have_count(2)
    assert act.locator(".setlists-title").all_inner_texts() == ["Lantern Light", "Paper Boats", "Night Ferry"]
    assert act.locator(".setlists-set-name").all_inner_texts() == ["Encore"]
    assert act.locator(".setlists-segue").count() == 1
    assert act.locator(".setlists-note").inner_text() == "with a guest"


def test_editor_pastes_previews_saves_and_deletes_a_setlist(server, browser, con):
    v = mk_venue(con, "Granite Hall", city="Oakland", region="CA", country="US")
    show = mk_show(con, "The Paper Lanterns", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))
    records.get_or_create_song(con, mk_artist(con, "The Paper Lanterns"), "Lantern Light")  # heard before

    page = open_page(browser, f"{server}/admin#/show/{show}", {"X-API-Key": WRITE_KEY})
    panel = page.locator('[data-panel="Setlists"]')
    expect(panel.locator("section.setlists-act")).to_have_count(2)
    expect(page.locator('[data-panel="Setlist"]')).to_be_attached()
    panels = page.locator("section.show-panel h2").all_inner_texts()
    assert panels.index("Setlists") < panels.index("Setlist")  # order 15, before setlist.fm's 20
    act = panel.locator("section.setlists-act", has_text="The Paper Lanterns")
    act.get_by_role("button", name="Add setlist").click()
    act.locator("textarea").fill(PASTE)
    act.get_by_role("button", name="Preview").click()
    expect(act.locator(".setlists-count")).to_have_text("3 songs · 2 new to The Paper Lanterns")
    assert act.locator(".setlists-preview .setlists-new").count() == 2
    assert con.execute("SELECT COUNT(*) FROM setlist").fetchone()[0] == 0  # nothing until Save

    act.get_by_label("Source").fill("Lantern fan club")
    act.get_by_role("button", name="Save").click()
    expect(act.locator(".setlists-source")).to_have_text("From Lantern fan club")
    assert_the_setlist(act)
    assert con.execute("SELECT COUNT(*) FROM setlist_entry").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM song").fetchone()[0] == 3
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []

    visitor = open_page(browser, f"{server}/#/show/{show}")
    vpanel = visitor.locator('[data-panel="Setlists"]')
    expect(vpanel.locator("section.setlists-act")).to_have_count(1)
    assert vpanel.locator("h3").all_inner_texts() == ["The Paper Lanterns"]
    assert_the_setlist(vpanel)
    assert vpanel.locator("button").count() == 0 and "fan club" not in vpanel.inner_text()
    assert visitor.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert visitor.errors == []

    act.get_by_role("button", name="Delete").click()
    act.get_by_role("button", name="No").click()
    expect(act.get_by_role("button", name="Delete")).to_be_visible()
    act.get_by_role("button", name="Delete").click()
    act.get_by_role("button", name="Yes").click()
    expect(act.get_by_text("No setlist yet.")).to_be_visible()
    assert con.execute("SELECT COUNT(*) FROM setlist").fetchone()[0] == 0
    assert page.errors == []


def test_a_show_without_setlists_has_no_setlists_panel_for_visitors(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    show = mk_show(con, "Cold Harbor", "2016-12-01", v)
    page = open_page(browser, f"{server}/#/show/{show}")
    expect(page.locator('[data-panel="Setlist"]')).to_be_attached()  # drawn after ours, so ours is done
    expect(page.locator('[data-panel="Setlists"]')).to_be_hidden()
    assert page.errors == []
