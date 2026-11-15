"""Playwright smoke test of the MusicBrainz links at phone width (412 px): link one artist from its page, and suggest and
link from #/admin/artists. MusicBrainz is a recorded fixture (urlopen and sleep patched); nothing touches the network.
Same setup as test_merge_ui.py: a live werkzeug server on a free loopback port and the test database; skips without
Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
import io
import os
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

import app as stubs
import musicbrainz
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


FIX = os.path.join(os.path.dirname(__file__), "fixtures", "musicbrainz")
HARBOR = "11111111-1111-4111-8111-111111111111"
LANTERNS = "33333333-3333-4333-8333-333333333333"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(monkeypatch):
    """Answers by the artist asked about: Cold Harbor gets hit.json (a top match at 100 and another at 86), The Paper
    Lanterns exact.json, anyone else a miss."""
    def urlopen(req, timeout=None):
        q = parse_qs(urlsplit(req.full_url).query)["query"][0]
        name = {'artist:"Cold Harbor"': "hit.json", 'artist:"Cold Harbour"': "hit.json", 'artist:"The Paper Lanterns"': "exact.json"}.get(q, "miss.json")
        with open(os.path.join(FIX, name), "rb") as f:
            return Resp(f.read())

    monkeypatch.setattr(musicbrainz.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(musicbrainz.time, "sleep", lambda s: None)
    monkeypatch.setattr(musicbrainz, "_last_call", None)


def test_link_one_artist_from_its_page(server, browser, con, net):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    a = mk_artist(con, "Cold Harbor")

    page = open_page(browser, f"{server}/admin#/artist/{a}", {"X-API-Key": WRITE_KEY})
    page.get_by_role("button", name="Link to MusicBrainz").click()
    cands = page.locator(".mb-candidate")
    cands.nth(1).wait_for()
    assert cands.count() == 2
    assert "UK post-punk band" in cands.nth(0).inner_text()
    cands.nth(0).get_by_role("button", name="Use").click()
    link = page.locator("a.mb-link")
    link.wait_for()
    assert link.get_attribute("href") == f"https://musicbrainz.org/artist/{HARBOR}"
    page.get_by_role("button", name="Change").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert con.execute("SELECT mbid FROM artist WHERE id = ?", (a,)).fetchone()[0] == HARBOR
    assert page.errors == []

    page.get_by_role("button", name="Unlink").click()
    page.get_by_role("button", name="Yes").click()
    page.get_by_role("button", name="Link to MusicBrainz").wait_for()
    assert page.locator("a.mb-link").count() == 0
    assert con.execute("SELECT mbid FROM artist WHERE id = ?", (a,)).fetchone()[0] is None


def test_suggest_for_all_then_link_all_exact(server, browser, con, net):
    v = mk_venue(con, "Granite Hall")
    for name in ("Cold Harbor", "The Paper Lanterns", "Nobody Special"):
        mk_show(con, name, "2009-06-20", v)

    page = open_page(browser, f"{server}/admin#/admin/artists", {"X-API-Key": WRITE_KEY})
    page.get_by_role("button", name="Suggest for all unlinked").click()
    page.locator(".mb-row", has_text="Nobody Special").get_by_text("No match.").wait_for()
    assert page.locator(".mb-row").count() == 3
    assert "UK post-punk band" in page.locator(".mb-row", has_text="Cold Harbor").inner_text()
    page.get_by_role("button", name="Link all exact").click()
    page.get_by_text("Linked 2 artists.").wait_for()
    assert page.locator(".mb-row").count() == 1
    got = dict(con.execute("SELECT name, mbid FROM artist").fetchall())
    assert got == {"Cold Harbor": HARBOR, "The Paper Lanterns": LANTERNS, "Nobody Special": None}
    assert page.errors == []


def test_the_public_page_links_to_musicbrainz_without_controls(server, browser, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    a = mk_artist(con, "Cold Harbor")
    con.execute("UPDATE artist SET mbid = ? WHERE id = ?", (HARBOR, a))
    page = open_page(browser, f"{server}/#/artist/{a}")
    page.locator("a.mb-link").wait_for()
    assert page.locator(".mb-controls").count() == 0


def test_a_taken_id_offers_the_merge_screen_with_both_preselected(server, browser, con, net):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Cold Harbour", "2010-06-20", v)
    keep, dup = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    con.execute("UPDATE artist SET mbid = ? WHERE id = ?", (HARBOR, keep))

    page = open_page(browser, f"{server}/admin#/artist/{dup}", {"X-API-Key": WRITE_KEY})
    page.get_by_role("button", name="Link to MusicBrainz").click()
    page.locator(".mb-candidate").nth(0).get_by_role("button", name="Use").click()
    page.get_by_role("link", name="Merge them").click()
    question = page.locator(".merge[data-kind=artists] .merge-question")
    question.get_by_text("Merge Cold Harbour into Cold Harbor?").wait_for()
    assert all("409" in e for e in page.errors)  # the refused link is logged by the browser, nothing else
