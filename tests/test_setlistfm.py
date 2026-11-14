"""setlist.fm (spec 7.11): search, the live fetch and its cache, the routes, and that no song data is stored.
Network is never touched: setlistfm.urlopen is replaced by a recorded fixture, an HTTP error or a timeout."""
import io
import json
import os
import socket
from urllib.error import HTTPError

import pytest

import setlistfm
import settings
from conftest import mk_show, mk_venue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "setlistfm")
SID = "63ab1c4e"
URL = "https://www.setlist.fm/setlist/the-paper-lanterns/2025/granite-hall-oakland-ca-63ab1c4e.html"


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """Stands in for urlopen: records each request, answers from `routes` (path prefix -> bytes, or an exception)."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        path = req.full_url[len(setlistfm.API):]
        for prefix, answer in self.routes.items():
            if path.startswith(prefix):
                if isinstance(answer, BaseException):
                    raise answer
                return Resp(answer)
        raise AssertionError(f"unexpected request {req.full_url}")


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(setlistfm, "urlopen", n)
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "test-setlist-key")
    setlistfm._cache.clear()
    yield n
    setlistfm._cache.clear()


def http_error(code):
    return HTTPError(setlistfm.API, code, "err", {}, io.BytesIO(fixture("empty.json")))


# ---------------------------------------------------------------- search
def test_search_sends_the_date_the_key_and_the_ua(net):
    net.routes["/search/setlists"] = fixture("search.json")
    found = setlistfm.search("The Paper Lanterns", "2025-07-19")
    req, timeout = net.calls[0]
    assert "date=19-07-2025" in req.full_url and "artistName=The+Paper+Lanterns" in req.full_url
    assert req.get_header("X-api-key") == "test-setlist-key"
    assert req.get_header("Accept") == "application/json"
    assert req.get_header("User-agent") == settings.UA
    assert timeout == 10
    assert found[0] == {"id": SID, "url": URL, "artist": "The Paper Lanterns", "venue": "Granite Hall",
                        "city": "Oakland", "date": "19-07-2025"}
    assert len(found) == 2


def test_search_miss_is_empty(net):
    net.routes["/search/setlists"] = http_error(404)
    assert setlistfm.search("Nobody", "2025-07-19") == []
    net.routes["/search/setlists"] = b'{"setlist": []}'
    assert setlistfm.search("Nobody", "2025-07-19") == []


@pytest.mark.parametrize("failure", [http_error(429), http_error(500), socket.timeout("timed out"), TimeoutError()])
def test_search_failures_are_unavailable(net, failure):
    net.routes["/search/setlists"] = failure
    with pytest.raises(setlistfm.Unavailable):
        setlistfm.search("The Paper Lanterns", "2025-07-19")


def test_no_key_makes_no_request(net, monkeypatch):
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    with pytest.raises(setlistfm.Unavailable):
        setlistfm.search("The Paper Lanterns", "2025-07-19")
    assert setlistfm.fetch_setlist(SID) is None
    assert net.calls == []


# ---------------------------------------------------------------- fetch and cache
def test_fetch_parses_the_sets(net):
    net.routes["/setlist/"] = fixture("setlist.json")
    got = setlistfm.fetch_setlist(SID)
    assert got["url"] == URL
    assert got["sets"] == [
        {"name": "", "encore": False, "songs": ["Lantern Light", "Slow Harbor", "Tin Roof Weather"]},
        {"name": "Acoustic", "encore": False, "songs": ["Quiet Orchard"]},
        {"name": "", "encore": True, "songs": ["Last Match"]},
    ]
    assert net.calls[0][0].full_url == f"{setlistfm.API}/setlist/{SID}"
    assert net.calls[0][0].get_header("X-api-key") == "test-setlist-key"


@pytest.mark.parametrize("failure", [http_error(404), http_error(429), http_error(500), socket.timeout("t"), TimeoutError()])
def test_fetch_miss_error_and_timeout_are_none(net, failure):
    net.routes["/setlist/"] = failure
    assert setlistfm.fetch_setlist(SID) is None


def test_fetch_rejects_odd_ids(net):
    assert setlistfm.fetch_setlist("../x") is None and net.calls == []


def test_cache_serves_the_second_call_and_expires(net, monkeypatch):
    net.routes["/setlist/"] = fixture("setlist.json")
    now = [1000.0]
    monkeypatch.setattr(setlistfm, "_now", lambda: now[0])
    first = setlistfm.fetch_setlist(SID)
    assert setlistfm.fetch_setlist(SID) == first and len(net.calls) == 1
    now[0] += 3599
    setlistfm.fetch_setlist(SID)
    assert len(net.calls) == 1
    now[0] += 2
    setlistfm.fetch_setlist(SID)
    assert len(net.calls) == 2


def test_failures_are_not_cached(net):
    net.routes["/setlist/"] = http_error(500)
    assert setlistfm.fetch_setlist(SID) is None
    net.routes["/setlist/"] = fixture("setlist.json")
    assert setlistfm.fetch_setlist(SID)["sets"]


def test_no_song_is_ever_stored(net, client, editor, con):
    net.routes["/setlist/"] = fixture("setlist.json")
    show = mk_show(con, "The Paper Lanterns", "2025-07-19", mk_venue(con, "Granite Hall"))
    assert editor.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL}).status_code == 200
    body = client.get(f"/api/shows/{show}/setlist").get_json()
    assert body["available"] and "Lantern Light" in json.dumps(body)
    songs = [s for st in body["sets"] for s in st["songs"]]
    assert songs
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for row in con.execute(f'SELECT * FROM "{table}"').fetchall():
            for value in tuple(row):
                if isinstance(value, str):
                    assert not any(song in value for song in songs), f"{table} holds {value!r}"


# ---------------------------------------------------------------- routes
@pytest.fixture
def show(con):
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "The Paper Lanterns + Marigold Static", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))


def test_search_route_uses_headliner_and_date(net, editor, show):
    net.routes["/search/setlists"] = fixture("search.json")
    body = editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json()
    assert body["available"] and body["results"][0]["id"] == SID
    assert "artistName=The+Paper+Lanterns" in net.calls[0][0].full_url and "date=19-07-2025" in net.calls[0][0].full_url


def test_search_route_needs_a_day(net, editor, con):
    s = mk_show(con, "The Paper Lanterns", "2025-07", mk_venue(con, "Granite Hall"))
    assert editor.get(f"/admin/api/shows/{s}/setlistfm/search").status_code == 400
    assert net.calls == []


def test_search_route_unavailable_on_error_and_no_key(net, editor, show, monkeypatch):
    net.routes["/search/setlists"] = TimeoutError()
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json() == {"available": False, "results": []}
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json() == {"available": False, "results": []}


def test_admin_routes_need_an_editor(client, show):
    assert client.get(f"/admin/api/shows/{show}/setlistfm/search").status_code == 401
    assert client.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL}).status_code == 401
    assert client.delete(f"/admin/api/shows/{show}/setlistfm").status_code == 401


def test_link_then_view_then_unlink(net, client, editor, show, con):
    net.routes["/setlist/"] = fixture("setlist.json")
    assert client.get(f"/api/shows/{show}/setlist").get_json() == {"available": False, "url": None, "sets": []}
    assert editor.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL}).status_code == 200
    assert tuple(con.execute("SELECT setlistfm_id, url FROM setlist_link").fetchone()) == (SID, URL)
    body = client.get(f"/api/shows/{show}/setlist").get_json()
    assert body["available"] is True and body["url"] == URL and len(body["sets"]) == 3
    assert editor.get(f"/admin/api/shows/{show}/setlist").get_json() == body
    assert editor.delete(f"/admin/api/shows/{show}/setlistfm").status_code == 200
    assert client.get(f"/api/shows/{show}/setlist").get_json()["available"] is False


def test_unavailable_keeps_the_stored_link(net, client, editor, show):
    editor.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL})
    for failure in (http_error(404), http_error(500), TimeoutError()):
        net.routes["/setlist/"] = failure
        assert client.get(f"/api/shows/{show}/setlist").get_json() == {"available": False, "url": URL, "sets": []}


def test_no_key_means_unavailable_with_the_link(net, client, editor, show, monkeypatch):
    editor.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL})
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    assert client.get(f"/api/shows/{show}/setlist").get_json() == {"available": False, "url": URL, "sets": []}
    assert net.calls == []


@pytest.mark.parametrize("body", [{"id": "../x", "url": URL}, {"id": SID, "url": "https://evil.example.com/x"},
                                  {"id": SID, "url": "javascript:alert(1)"}, {"id": SID}, {"url": URL}])
def test_link_rejects_bad_input(editor, show, body):
    assert editor.put(f"/admin/api/shows/{show}/setlistfm", json=body).status_code == 400


def test_upcoming_show_is_404_for_the_public(client, editor, con):
    s = mk_show(con, "The Paper Lanterns", "2099-09-09", mk_venue(con, "Granite Hall"), status="upcoming")
    assert client.get(f"/api/shows/{s}/setlist").status_code == 404
    assert client.get("/api/shows/99999/setlist").status_code == 404
    assert editor.get(f"/admin/api/shows/{s}/setlist").status_code == 200


def test_public_view_off_hides_the_setlist(client, show, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    r = client.get(f"/api/shows/{show}/setlist")
    assert r.status_code == 403 and r.get_json() == {"error": "Sign in to view"}
