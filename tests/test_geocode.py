"""Venue coordinates (Task 16): geocode.py against recorded Nominatim fixtures; urlopen is patched, never the network."""
import io
import os
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

import geocode
import settings
from common import ApiError
from conftest import mk_show, mk_venue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "nominatim")


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fixture_bytes(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


@pytest.fixture
def net(monkeypatch):
    """Patches urlopen with a queue of replies (fixture name, or an exception); records requests and sleeps."""
    state = type("S", (), {"replies": [], "requests": [], "sleeps": [], "now": 1000.0})()

    def urlopen(req, timeout=None):
        state.requests.append((req, timeout))
        reply = state.replies.pop(0) if state.replies else "hit.json"
        if isinstance(reply, Exception):
            raise reply
        return Resp(fixture_bytes(reply))

    def sleep(s):
        state.sleeps.append(s)
        state.now += s

    monkeypatch.setattr(geocode.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(geocode.time, "sleep", sleep)
    monkeypatch.setattr(geocode.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(geocode, "_last_call", None)
    return state


def venue_row(con, vid):
    return con.execute("SELECT lat, lon, geocode_source FROM venue WHERE id = ?", (vid,)).fetchone()


def test_lookup_hit(net):
    assert geocode.lookup("The Bellwether", "Oakland", "CA", "US") == (37.804412, -122.271114)


def test_lookup_request_carries_ua_query_and_timeout(net):
    geocode.lookup("The Bellwether", "Oakland", "CA", "US")
    req, timeout = net.requests[0]
    assert req.get_header("User-agent") == settings.UA
    assert timeout == 10
    url = urlsplit(req.full_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == settings.GEOCODER_URL
    q = parse_qs(url.query)
    assert q == {"q": ["The Bellwether, Oakland, CA, US"], "format": ["jsonv2"], "limit": ["1"]}


def test_lookup_leaves_out_missing_parts(net):
    geocode.lookup("Cold Harbor Hall", "Oakland", None, None)
    assert parse_qs(urlsplit(net.requests[0][0].full_url).query)["q"] == ["Cold Harbor Hall, Oakland"]


def test_lookup_miss_is_none(net):
    net.replies = ["miss.json"]
    assert geocode.lookup("Nowhere", "Nowhere", None, None) is None


@pytest.mark.parametrize("reply", [
    urllib.error.HTTPError("http://x", 500, "boom", {}, None),
    TimeoutError("timed out"),
    urllib.error.URLError(TimeoutError("timed out")),
    OSError("refused"),
])
def test_lookup_failure_is_502(net, reply):
    net.replies = [reply]
    with pytest.raises(ApiError) as e:
        geocode.lookup("The Bellwether", "Oakland", "CA", "US")
    assert (e.value.status, e.value.message) == (502, "Lookup failed")


def test_lookup_garbage_is_502(net, monkeypatch):
    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda req, timeout=None: Resp(b"<html>not json"))
    with pytest.raises(ApiError) as e:
        geocode.lookup("a", "b", None, None)
    assert e.value.status == 502


def test_calls_are_a_second_apart(net):
    geocode.lookup("a", "b", None, None)
    assert net.sleeps == []
    geocode.lookup("c", "d", None, None)
    assert len(net.sleeps) == 1 and net.sleeps[0] >= 1.0
    net.now += 5
    geocode.lookup("e", "f", None, None)
    assert len(net.sleeps) == 1  # already more than a second since the last call


def test_failed_call_still_counts_for_the_rate_limit(net):
    net.replies = [TimeoutError()]
    with pytest.raises(ApiError):
        geocode.lookup("a", "b", None, None)
    geocode.lookup("c", "d", None, None)
    assert net.sleeps and net.sleeps[0] >= 1.0 - 1e-9


def test_locate_venue_stores_coordinates(net, con):
    vid = mk_venue(con, "The Bellwether")
    v = geocode.locate_venue(con, vid)
    assert (v["lat"], v["lon"], v["geocode_source"]) == (37.804412, -122.271114, "nominatim")
    assert tuple(venue_row(con, vid)) == (37.804412, -122.271114, "nominatim")


def test_locate_venue_miss_stores_nothing(net, con):
    net.replies = ["miss.json"]
    vid = mk_venue(con, "Nowhere Hall")
    v = geocode.locate_venue(con, vid)
    assert v["lat"] is None and v["geocode_source"] is None
    assert tuple(venue_row(con, vid)) == (None, None, None)


@pytest.mark.parametrize("reply", [urllib.error.HTTPError("http://x", 500, "boom", {}, None), TimeoutError()])
def test_locate_venue_failure_stores_nothing(net, con, reply):
    net.replies = [reply]
    vid = mk_venue(con, "The Bellwether")
    with pytest.raises(ApiError) as e:
        geocode.locate_venue(con, vid)
    assert e.value.status == 502
    assert tuple(venue_row(con, vid)) == (None, None, None)


def test_locate_unknown_venue_is_404(net, con):
    with pytest.raises(ApiError) as e:
        geocode.locate_venue(con, 999)
    assert e.value.status == 404 and net.requests == []


def test_set_coords(con):
    vid = mk_venue(con, "The Bellwether")
    v = geocode.set_coords(con, vid, 37.5, -122.25)
    assert (v["lat"], v["lon"], v["geocode_source"]) == (37.5, -122.25, "manual")


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181), ("x", 0), (None, 0), (True, 0),
                                     (float("nan"), 0)])
def test_set_coords_rejects(con, lat, lon):
    vid = mk_venue(con, "The Bellwether")
    with pytest.raises(ApiError) as e:
        geocode.set_coords(con, vid, lat, lon)
    assert e.value.status == 400
    assert tuple(venue_row(con, vid)) == (None, None, None)


def test_missing_coords(con):
    a = mk_venue(con, "Bellwether")
    mk_venue(con, "Located", lat=1.0, lon=2.0)
    b = mk_venue(con, "Cold Harbor Hall", city="Reno", region="NV")
    mk_show(con, "The Paper Lanterns", "2024-05-01", b)
    mk_show(con, "Marigold Static", "2024-06-01", b)
    assert geocode.missing_coords(con) == [
        {"id": b, "name": "Cold Harbor Hall", "city": "Reno", "shows": 2},
        {"id": a, "name": "Bellwether", "city": "Oakland", "shows": 0},
    ]


# ---------------------------------------------------------------- routes
def test_routes_need_an_editor(client):
    assert client.get("/admin/api/venues/missing-coords").status_code == 401
    assert client.post("/admin/api/venues/1/locate").status_code == 401
    assert client.put("/admin/api/venues/1/coords", json={"lat": 1, "lon": 2}).status_code == 401


def test_missing_coords_route(editor, con):
    vid = mk_venue(con, "Bellwether")
    r = editor.get("/admin/api/venues/missing-coords")
    assert r.status_code == 200
    assert r.get_json() == {"venues": [{"id": vid, "name": "Bellwether", "city": "Oakland", "shows": 0}]}


def test_locate_route(editor, con, net):
    vid = mk_venue(con, "The Bellwether")
    r = editor.post(f"/admin/api/venues/{vid}/locate")
    assert r.status_code == 200
    assert r.get_json()["lat"] == 37.804412
    assert editor.get("/admin/api/venues/missing-coords").get_json() == {"venues": []}


def test_locate_route_failure_is_502_json(editor, con, net):
    net.replies = [TimeoutError()]
    vid = mk_venue(con, "The Bellwether")
    r = editor.post(f"/admin/api/venues/{vid}/locate")
    assert r.status_code == 502 and r.get_json() == {"error": "Lookup failed"}


def test_coords_route(editor, con):
    vid = mk_venue(con, "The Bellwether")
    r = editor.put(f"/admin/api/venues/{vid}/coords", json={"lat": 37.5, "lon": -122.25})
    assert r.status_code == 200 and r.get_json()["geocode_source"] == "manual"
    assert editor.put(f"/admin/api/venues/{vid}/coords", json={"lat": 91, "lon": 0}).status_code == 400
    assert editor.put(f"/admin/api/venues/{vid}/coords", json={"lat": 1}).status_code == 400
    assert editor.put("/admin/api/venues/999/coords", json={"lat": 1, "lon": 2}).status_code == 404
