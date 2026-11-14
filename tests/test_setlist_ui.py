"""Playwright smoke test of the Setlist panel at phone width (412 px): an editor runs "Find setlists" on a show with two
acts, confirms both, and sees two sets, each under its act with its own setlist.fm attribution; a visitor then sees
the same two sets. setlist.fm is a recorded fixture (setlistfm.urlopen is replaced in this process, where the test
server runs). Same setup as test_show_ui.py; skips without Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
import io
import os
import threading

import pytest

import app as stubs
import setlistfm
import settings
from conftest import WRITE_KEY, mk_show, mk_venue

REQUIRED = os.environ.get("STUBS_UI_REQUIRED") == "1"
PHONE = {"width": 412, "height": 915}
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "setlistfm")
URL = "https://www.setlist.fm/setlist/the-paper-lanterns/2025/granite-hall-oakland-ca-63ab1c4e.html"
OPENER_URL = "https://www.setlist.fm/setlist/marigold-static/2025/granite-hall-oakland-ca-2b7f9a10.html"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    if REQUIRED:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def setlistfm_net(monkeypatch):
    """setlist.fm from recorded fixtures: the venue search, then each act's setlist. Records the paths asked for."""
    routes = {"/search/setlists": "venue_search.json", "/setlist/63ab1c4e": "setlist.json",
              "/setlist/2b7f9a10": "setlist_opener.json"}
    calls = []

    def fake_urlopen(req, timeout=None):
        path = req.full_url[len(setlistfm.API):]
        calls.append(path)
        for prefix, name in routes.items():
            if path.startswith(prefix):
                return Resp(fixture(name))
        raise AssertionError(f"unexpected request {req.full_url}")

    monkeypatch.setattr(setlistfm, "urlopen", fake_urlopen)
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "test-setlist-key")
    setlistfm._cache.clear()
    yield calls
    setlistfm._cache.clear()


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


def assert_two_sets(page):
    panel = page.locator('[data-panel="Setlist"]')
    acts = panel.locator("section.setlist-act")
    acts.nth(1).wait_for()
    assert acts.count() == 2
    assert panel.locator("section.setlist-act h3").all_inner_texts() == ["Marigold Static", "The Paper Lanterns"]
    assert "Static Bloom" in acts.nth(0).inner_text() and "Lantern Light" in acts.nth(1).inner_text()
    credits = [acts.nth(i).locator(".setlist-credit a") for i in range(2)]
    assert [c.get_attribute("href") for c in credits] == [OPENER_URL, URL]
    assert all(c.inner_text() == "Setlist from setlist.fm" and "nofollow" not in (c.get_attribute("rel") or "")
               for c in credits)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_editor_finds_and_confirms_setlists_for_two_acts(server, browser, con, setlistfm_net):
    v = mk_venue(con, "Granite Hall", city="Oakland", region="CA", country="US")
    show = mk_show(con, "The Paper Lanterns + Marigold Static", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))

    page = open_page(browser, f"{server}/admin#/show/{show}", {"X-API-Key": WRITE_KEY})
    panel = page.locator('[data-panel="Setlist"]')
    panel.get_by_role("button", name="Find setlists").click()
    form = panel.locator("form.setlist-matches")
    form.wait_for()
    assert form.locator("fieldset legend").all_inner_texts() == ["Marigold Static", "The Paper Lanterns"]
    assert form.locator("input[type=radio]:checked").evaluate_all("els => els.map(e => e.value)") == \
        ["2b7f9a10", "63ab1c4e"]
    assert "Cold Harbor" in form.locator(".setlist-unmatched").inner_text()  # listed, not offered
    assert con.execute("SELECT COUNT(*) FROM setlist_link").fetchone()[0] == 0  # nothing until confirmed

    form.get_by_role("button", name="Link selected").click()
    assert_two_sets(page)
    assert con.execute("SELECT COUNT(*) FROM setlist_link").fetchone()[0] == 2
    assert panel.get_by_role("button", name="Find setlists").is_visible()
    assert page.errors == []

    visitor = open_page(browser, f"{server}/#/show/{show}")
    assert_two_sets(visitor)
    assert visitor.locator('[data-panel="Setlist"] button').count() == 0
    assert visitor.errors == []
