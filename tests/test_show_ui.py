"""Playwright smoke test of #/show/<id> at phone width (412 px): the lineup, Previous/Next, panels in order (one that
fails does not break the page), and an upcoming show that is "Show not found." for a visitor but visible to an editor.
Same setup as test_shell_ui.py; skips without Playwright or Chromium unless STUBS_UI_REQUIRED=1."""
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


REGISTER = """async ([ok, bad]) => {
  const { registerShowPanel } = await import("/static/js/registry.js");
  registerShowPanel("Zed test", { order: 5, render: el => { el.textContent = "zed ran"; } });
  registerShowPanel("Boom test", { order: 6, render: () => { throw new Error("boom"); } });
  registerShowPanel("Late test", { order: 99, render: async el => { el.textContent = "late ran"; } });
}"""


def seed(con):
    v = mk_venue(con, "Granite Hall", city="Oakland", region="CA", country="US")
    a = mk_show(con, "Cold Harbor", "2024-05-01", v,
                lineup=(("Cold Harbor", "headliner"), ("Marigold Static", "opener")))
    b = mk_show(con, "The Paper Lanterns", "2024-06-01", v)
    up = mk_show(con, "Future Band", "2027-01-01", v, status="upcoming")
    return a, b, up


def test_show_renders_lineup_and_steps(server, browser, con):
    a, b, _ = seed(con)
    page = open_page(browser, f"{server}/#/show/{a}")
    page.locator("h1", has_text="Cold Harbor").wait_for()
    assert page.locator(".lineup-group h3").all_inner_texts() == ["Headliner", "Openers"]
    assert page.locator(".show-where a").nth(0).get_attribute("href") == f"#/venue/{con.execute('SELECT venue_id FROM show WHERE id=?', (a,)).fetchone()[0]}"
    assert page.locator(".show-where a").nth(1).get_attribute("href") == "#/city/US/CA/Oakland"
    page.locator(".show-step a", has_text="Next").click()
    page.locator("h1", has_text="The Paper Lanterns").wait_for()
    assert page.url.endswith(f"#/show/{b}")
    assert page.errors == []


def test_panels_in_order_and_a_failing_one_does_not_break_the_page(server, browser, con):
    a, _, _ = seed(con)
    page = open_page(browser, f"{server}/#/")
    page.wait_for_selector("#nav a")
    page.evaluate(REGISTER, [1, 2])
    page.goto(f"{server}/#/show/{a}")
    page.locator("h1", has_text="Cold Harbor").wait_for()
    page.locator(".show-panel[data-panel='Late test']").wait_for()
    names = page.locator(".show-panel").evaluate_all("els => els.map(e => e.dataset.panel)")
    assert names.index("Zed test") < names.index("Boom test") < names.index("Pictures") < names.index("Late test")
    assert page.locator(".show-panel[data-panel='Zed test']").inner_text().endswith("zed ran")
    assert "isn't available" in page.locator(".show-panel[data-panel='Boom test']").inner_text()
    assert page.locator(".show-step").is_visible()


def test_upcoming_show_is_not_found_for_a_visitor_and_visible_to_an_editor(server, browser, con):
    _, _, up = seed(con)
    visitor = open_page(browser, f"{server}/#/show/{up}")
    visitor.locator(".notice", has_text="Show not found.").wait_for()
    assert "Future Band" not in visitor.content()

    editor = open_page(browser, f"{server}/admin#/show/{up}", {"X-API-Key": WRITE_KEY})
    editor.locator("h1", has_text="Future Band").wait_for()
    assert editor.locator(".tag-upcoming").is_visible()
