"""Task 19: fetch from a URL (spec 7.12) and its SSRF guard (review focus 5).

The network is never touched. `socket.getaddrinfo` is replaced by a table of invented answers (anything not in it
resolves to PUBLIC, so a guard that leaned on the resolver to catch a disguised address would fail here), and
`fetchurl._open` (the one place a connection is made) by `Web`, which serves the fixtures in tests/fixtures/pages/ and
records every connection it was asked for. Where a test needs the real connection code it runs over a socketpair."""
import os
import socket
import threading
from contextlib import contextmanager

import pytest

import fetchurl
import settings
from common import ApiError
from conftest import mk_show, mk_venue

PAGES = os.path.join(os.path.dirname(__file__), "fixtures", "pages")
# A globally routable address for invented public hosts to resolve to (IANA's example.com address).
PUBLIC = "93.184.216.34"
PAGE = "https://tickets.example.com/events/2009/cold-harbor"
OG_PAGE = "https://tickets.example.com/events/marigold-static"
IMAGE = "https://tickets.example.com/images/cold-harbor-2009.jpg"


def page(name):
    with open(os.path.join(PAGES, name), "rb") as f:
        return f.read()


class Resp:
    """What fetchurl._open yields: status, getheader(), read(n). Serves body in pieces; `fail` raises mid-read."""

    def __init__(self, status=200, headers=None, body=b"", fail=None):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body
        self.fail = fail

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, n=-1):
        if self.fail:
            raise self.fail
        n = len(self.body) if n is None or n < 0 else n
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk


class Web:
    """Stands in for fetchurl._open. `pages` maps a URL to a Resp or an exception to raise on connecting; `calls`
    records (scheme, host, port, addr, target) for every connection made."""

    def __init__(self):
        self.pages = {}
        self.calls = []

    @contextmanager
    def __call__(self, scheme, host, port, addr, target):
        self.calls.append((scheme, host, port, addr, target))
        default = 443 if scheme == "https" else 80
        url = f"{scheme}://{host}{'' if port == default else f':{port}'}{target}"
        answer = self.pages.get(url)
        if answer is None:
            raise AssertionError(f"unexpected fetch {url}")
        if isinstance(answer, BaseException):
            raise answer
        yield answer

    def html(self, url, body, **headers):
        self.pages[url] = Resp(200, {"Content-Type": "text/html; charset=utf-8", **headers}, body)

    def redirect(self, url, to, status=302):
        self.pages[url] = Resp(status, {"Location": to})


class Dns:
    """Stands in for socket.getaddrinfo: invented answers by host name, PUBLIC for anything else."""

    def __init__(self):
        self.answers = {}
        self.lookups = []

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.lookups.append(host)
        out = []
        for ip in self.answers.get(host, [PUBLIC]):
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port or 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)))
        return out


@pytest.fixture
def dns(monkeypatch):
    d = Dns()
    monkeypatch.setattr(socket, "getaddrinfo", d)
    return d


@pytest.fixture
def web(monkeypatch, dns):
    w = Web()
    monkeypatch.setattr(fetchurl, "_open", w)
    return w


@pytest.fixture
def show(con):
    return mk_show(con, "Cold Harbor", "2009-06-21", mk_venue(con, "Granite Hall"))


def refused(fn, *a, **kw):
    with pytest.raises(ApiError) as e:
        fn(*a, **kw)
    return e.value


def counts(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("artist", "lineup", "source", "image", "show")}


# ---------------------------------------------------------------- extract
def test_extract_og_and_jsonld():
    got = fetchurl.extract(page("show-jsonld.html").decode(), PAGE)
    assert got["image"] == IMAGE  # og:image, made absolute against the page, wins over the JSON-LD image
    assert got["title"] == "Cold Harbor with Velvet Arcade"
    assert got["event"] == {"date": "2009-06-20", "venue": "Granite Hall",
                            "performers": ["Cold Harbor", "Velvet Arcade"]}


def test_extract_og_only():
    got = fetchurl.extract(page("show-og.html").decode(), OG_PAGE)
    assert got["image"] == "https://tickets.example.com/media/marigold-static-poster.jpg"
    assert got["title"] == "Marigold Static & Friends, live at The Bellwether"
    assert got["event"] == {"date": None, "venue": None, "performers": []}


def test_extract_odd_shapes():
    html = """<html><head><title>  Fallback   title </title>
    <meta property="og:image" content="javascript:alert(1)">
    <script type="application/ld+json">{"@graph": [{"@type": ["Event", "Thing"], "startDate": "2025-07",
      "location": [{"name": "The Bellwether"}], "performer": {"name": "Marigold Static"},
      "image": [{"url": "/a.png"}]}]}</script></head></html>"""
    got = fetchurl.extract(html, OG_PAGE)
    assert got["title"] == "Fallback title"  # <title> when there is no og:title
    assert got["image"] == "https://tickets.example.com/a.png"  # a javascript: og:image is ignored
    assert got["event"] == {"date": None, "venue": "The Bellwether", "performers": ["Marigold Static"]}
    assert fetchurl.extract("", OG_PAGE) == {"image": None, "title": None,
                                             "event": {"date": None, "venue": None, "performers": []}}


# ---------------------------------------------------------------- the SSRF guard
@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://192.168.1.100/", "http://[::1]/",
                                 "http://169.254.169.254/latest/", "file:///etc/passwd", "ftp://example.com/"])
def test_refused_addresses(url, web):
    e = refused(fetchurl.safe_fetch, url)
    assert e.status == 400
    assert web.calls == []


# Review focus 5: disguised loopback. The fake resolver answers PUBLIC for these, so the guard must see through them
# itself rather than trust whatever the resolver makes of them.
@pytest.mark.parametrize("url", ["http://2130706433/", "http://[::ffff:127.0.0.1]/", "http://0x7f.1/"])
def test_disguised_loopback_refused(url, web):
    e = refused(fetchurl.safe_fetch, url)
    assert (e.status, e.message) == (400, "That address isn't allowed.")
    assert web.calls == []


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.8.9.10", "::1", "0.0.0.0", "::",                        # loopback, unspecified
    "10.0.0.5", "172.16.0.1", "192.168.1.100", "100.64.0.1", "fc00::5",         # private, shared, unique local
    "169.254.169.254", "fe80::1", "224.0.0.1", "ff02::1",                       # link-local, multicast
    "240.0.0.1", "255.255.255.255", "192.0.2.10", "2001:db8::1",                # reserved, documentation
    "::ffff:127.0.0.1", "::ffff:10.0.0.5", "::ffff:7f00:1", "::127.0.0.1",      # IPv4 inside IPv6
    "64:ff9b::7f00:1", "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:80ff:fffe",  # NAT64, 6to4, Teredo
    "2130706433", "0x7f000001", "0x7f.1", "127.1", "0177.0.0.1", "0x7f.0.0.1",  # inet_aton spellings
    "017700000001", "127.0.1", "0x7F.1.", "1.2.3.4.5", "0xffffffffff", "\uff11\uff12\uff17.\uff11",
])
def test_check_host_refuses_every_non_public_address(host, dns):
    e = refused(fetchurl.check_host, host)
    assert (e.status, e.message) == (400, "That address isn't allowed.")


def test_check_host_refuses_a_name_if_any_address_is_private(dns):
    dns.answers["split.example.com"] = [PUBLIC, "10.0.0.5"]
    dns.answers["v6.example.com"] = ["::ffff:169.254.169.254"]
    dns.answers["localhost"] = ["127.0.0.1", "::1"]
    for host in ("split.example.com", "v6.example.com", "localhost"):
        assert refused(fetchurl.check_host, host).status == 400


def test_check_host_returns_the_checked_addresses(dns):
    dns.answers["tickets.example.com"] = [PUBLIC, PUBLIC]
    assert fetchurl.check_host("tickets.example.com") == [PUBLIC]
    assert fetchurl.check_host(PUBLIC) == [PUBLIC]
    assert dns.lookups == ["tickets.example.com"]  # an address literal is not looked up


def test_check_host_unknown_name(monkeypatch):
    def fail(*a, **kw):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    e = refused(fetchurl.check_host, "nowhere.example.com")
    assert (e.status, e.message) == (400, "Couldn't find that site.")


def test_redirect_to_private_refused(web, dns):
    web.redirect(PAGE, "http://10.0.0.5/admin")
    e = refused(fetchurl.safe_fetch, PAGE)
    assert (e.status, e.message) == (400, "That address isn't allowed.")
    assert [c[3] for c in web.calls] == [PUBLIC]  # the first hop only: nothing was opened to 10.0.0.5


def test_redirect_to_a_name_that_resolves_private_or_to_a_file_refused(web, dns):
    dns.answers["intranet.example.com"] = ["10.0.0.5"]
    web.redirect(PAGE, "https://intranet.example.com/")
    assert refused(fetchurl.safe_fetch, PAGE).status == 400
    web.redirect(PAGE, "file:///etc/passwd", status=301)
    assert refused(fetchurl.safe_fetch, PAGE).status == 400
    assert all(c[1] == "tickets.example.com" for c in web.calls)


def test_redirects_are_followed_relative_and_capped(web):
    web.redirect(PAGE, "/r/1")
    for i in range(1, 5):
        web.redirect(f"https://tickets.example.com/r/{i}", f"/r/{i + 1}", status=(301, 302, 303, 307, 308)[i])
    web.html("https://tickets.example.com/r/5", b"<title>here</title>")
    final, body, ctype = fetchurl.safe_fetch(PAGE)
    assert (final, body, ctype) == ("https://tickets.example.com/r/5", b"<title>here</title>", "text/html")
    web.redirect("https://tickets.example.com/r/5", "/r/6")
    e = refused(fetchurl.safe_fetch, PAGE)
    assert (e.status, e.message) == (502, "Too many redirects.")
    assert len(web.calls) == 6 + 6  # five redirects followed, the sixth refused before connecting


def test_connects_to_the_checked_address(web, dns):
    dns.answers["tickets.example.com"] = [PUBLIC]
    web.html(PAGE, b"<p>ok</p>")
    fetchurl.safe_fetch(PAGE)
    assert web.calls == [("https", "tickets.example.com", 443, PUBLIC, "/events/2009/cold-harbor")]
    assert dns.lookups == ["tickets.example.com"]  # resolved once, by the guard


def test_the_real_connection_goes_to_the_checked_address(monkeypatch, dns):
    """End to end through http.client over a socketpair: the socket is opened to the checked address (never the
    name, which a second lookup could rebind), and the request carries the Host and the UA."""
    dns.answers["tickets.example.com"] = [PUBLIC]
    ours, theirs = socket.socketpair()
    seen, opened = {}, []

    def server():
        data = b""
        while b"\r\n\r\n" not in data:
            data += theirs.recv(4096)
        seen["request"] = data.decode()
        theirs.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 13\r\n"
                       b"Connection: close\r\n\r\n<p>hello</p>\n")
        theirs.close()

    def create_connection(address, timeout=None, *a, **kw):
        opened.append((address, timeout))
        return ours

    monkeypatch.setattr(socket, "create_connection", create_connection)
    t = threading.Thread(target=server)
    t.start()
    try:
        assert fetchurl.safe_fetch("http://tickets.example.com/e/1?x=2") == (
            "http://tickets.example.com/e/1?x=2", b"<p>hello</p>\n", "text/html")
    finally:
        t.join(5)
        ours.close()
    assert opened == [((PUBLIC, 80), fetchurl.TIMEOUT)]
    assert dns.lookups == ["tickets.example.com"]
    head = seen["request"].split("\r\n")
    assert head[0] == "GET /e/1?x=2 HTTP/1.1"
    assert "Host: tickets.example.com" in head
    assert f"User-Agent: {settings.UA}" in head


def test_https_verifies_the_name_on_the_checked_address(monkeypatch):
    opened, wrapped = [], []

    class Ctx:
        def wrap_socket(self, sock, server_hostname=None):
            wrapped.append(server_hostname)
            return sock

    monkeypatch.setattr(socket, "create_connection", lambda address, timeout=None, *a, **kw: opened.append(address))
    monkeypatch.setattr(fetchurl, "_tls", lambda: Ctx())
    conn = fetchurl._PinnedHTTPS("tickets.example.com", 443, PUBLIC, 5)
    conn.connect()
    assert opened == [(PUBLIC, 443)]
    assert wrapped == ["tickets.example.com"]


def test_body_cap_and_timeout(web):
    web.html(PAGE, b"x" * (2_000_000 + 1))
    e = refused(fetchurl.safe_fetch, PAGE)
    assert (e.status, e.message) == (400, "Page too large.")
    web.pages[PAGE] = Resp(200, {"Content-Type": "text/html", "Content-Length": "9000000"}, b"")
    assert refused(fetchurl.safe_fetch, PAGE).message == "Page too large."
    web.html(PAGE, b"x" * 2_000_000)
    assert len(fetchurl.safe_fetch(PAGE)[1]) == 2_000_000
    web.pages[PAGE] = socket.timeout("timed out")
    e = refused(fetchurl.safe_fetch, PAGE)
    assert (e.status, e.message) == (502, "That site took too long to answer.")
    web.pages[PAGE] = Resp(200, {"Content-Type": "text/html"}, fail=TimeoutError("timed out"))
    assert refused(fetchurl.safe_fetch, PAGE).status == 502


def test_http_error_and_unreachable_are_502(web):
    web.pages[PAGE] = Resp(404, {"Content-Type": "text/html"}, b"gone")
    e = refused(fetchurl.safe_fetch, PAGE)
    assert (e.status, e.message) == (502, "That site answered 404.")
    web.pages[PAGE] = ConnectionRefusedError("refused")
    assert refused(fetchurl.safe_fetch, PAGE).status == 502
    web.pages[PAGE] = Resp(302, {})
    assert refused(fetchurl.safe_fetch, PAGE).status == 502  # a redirect that goes nowhere


def test_urls_with_credentials_or_no_host_refused(web):
    for url in ("http://user:pw@tickets.example.com/", "http:///x", "https://", "//tickets.example.com/", "x"):
        assert refused(fetchurl.safe_fetch, url).status == 400
    assert web.calls == []


# ---------------------------------------------------------------- suggestions and apply (the routes)
def post(c, show_id, body, apply=False):
    return c.post(f"/admin/api/shows/{show_id}/fetch-url{'/apply' if apply else ''}", json=body)


def test_suggestions(editor, con, web, show):
    web.html(PAGE, page("show-jsonld.html"))
    r = post(editor, show, {"url": PAGE})
    assert r.status_code == 200
    got = r.get_json()
    assert got["url"] == PAGE and got["image"] == IMAGE and got["title"] == "Cold Harbor with Velvet Arcade"
    assert got["event"]["date"] == "2009-06-20" and got["event"]["venue"] == "Granite Hall"
    assert got["performers"] == [{"name": "Cold Harbor", "on_lineup": True},
                                 {"name": "Velvet Arcade", "on_lineup": False}]
    assert got["date_differs"] is True


def test_suggestions_for_a_bare_image_offer_it_as_the_poster(editor, web, show):
    web.pages[IMAGE] = Resp(200, {"Content-Type": "image/jpeg"}, page("poster.jpg"))
    got = post(editor, show, {"url": IMAGE}).get_json()
    assert got["image"] == IMAGE and got["performers"] == [] and got["date_differs"] is False


def test_nothing_applied_without_apply(editor, con, web, show):
    web.html(PAGE, page("show-jsonld.html"))
    before = counts(con)
    assert post(editor, show, {"url": PAGE}).status_code == 200
    assert counts(con) == before
    assert not settings.IMAGE_DIR.exists() or not os.listdir(settings.IMAGE_DIR)


def test_apply_poster_records_source_url(editor, con, web, show):
    web.pages[IMAGE] = Resp(200, {"Content-Type": "image/jpeg"}, page("poster.jpg"))
    r = post(editor, show, {"kind": "poster", "value": IMAGE, "url": PAGE}, apply=True)
    assert r.status_code == 201
    row = con.execute("SELECT kind, source_url, file FROM image WHERE show_id = ?", (show,)).fetchone()
    assert (row["kind"], row["source_url"]) == ("poster", PAGE)
    assert (settings.IMAGE_DIR / row["file"]).read_bytes() == page("poster.jpg")
    assert r.get_json()["image"]["kind"] == "poster"


def test_apply_poster_refuses_what_is_not_an_image_or_not_allowed(editor, con, web, show):
    web.html(IMAGE, b"<html>not an image</html>")
    assert post(editor, show, {"kind": "poster", "value": IMAGE, "url": PAGE}, apply=True).status_code == 400
    r = post(editor, show, {"kind": "poster", "value": "http://169.254.169.254/x.jpg", "url": PAGE}, apply=True)
    assert r.status_code == 400 and r.get_json()["error"] == "That address isn't allowed."
    assert counts(con)["image"] == 0


def test_apply_performer_adds_lineup_and_source(editor, con, show):
    before = counts(con)
    r = post(editor, show, {"kind": "performer", "value": "Velvet Arcade", "url": PAGE}, apply=True)
    assert r.status_code == 201
    entry = con.execute("SELECT l.id, l.role, a.name FROM lineup l JOIN artist a ON a.id = l.artist_id "
                        "WHERE l.show_id = ? AND a.name = 'Velvet Arcade'", (show,)).fetchone()
    assert entry["role"] == "performer"
    src = con.execute("SELECT label, url, lineup_id FROM source WHERE show_id = ?", (show,)).fetchone()
    assert (src["label"], src["url"], src["lineup_id"]) == ("tickets.example.com", PAGE, entry["id"])
    assert counts(con)["artist"] == before["artist"] + 1
    assert con.execute("SELECT version FROM show WHERE id = ?", (show,)).fetchone()[0] == 2
    again = post(editor, show, {"kind": "performer", "value": "velvet  arcade", "url": PAGE}, apply=True)
    assert again.status_code == 409
    assert counts(con)["source"] == 1


def test_apply_source(editor, con, show):
    r = post(editor, show, {"kind": "source", "value": "", "url": PAGE}, apply=True)
    assert r.status_code == 201
    src = con.execute("SELECT label, url, lineup_id FROM source WHERE show_id = ?", (show,)).fetchone()
    assert (src["label"], src["url"], src["lineup_id"]) == ("tickets.example.com", PAGE, None)
    assert post(editor, show, {"kind": "source", "url": PAGE}, apply=True).status_code == 409


def test_apply_and_suggest_validate(editor, client, con, web, show):
    assert post(editor, show, {"kind": "rating", "value": "5", "url": PAGE}, apply=True).status_code == 400
    assert post(editor, show, {"kind": "source", "url": "javascript:alert(1)"}, apply=True).status_code == 400
    assert post(editor, show, {"kind": "performer", "value": "", "url": PAGE}, apply=True).status_code == 400
    assert post(editor, show, {"url": "x" * 2001}).status_code == 400
    assert post(editor, show, {}).status_code == 400
    assert post(editor, 999, {"kind": "source", "url": PAGE}, apply=True).status_code == 404
    assert post(editor, 999, {"url": PAGE}).status_code == 404
    assert post(client, show, {"url": PAGE}).status_code == 401
    assert post(client, show, {"kind": "source", "url": PAGE}, apply=True).status_code == 401
    assert counts(con)["source"] == 0
