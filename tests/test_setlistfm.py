"""setlist.fm (spec 7.11): search (by act, and by venue for the whole show, matched to the lineup), the live fetch
and its cache, per-lineup-entry links, the panel, the routes, and that no song data is stored. Network is never
touched: setlistfm.urlopen is replaced by a recorded fixture, an HTTP error or a timeout."""
import io
import os
import socket
from urllib.error import HTTPError

import pytest

import setlistfm
import settings
from conftest import mk_artist, mk_show, mk_venue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "setlistfm")
SID = "63ab1c4e"
URL = "https://www.setlist.fm/setlist/the-paper-lanterns/2025/granite-hall-oakland-ca-63ab1c4e.html"
OPENER_SID = "2b7f9a10"
OPENER_URL = "https://www.setlist.fm/setlist/marigold-static/2025/granite-hall-oakland-ca-2b7f9a10.html"
UNKNOWN_SID = "4e1d8c22"


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


def test_search_venue_sends_venue_city_and_date(net):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    found = setlistfm.search_venue("Granite Hall", "Oakland", "2025-07-19")
    req, timeout = net.calls[0]
    assert "venueName=Granite+Hall" in req.full_url and "cityName=Oakland" in req.full_url
    assert "date=19-07-2025" in req.full_url and "artistName" not in req.full_url
    assert req.get_header("X-api-key") == "test-setlist-key" and req.get_header("User-agent") == settings.UA
    assert timeout == 10
    assert [(r["id"], r["artist"]) for r in found] == [(SID, "The Paper Lanterns"), (OPENER_SID, "Marigold Static"),
                                                       (UNKNOWN_SID, "Cold Harbor")]


def test_search_venue_miss_and_failures(net):
    net.routes["/search/setlists"] = http_error(404)
    assert setlistfm.search_venue("Granite Hall", "Oakland", "2025-07-19") == []
    for failure in (http_error(500), socket.timeout("t")):
        net.routes["/search/setlists"] = failure
        with pytest.raises(setlistfm.Unavailable):
            setlistfm.search_venue("Granite Hall", "Oakland", "2025-07-19")


def test_no_key_makes_no_request(net, monkeypatch):
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    with pytest.raises(setlistfm.Unavailable):
        setlistfm.search("The Paper Lanterns", "2025-07-19")
    with pytest.raises(setlistfm.Unavailable):
        setlistfm.search_venue("Granite Hall", "Oakland", "2025-07-19")
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


# ---------------------------------------------------------------- helpers (this file only)
@pytest.fixture
def show(con):
    """The Paper Lanterns with Marigold Static opening (running order: the opener first) at Granite Hall, Oakland."""
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "The Paper Lanterns + Marigold Static", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))


def entry_of(con, show_id, name):
    return con.execute("SELECT l.id FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? AND a.name = ?",
                       (show_id, name)).fetchone()[0]


def link_url(show_id, lineup_id):
    return f"/admin/api/shows/{show_id}/lineup/{lineup_id}/setlistfm"


def both_setlists(net):
    net.routes[f"/setlist/{SID}"] = fixture("setlist.json")
    net.routes[f"/setlist/{OPENER_SID}"] = fixture("setlist_opener.json")


def link_both(editor, con, show):
    head, opener = entry_of(con, show, "The Paper Lanterns"), entry_of(con, show, "Marigold Static")
    assert editor.put(link_url(show, head), json={"id": SID, "url": URL}).status_code == 200
    assert editor.put(link_url(show, opener), json={"id": OPENER_SID, "url": OPENER_URL}).status_code == 200
    return head, opener


# ---------------------------------------------------------------- never stored
def test_no_song_is_ever_stored(net, client, editor, con, show):
    both_setlists(net)
    link_both(editor, con, show)
    body = client.get(f"/api/shows/{show}/setlist").get_json()
    songs = [s for act in body["acts"] for st in act["sets"] for s in st["songs"]]
    assert "Lantern Light" in songs and "Static Bloom" in songs  # two acts' songs, both seen
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for row in con.execute(f'SELECT * FROM "{table}"').fetchall():
            for value in tuple(row):
                if isinstance(value, str):
                    assert not any(song in value for song in songs), f"{table} holds {value!r}"


# ---------------------------------------------------------------- the panel
def test_panel_shows_each_linked_act_in_lineup_order(net, client, editor, con, show):
    both_setlists(net)
    assert client.get(f"/api/shows/{show}/setlist").get_json() == {"acts": []}
    head, opener = link_both(editor, con, show)
    assert {tuple(r) for r in con.execute("SELECT lineup_id, setlistfm_id, url FROM setlist_link")} == \
        {(head, SID, URL), (opener, OPENER_SID, OPENER_URL)}
    body = client.get(f"/api/shows/{show}/setlist").get_json()
    assert [(a["lineup_id"], a["name"], a["role"], a["available"], a["url"]) for a in body["acts"]] == [
        (opener, "Marigold Static", "opener", True, OPENER_URL), (head, "The Paper Lanterns", "headliner", True, URL)]
    assert body["acts"][0]["sets"] == [{"name": "", "encore": False,
                                        "songs": ["Static Bloom", "Copper Wires", "Lowlight Avenue"]}]
    assert len(body["acts"][1]["sets"]) == 3
    assert "unlinked" not in body


def test_panel_one_act_failing_never_hides_the_others(net, client, editor, con, show):
    both_setlists(net)
    head, opener = link_both(editor, con, show)
    for failure in (http_error(500), http_error(404), TimeoutError()):
        setlistfm._cache.clear()
        net.routes[f"/setlist/{OPENER_SID}"] = failure
        acts = client.get(f"/api/shows/{show}/setlist").get_json()["acts"]
        assert acts[0] == {"lineup_id": opener, "name": "Marigold Static", "role": "opener", "available": False,
                           "url": OPENER_URL, "sets": []}
        assert acts[1]["available"] is True and acts[1]["url"] == URL and acts[1]["sets"]


def test_panel_fetches_one_after_another_through_the_cache(net, client, editor, con, show):
    both_setlists(net)
    link_both(editor, con, show)
    client.get(f"/api/shows/{show}/setlist")
    client.get(f"/api/shows/{show}/setlist")
    assert [c[0].full_url.rsplit("/", 1)[1] for c in net.calls] == [OPENER_SID, SID]


def test_editor_panel_lists_the_acts_with_no_link(net, editor, con, show):
    both_setlists(net)
    head = entry_of(con, show, "The Paper Lanterns")
    editor.put(link_url(show, head), json={"id": SID, "url": URL})
    body = editor.get(f"/admin/api/shows/{show}/setlist").get_json()
    assert [a["lineup_id"] for a in body["acts"]] == [head]
    assert body["unlinked"] == [{"lineup_id": entry_of(con, show, "Marigold Static"), "name": "Marigold Static",
                                 "role": "opener"}]


def test_no_key_means_unavailable_with_the_link(net, client, editor, con, show, monkeypatch):
    head = entry_of(con, show, "The Paper Lanterns")
    editor.put(link_url(show, head), json={"id": SID, "url": URL})
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    assert client.get(f"/api/shows/{show}/setlist").get_json()["acts"] == [
        {"lineup_id": head, "name": "The Paper Lanterns", "role": "headliner", "available": False, "url": URL,
         "sets": []}]
    assert net.calls == []


# ---------------------------------------------------------------- linking per lineup entry
def test_link_replaces_and_unlink_removes_one_entry(net, client, editor, con, show):
    both_setlists(net)
    head, opener = link_both(editor, con, show)
    other = "73cd2d5f"
    other_url = "https://www.setlist.fm/setlist/the-paper-lanterns/2025/summit-pavilion-concord-ca-73cd2d5f.html"
    assert editor.put(link_url(show, head), json={"id": other, "url": other_url}).status_code == 200
    assert con.execute("SELECT setlistfm_id FROM setlist_link WHERE lineup_id = ?", (head,)).fetchone()[0] == other
    assert editor.delete(link_url(show, head)).get_json() == {"ok": True}
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist_link")] == [opener]
    assert editor.delete(link_url(show, head)).status_code == 200  # already gone: still fine


def test_lineup_id_from_another_show_is_404(net, editor, con, show):
    other = mk_show(con, "Cold Harbor", "2025-07-20", mk_venue(con, "Summit Pavilion", "Concord"))
    foreign = entry_of(con, other, "Cold Harbor")
    assert editor.put(link_url(show, foreign), json={"id": SID, "url": URL}).status_code == 404
    assert editor.delete(link_url(show, foreign)).status_code == 404
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search?lineup_id={foreign}").status_code == 404
    assert editor.put(link_url(99999, foreign), json={"id": SID, "url": URL}).status_code == 404
    assert editor.put(link_url(show, 99999), json={"id": SID, "url": URL}).status_code == 404
    assert con.execute("SELECT COUNT(*) FROM setlist_link").fetchone()[0] == 0
    assert net.calls == []


@pytest.mark.parametrize("body", [{"id": "../x", "url": URL}, {"id": SID, "url": "https://evil.example.com/x"},
                                  {"id": SID, "url": "javascript:alert(1)"}, {"id": SID}, {"url": URL}])
def test_link_rejects_bad_input(editor, con, show, body):
    assert editor.put(link_url(show, entry_of(con, show, "The Paper Lanterns")), json=body).status_code == 400


def test_the_per_show_link_routes_are_gone(editor, show):
    assert editor.put(f"/admin/api/shows/{show}/setlistfm", json={"id": SID, "url": URL}).status_code == 404
    assert editor.delete(f"/admin/api/shows/{show}/setlistfm").status_code == 404


# ---------------------------------------------------------------- search: one act
def test_act_search_uses_its_name_and_the_date(net, editor, con, show):
    net.routes["/search/setlists"] = fixture("search.json")
    opener = entry_of(con, show, "Marigold Static")
    body = editor.get(f"/admin/api/shows/{show}/setlistfm/search?lineup_id={opener}").get_json()
    assert body["available"] and [r["id"] for r in body["results"]] == [SID, "73cd2d5f"]
    url = net.calls[0][0].full_url
    assert "artistName=Marigold+Static" in url and "date=19-07-2025" in url and "venueName" not in url


def test_act_search_unavailable_and_bad_id(net, editor, con, show):
    net.routes["/search/setlists"] = TimeoutError()
    opener = entry_of(con, show, "Marigold Static")
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search?lineup_id={opener}").get_json() == \
        {"available": False, "results": []}
    for bad in ("abc", "-1", "1.5", ""):
        assert editor.get(f"/admin/api/shows/{show}/setlistfm/search?lineup_id={bad}").status_code == 400


# ---------------------------------------------------------------- search: the whole show
def test_show_search_matches_results_to_the_lineup(net, editor, con, show):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    body = editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json()
    assert body["available"] is True
    assert [(a["lineup_id"], a["name"], a["role"], a["linked"], [r["id"] for r in a["results"]]) for a in body["acts"]] == [
        (entry_of(con, show, "Marigold Static"), "Marigold Static", "opener", None, [OPENER_SID]),
        (entry_of(con, show, "The Paper Lanterns"), "The Paper Lanterns", "headliner", None, [SID])]
    assert body["acts"][1]["results"][0] == {"id": SID, "url": URL, "artist": "The Paper Lanterns",
                                             "venue": "Granite Hall", "city": "Oakland", "date": "19-07-2025"}
    assert [(r["id"], r["artist"]) for r in body["unmatched"]] == [(UNKNOWN_SID, "Cold Harbor")]
    assert len(net.calls) == 1  # the label is the venue's name: one search
    url = net.calls[0][0].full_url
    assert "venueName=Granite+Hall" in url and "cityName=Oakland" in url and "date=19-07-2025" in url
    assert con.execute("SELECT COUNT(*) FROM setlist_link").fetchone()[0] == 0  # nothing linked until confirmed


def test_show_search_says_what_is_linked(net, editor, con, show):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    head = entry_of(con, show, "The Paper Lanterns")
    editor.put(link_url(show, head), json={"id": SID, "url": URL})
    acts = editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json()["acts"]
    assert [a["linked"] for a in acts] == [None, SID]


def test_show_search_matches_through_an_alias(net, editor, con):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    s = mk_show(con, "The Paper Lanterns", "2025-07-19", mk_venue(con, "Granite Hall"),
                lineup=(("Ninefold", "opener"), ("The Paper Lanterns", "headliner")))
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('cold harbor', ?)", (mk_artist(con, "Ninefold"),))
    body = editor.get(f"/admin/api/shows/{s}/setlistfm/search").get_json()
    assert [[r["id"] for r in a["results"]] for a in body["acts"]] == [[UNKNOWN_SID], [SID]]
    assert [r["id"] for r in body["unmatched"]] == [OPENER_SID]


def test_show_search_tries_the_label_then_the_current_name(net, editor, con):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "The Paper Lanterns", "2025-07-19", v, venue_label="Old Quarry Ballroom")
    body = editor.get(f"/admin/api/shows/{s}/setlistfm/search").get_json()
    urls = [c[0].full_url for c in net.calls]
    assert len(urls) == 2 and "venueName=Old+Quarry+Ballroom" in urls[0] and "venueName=Granite+Hall" in urls[1]
    assert all("cityName=Oakland" in u for u in urls)
    assert [r["id"] for r in body["acts"][0]["results"]] == [SID]  # each setlist once, though both searches found it
    assert [r["id"] for r in body["unmatched"]] == [OPENER_SID, UNKNOWN_SID]


def test_show_search_skips_a_label_that_is_the_same_name(net, editor, con):
    net.routes["/search/setlists"] = fixture("venue_search.json")
    s = mk_show(con, "The Paper Lanterns", "2025-07-19", mk_venue(con, "The Granite Hall"), venue_label="Granite Hall")
    editor.get(f"/admin/api/shows/{s}/setlistfm/search")
    assert len(net.calls) == 1


def test_show_search_empty(net, editor, con, show):
    net.routes["/search/setlists"] = http_error(404)  # setlist.fm answers an empty search with 404 (empty.json)
    body = editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json()
    assert body["available"] is True and body["unmatched"] == []
    assert [a["results"] for a in body["acts"]] == [[], []]


@pytest.mark.parametrize("failure", [http_error(429), http_error(500), socket.timeout("t"), TimeoutError()])
def test_show_search_unavailable(net, editor, show, failure):
    net.routes["/search/setlists"] = failure
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json() == \
        {"available": False, "acts": [], "unmatched": []}


def test_show_search_no_key(net, editor, show, monkeypatch):
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "")
    assert editor.get(f"/admin/api/shows/{show}/setlistfm/search").get_json() == \
        {"available": False, "acts": [], "unmatched": []}
    assert net.calls == []


def test_search_needs_a_day(net, editor, con):
    s = mk_show(con, "The Paper Lanterns", "2025-07", mk_venue(con, "Granite Hall"))
    assert editor.get(f"/admin/api/shows/{s}/setlistfm/search").status_code == 400
    lid = entry_of(con, s, "The Paper Lanterns")
    assert editor.get(f"/admin/api/shows/{s}/setlistfm/search?lineup_id={lid}").status_code == 400
    assert net.calls == []


# ---------------------------------------------------------------- access
def test_admin_routes_need_an_editor(client, con, show):
    lid = entry_of(con, show, "The Paper Lanterns")
    assert client.get(f"/admin/api/shows/{show}/setlistfm/search").status_code == 401
    assert client.get(f"/admin/api/shows/{show}/setlistfm/search?lineup_id={lid}").status_code == 401
    assert client.put(link_url(show, lid), json={"id": SID, "url": URL}).status_code == 401
    assert client.delete(link_url(show, lid)).status_code == 401
    assert client.get(f"/admin/api/shows/{show}/setlist").status_code == 401


def test_upcoming_show_is_404_for_the_public(client, editor, con):
    s = mk_show(con, "The Paper Lanterns", "2099-09-09", mk_venue(con, "Granite Hall"), status="upcoming")
    assert client.get(f"/api/shows/{s}/setlist").status_code == 404
    assert client.get("/api/shows/99999/setlist").status_code == 404
    assert editor.get(f"/admin/api/shows/{s}/setlist").status_code == 200


def test_public_view_off_hides_the_setlist(client, show, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    r = client.get(f"/api/shows/{show}/setlist")
    assert r.status_code == 403 and r.get_json() == {"error": "Sign in to view"}
