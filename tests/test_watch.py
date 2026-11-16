"""Post-v1 #47: concert watch (watch.py, ticketmaster.py, lastfm.py, notify.py, their routes and the merge). One fake
urlopen answers every outside call by host from recorded, invented fixtures under tests/fixtures/watch/ (Nominatim from
tests/fixtures/nominatim/); time.sleep is patched. Nothing touches the network."""
import io
import json
import logging
import os
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

import lastfm
import geocode
import merge
import notify
import settings
import ticketmaster
import watch
from common import ApiError
from conftest import READ_KEY, mk_artist, mk_show, mk_venue

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
TM_KEY = "tm-test-key-0000"
FM_KEY = "fm-test-key-0000"
PUSH = "https://ntfy.example.com/stubs-watch-a1b2c3"
HARBOR = "11111111-1111-4111-8111-111111111111"
OTHER_HARBOR = "55555555-5555-4555-8555-555555555555"
NOW = "2026-11-14 09:00:00"
HOME = {"name": "Home", "lat": 37.80, "lon": -122.27, "radius_km": 100}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fixture_bytes(name):
    folder = "nominatim" if name.startswith("nominatim/") else "watch"
    with open(os.path.join(FIX, folder, name.split("/")[-1]), "rb") as f:
        return f.read()


KINDS = {"app.ticketmaster.com": "tm", "ws.audioscrobbler.com": "fm", "nominatim.openstreetmap.org": "geo",
         "ntfy.example.com": "push", "hooks.example.com": "push"}
DEFAULTS = {"tm": "tm_hit.json", "fm": "lastfm_top.json", "geo": "nominatim/miss.json", "push": b"ok"}


@pytest.fixture
def net(monkeypatch):
    """Queues of replies per service (net.replies["tm"] etc.: a fixture name, bytes, or an exception to raise); every
    request is recorded as (kind, req, timeout), every sleep in net.sleeps."""
    state = type("S", (), {})()
    state.replies = {k: [] for k in DEFAULTS}
    state.requests, state.sleeps = [], []

    def urlopen(req, timeout=None):
        kind = KINDS[urlsplit(req.full_url).hostname]
        state.requests.append((kind, req, timeout))
        reply = state.replies[kind].pop(0) if state.replies[kind] else DEFAULTS[kind]
        if isinstance(reply, Exception):
            raise reply
        return Resp(reply if isinstance(reply, bytes) else fixture_bytes(reply))

    monkeypatch.setattr(ticketmaster.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ticketmaster.time, "sleep", state.sleeps.append)
    for mod in (ticketmaster, lastfm, geocode):
        monkeypatch.setattr(mod, "_last_call", None)
    monkeypatch.setattr(settings, "TICKETMASTER_API_KEY", TM_KEY)
    monkeypatch.setattr(settings, "LASTFM_API_KEY", FM_KEY)
    monkeypatch.setattr(settings, "LASTFM_USER", "riley-listens")
    monkeypatch.setattr(settings, "WATCH_NOTIFY_URL", PUSH)
    monkeypatch.setattr(settings, "WATCH_NOTIFY_FORMAT", "ntfy")
    state.of = lambda kind: [(r, t) for k, r, t in state.requests if k == kind]
    return state


def failures():
    return [urllib.error.HTTPError("https://app.ticketmaster.com/x", 503, "Service Unavailable", {}, None),
            TimeoutError("timed out"), urllib.error.URLError("refused")]


def area(con, **kw):
    return watch.add_area(con, {**HOME, **kw})


def sighting_rows(con):
    return {r["source_event_id"]: dict(r) for r in con.execute("SELECT * FROM sighting")}


# ---------------------------------------------------------------- the Ticketmaster client
def test_ticketmaster_hit_keeps_only_the_artists_own_events(net):
    found = ticketmaster.events("Marigold Static", None, "2026-11-14")
    assert [e["source_event_id"] for e in found] == ["Z7MS0001", "Z7MS0002", "Z7MS0004"]  # not the tribute, not cancelled
    first = found[0]
    assert first == {"source_event_id": "Z7MS0001", "date": "2027-03-14", "venue": "Old Mill Theatre", "city": "Portland",
                     "region": "OR", "country": "US", "lat": 45.5, "lon": -122.6,
                     "url": "https://www.ticketmaster.com/event/Z7MS0001", "on_sale": "2026-11-20T18:00:00Z"}
    assert found[2]["lat"] is None and found[2]["lon"] is None  # this venue came without coordinates


def test_ticketmaster_request_has_ua_key_query_and_timeout(net):
    ticketmaster.events("Marigold Static", None, "2026-11-14")
    req, timeout = net.of("tm")[0]
    assert req.get_header("User-agent") == settings.UA and timeout == 10
    url = urlsplit(req.full_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == ticketmaster.URL
    assert parse_qs(url.query) == {"keyword": ["Marigold Static"], "classificationName": ["music"], "size": ["100"],
                                   "sort": ["date,asc"], "startDateTime": ["2026-11-14T00:00:00Z"], "apikey": [TM_KEY]}


def test_ticketmaster_miss_is_empty(net):
    net.replies["tm"] = ["tm_empty.json"]
    assert ticketmaster.events("Nobody At All", None, "2026-11-14") == []


def test_ticketmaster_leaves_out_past_dates(net):
    assert [e["source_event_id"] for e in ticketmaster.events("Marigold Static", None, "2027-04-03")] == ["Z7MS0004"]


@pytest.mark.parametrize("failure", failures())
def test_ticketmaster_failures_are_502_and_never_show_the_key(net, failure, caplog):
    net.replies["tm"] = [failure]
    with caplog.at_level(logging.DEBUG), pytest.raises(ApiError) as e:
        ticketmaster.events("Marigold Static", None, "2026-11-14")
    assert (e.value.status, e.value.message) == (502, "Lookup failed")
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert TM_KEY not in caplog.text and TM_KEY not in str(e.value)


def test_ticketmaster_unreadable_answer_is_502(net):
    net.replies["tm"] = [b"<html>busy</html>"]
    with pytest.raises(ApiError) as e:
        ticketmaster.events("Marigold Static", None, "2026-11-14")
    assert e.value.status == 502


def test_ticketmaster_calls_are_spaced_within_its_rate_limit(net):
    ticketmaster.events("Marigold Static", None, "2026-11-14")
    assert net.sleeps == []
    ticketmaster.events("Marigold Static", None, "2026-11-14")
    assert len(net.sleeps) == 1 and 0 < net.sleeps[0] <= ticketmaster.MIN_GAP == 0.25


def test_ticketmaster_matches_by_musicbrainz_id_when_both_have_one(net):
    net.replies["tm"] = ["tm_mbid.json", "tm_mbid.json", "tm_mbid.json"]
    linked = ticketmaster.events("Cold Harbor", HARBOR, "2026-11-14")
    assert [e["source_event_id"] for e in linked] == ["Z7CH0001"]  # the same-name band with another id is not him
    assert [e["source_event_id"] for e in ticketmaster.events("Cold Harbor", OTHER_HARBOR.upper(), "2026-11-14")] \
        == ["Z7CH0002"]
    # without an MBID on our side, the exact name decides: both
    assert [e["source_event_id"] for e in ticketmaster.events("cold  harbor", None, "2026-11-14")] \
        == ["Z7CH0001", "Z7CH0002"]


def test_is_artist_is_never_fuzzy():
    assert ticketmaster.is_artist({"name": "MARIGOLD  STATIC"}, "Marigold Static")
    assert not ticketmaster.is_artist({"name": "Marigold Statics"}, "Marigold Static")
    assert not ticketmaster.is_artist({"name": "The Marigold Static"}, "Marigold Static")
    assert not ticketmaster.is_artist({"name": ""}, "")
    with_id = {"name": "Cold Harbor", "externalLinks": {"musicbrainz": [{"id": HARBOR}]}}
    assert ticketmaster.is_artist(with_id, "Cold Harbor", HARBOR)
    assert not ticketmaster.is_artist(with_id, "Cold Harbor", OTHER_HARBOR)
    assert ticketmaster.is_artist({"name": "Cold Harbor"}, "Cold Harbor", HARBOR)  # no id on their side: the name


# ---------------------------------------------------------------- the Last.fm client
def test_lastfm_hit(net):
    top = lastfm.top_artists("riley-listens")
    assert top[0] == {"name": "Marigold Static", "playcount": 412, "mbid": "44444444-4444-4444-8444-444444444444",
                      "url": "https://www.last.fm/music/Marigold+Static"}
    assert [a["mbid"] for a in top[1:]] == [None, None]


def test_lastfm_request_has_ua_key_user_and_timeout(net):
    lastfm.top_artists("riley-listens")
    req, timeout = net.of("fm")[0]
    assert req.get_header("User-agent") == settings.UA and timeout == 10
    q = parse_qs(urlsplit(req.full_url).query)
    assert q == {"method": ["user.gettopartists"], "user": ["riley-listens"], "period": ["12month"], "limit": ["50"],
                 "api_key": [FM_KEY], "format": ["json"]}


def test_lastfm_miss_is_empty(net):
    net.replies["fm"] = [b'{"topartists": {"artist": [], "@attr": {"user": "riley-listens", "total": "0"}}}']
    assert lastfm.top_artists("riley-listens") == []


@pytest.mark.parametrize("failure", failures() + ["lastfm_error.json"])
def test_lastfm_failures_are_502_and_never_show_the_key(net, failure, caplog):
    net.replies["fm"] = [failure]
    with caplog.at_level(logging.DEBUG), pytest.raises(ApiError) as e:
        lastfm.top_artists("riley-listens")
    assert (e.value.status, e.value.message) == (502, "Lookup failed")
    assert FM_KEY not in caplog.text and FM_KEY not in str(e.value)


def test_lastfm_calls_are_a_second_apart(net):
    lastfm.top_artists("riley-listens")
    lastfm.top_artists("riley-listens")
    assert len(net.sleeps) == 1 and 0 < net.sleeps[0] <= 1.0


# ---------------------------------------------------------------- who is watched
def test_suggest_from_history_orders_by_times_then_latest(con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Cold Harbor", "2012-01-01", v, lineup=(("Velvet Arcade", "opener"), ("Cold Harbor", "headliner")))
    mk_show(con, "Marigold Static", "2015-05-05", v)
    mk_show(con, "Ninefold", "2030-01-01", v, status="upcoming")  # upcoming shows don't count
    assert [(s["name"], s["times"], s["last"]) for s in watch.suggest_from_history(con)] == [
        ("Cold Harbor", 2, "2012-01-01"), ("Marigold Static", 1, "2015-05-05"), ("Velvet Arcade", 1, "2012-01-01")]
    assert [s["name"] for s in watch.suggest_from_history(con, 2)] == ["Cold Harbor"]
    watch.watch_artist(con, mk_artist(con, "Cold Harbor"))
    assert "Cold Harbor" not in [s["name"] for s in watch.suggest_from_history(con)]


def test_suggest_from_lastfm_marks_known_and_seen(con, net):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_artist(con, "Velvet Arcade")  # in Stubs, never seen
    got = {s["name"]: s for s in watch.suggest_from_lastfm(con)}
    assert got["Marigold Static"]["artist_id"] is None and not got["Marigold Static"]["seen"]
    assert got["Cold Harbor"]["seen"] and got["Cold Harbor"]["artist_id"] == mk_artist(con, "Cold Harbor")
    assert got["Velvet Arcade"]["artist_id"] is not None and not got["Velvet Arcade"]["seen"]
    watch.add_watch(con, "Cold Harbor", origin="lastfm")
    assert "Cold Harbor" not in [s["name"] for s in watch.suggest_from_lastfm(con)]


def test_suggest_from_lastfm_is_off_without_key_or_user(con, net, monkeypatch):
    monkeypatch.setattr(settings, "LASTFM_USER", "")
    assert watch.suggest_from_lastfm(con) == [] and net.of("fm") == []


def test_add_watch_makes_a_never_seen_artist_and_resolves_aliases(con):
    aid = watch.add_watch(con, "Marigold Static")
    assert con.execute("SELECT name FROM artist WHERE id = ?", (aid,)).fetchone()[0] == "Marigold Static"
    assert watch.add_watch(con, "marigold  static", anywhere=True) == aid  # already watched: unchanged
    w = con.execute("SELECT origin, anywhere, muted, last_checked FROM watch WHERE artist_id = ?", (aid,)).fetchone()
    assert tuple(w) == ("manual", 0, 0, None)
    harbor = mk_artist(con, "Cold Harbor")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('cold harbour', ?)", (harbor,))
    assert watch.add_watch(con, "Cold Harbour", origin="lastfm") == harbor
    con.execute("UPDATE artist SET mbid = ? WHERE id = ?", (HARBOR, harbor))
    con.execute("DELETE FROM watch")
    assert watch.add_watch(con, "Some Other Spelling", mbid=HARBOR) == harbor  # the MusicBrainz id first
    with pytest.raises(ApiError) as e:
        watch.add_watch(con, "Ninefold", origin="friend")
    assert e.value.status == 400


def test_update_and_remove_watch(con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    seen = watch.watch_artist(con, mk_artist(con, "Cold Harbor"))
    never = watch.add_watch(con, "Marigold Static")
    assert watch.update_watch(con, seen, anywhere=True, muted=True)["anywhere"] is True
    with pytest.raises(ApiError) as e:
        watch.update_watch(con, seen, muted="yes")
    assert e.value.status == 400
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city) "
                "VALUES (?, 'email', 'e1', '2027-01-01', 'Granite Hall', 'Oakland')", (never,))
    watch.remove_watch(con, never)
    watch.remove_watch(con, seen)
    assert con.execute("SELECT COUNT(*) FROM watch").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 0
    names = [r[0] for r in con.execute("SELECT name FROM artist")]
    assert names == ["Cold Harbor"]  # the watch-only artist left nothing behind; the seen one stays
    with pytest.raises(ApiError) as e:
        watch.remove_watch(con, seen)
    assert e.value.status == 404


# ---------------------------------------------------------------- areas
@pytest.mark.parametrize("bad", [{"lat": 90.5}, {"lat": "37"}, {"lon": -181}, {"lon": True}, {"radius_km": 0},
                                 {"radius_km": 20001}, {"name": ""}, {"name": "x" * 101}, {"lat": float("nan")}])
def test_area_is_range_checked(con, bad):
    with pytest.raises(ApiError) as e:
        area(con, **bad)
    assert e.value.status == 400
    assert watch.list_areas(con) == []


def test_near_is_within_some_radius(con):
    a = area(con)
    assert a == {"id": a["id"], "name": "Home", "lat": 37.8, "lon": -122.27, "radius_km": 100.0}
    areas = watch.list_areas(con)
    assert watch.near(areas, 37.80, -122.27)
    assert watch.near(areas, 38.69, -122.27)          # about 99 km due north
    assert not watch.near(areas, 38.71, -122.27)      # about 101 km
    assert not watch.near(areas, 45.5, -122.6)        # about 857 km
    assert 850 < watch.distance_km(37.8, -122.27, 45.5, -122.6) < 860
    area(con, name="North", lat=45.5, lon=-122.6, radius_km=10)
    assert watch.near(watch.list_areas(con), 45.5, -122.6)
    assert not watch.near([], 37.8, -122.27)


# ---------------------------------------------------------------- run
def seed_watch(con, *names, anywhere=False):
    return [watch.add_watch(con, n, anywhere=anywhere) for n in names]


def test_run_keeps_events_near_an_area_and_notifies_once(con, net):
    area(con)
    mk_venue(con, "Granite Hall", lat=37.81, lon=-122.26)  # the event without coordinates is placed by this venue
    (ms,) = seed_watch(con, "Marigold Static")
    r = watch.run(con, NOW)
    assert r == {"checked": 1, "new": 2, "notified": 2}
    rows = sighting_rows(con)
    assert set(rows) == {"Z7MS0002", "Z7MS0004"}  # Portland is out of range
    assert rows["Z7MS0004"]["lat"] == 37.81 and net.of("geo") == []
    assert all(r["status"] == "new" and r["notified_at"] and r["first_seen"] == NOW for r in rows.values())
    assert con.execute("SELECT last_checked FROM watch WHERE artist_id = ?", (ms,)).fetchone()[0] == NOW
    (push, timeout), = net.of("push")
    assert push.full_url == PUSH and timeout == 10 and push.get_header("User-agent") == settings.UA
    assert push.data.decode() == ("Marigold Static · Apr 2, 2027 · The Bellwether, Oakland\n"
                                  "https://www.ticketmaster.com/event/Z7MS0002\n"
                                  "Marigold Static · May 1, 2027 · Granite Hall, Oakland\n"
                                  "https://www.ticketmaster.com/event/Z7MS0004")
    again = watch.run(con, "2026-11-14 10:00:00")
    assert again == {"checked": 1, "new": 0, "notified": 0}  # a re-run pushes nothing new
    assert len(net.of("push")) == 1
    assert sighting_rows(con)["Z7MS0002"]["last_seen"] == "2026-11-14 10:00:00"


def test_run_anywhere_keeps_every_event_and_geocodes_the_unplaced(con, net):
    seed_watch(con, "Marigold Static", anywhere=True)
    net.replies["geo"] = ["nominatim/hit.json"]
    watch.run(con, NOW)
    rows = sighting_rows(con)
    assert set(rows) == {"Z7MS0001", "Z7MS0002", "Z7MS0004"}
    assert (rows["Z7MS0004"]["lat"], rows["Z7MS0004"]["lon"]) == (37.804412, -122.271114)
    q = parse_qs(urlsplit(net.of("geo")[0][0].full_url).query)["q"]
    assert q == ["Granite Hall, Oakland, CA, US"]


def test_run_tries_the_city_when_the_venue_is_unknown_and_drops_what_it_cannot_place(con, net):
    area(con)
    seed_watch(con, "Marigold Static")
    net.replies["geo"] = ["nominatim/miss.json", "nominatim/miss.json"]
    watch.run(con, NOW)
    assert set(sighting_rows(con)) == {"Z7MS0002"}
    assert [parse_qs(urlsplit(r.full_url).query)["q"] for r, _ in net.of("geo")] == [
        ["Granite Hall, Oakland, CA, US"], ["Oakland, CA, US"]]


def test_run_checks_the_oldest_limit_first(con, net):
    ids = seed_watch(con, "Cold Harbor", "Marigold Static", "Velvet Arcade", "Ninefold")
    con.execute("UPDATE watch SET last_checked = '2026-11-13 08:00:00' WHERE artist_id = ?", (ids[0],))
    con.execute("UPDATE watch SET last_checked = '2026-11-12 08:00:00' WHERE artist_id = ?", (ids[1],))
    con.execute("UPDATE watch SET muted = 1 WHERE artist_id = ?", (ids[3],))
    net.replies["tm"] = ["tm_empty.json"] * 3
    assert watch.run(con, NOW, limit=2)["checked"] == 2
    asked = [parse_qs(urlsplit(r.full_url).query)["keyword"][0] for r, _ in net.of("tm")]
    assert asked == ["Velvet Arcade", "Marigold Static"]  # never checked first, then the oldest; muted never
    watch.run(con, "2026-11-14 10:00:00", limit=2)
    asked = [parse_qs(urlsplit(r.full_url).query)["keyword"][0] for r, _ in net.of("tm")]
    assert asked[2:] == ["Cold Harbor", "Marigold Static"]  # then a tie on last_checked goes by artist id


def test_run_is_409_while_another_is_going_and_unlocks_after(con, net):
    seed_watch(con, "Marigold Static")
    watch._lock(con)
    with pytest.raises(ApiError) as e:
        watch.run(con, NOW)
    assert (e.value.status, e.value.message) == (409, "A check is already running.")
    watch._unlock(con)
    watch.run(con, NOW)
    assert con.execute("SELECT COUNT(*) FROM meta WHERE k = 'watch_run'").fetchone()[0] == 0


def test_a_stale_lock_does_not_block_forever(con, net, monkeypatch):
    con.execute("INSERT INTO meta (k, v) VALUES ('watch_run', '1000.0')")
    assert watch.run(con, NOW)["checked"] == 0


def test_run_is_409_over_http(editor, con, clock):
    clock.set("2026-11-14")
    watch._lock(con)
    r = editor.post("/admin/api/watch/run")
    assert r.status_code == 409 and r.get_json() == {"error": "A check is already running."}


def test_a_failing_source_stops_the_run_and_leaves_last_checked(con, net):
    a, b = seed_watch(con, "Cold Harbor", "Marigold Static")
    net.replies["tm"] = [TimeoutError("timed out")]
    assert watch.run(con, NOW) == {"checked": 0, "new": 0, "notified": 0}
    assert len(net.of("tm")) == 1  # a source that failed is not asked again this run
    assert con.execute("SELECT COUNT(*) FROM watch WHERE last_checked IS NULL").fetchone()[0] == 2


def test_expired_and_vanished_sightings_are_deleted(con, net):
    (ms,) = seed_watch(con, "Marigold Static", anywhere=True)
    ins = ("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city, status) "
           "VALUES (?, ?, ?, ?, 'Old Mill Theatre', 'Portland', ?)")
    con.execute(ins, (ms, "ticketmaster", "GONE1", "2027-01-01", "dismissed"))  # no longer returned
    con.execute(ins, (ms, "ticketmaster", "PAST1", "2026-11-13", "new"))       # yesterday
    con.execute(ins, (ms, "email", "TODAY", "2026-11-14", "new"))              # today stays
    con.execute(ins, (ms, "email", "LATER", "2027-01-01", "new"))              # email: nothing to vanish from
    net.replies["geo"] = ["nominatim/hit.json"]
    watch.run(con, NOW)
    assert set(sighting_rows(con)) == {"Z7MS0001", "Z7MS0002", "Z7MS0004", "TODAY", "LATER"}


def test_a_dismissed_sighting_stays_dismissed_and_is_never_pushed(con, net):
    area(con)
    mk_venue(con, "Granite Hall", lat=37.81, lon=-122.26)
    seed_watch(con, "Marigold Static")
    net.replies["push"] = [urllib.error.URLError("refused")]
    watch.run(con, NOW)
    sid = sighting_rows(con)["Z7MS0002"]["id"]
    watch.dismiss(con, sid)
    watch.run(con, "2026-11-14 10:00:00")
    assert sighting_rows(con)["Z7MS0002"]["status"] == "dismissed"
    (push, _), = net.of("push")[1:]
    assert "Z7MS0002" not in push.data.decode() and "Z7MS0004" in push.data.decode()
    with pytest.raises(ApiError) as e:
        watch.dismiss(con, sid)
    assert e.value.status == 409


def test_a_failed_push_is_retried_next_run_and_a_sent_one_never_repeats(con, net, caplog):
    area(con)
    seed_watch(con, "Marigold Static")
    net.replies["push"] = [urllib.error.HTTPError(PUSH, 500, "Server Error", {}, None)]
    with caplog.at_level(logging.DEBUG):
        assert watch.run(con, NOW)["notified"] == 0
    assert PUSH not in caplog.text and "stubs-watch-a1b2c3" not in caplog.text
    assert all(r["notified_at"] is None for r in sighting_rows(con).values())
    assert watch.run(con, "2026-11-14 10:00:00")["notified"] == 1  # Z7MS0002 (Z7MS0004 can't be placed)
    assert watch.run(con, "2026-11-14 11:00:00")["notified"] == 0
    assert len(net.of("push")) == 2


def test_no_url_means_no_push_and_the_card_still_shows_them(con, net, monkeypatch):
    monkeypatch.setattr(settings, "WATCH_NOTIFY_URL", "")
    area(con)
    seed_watch(con, "Marigold Static")
    assert watch.run(con, NOW)["notified"] == 0
    assert net.of("push") == []
    assert [s["venue"] for s in watch.sightings(con)] == ["The Bellwether"]


def test_a_sighting_you_have_tickets_for_is_shown_but_never_pushed(con, net):
    area(con)
    (ms,) = seed_watch(con, "Marigold Static")
    v = mk_venue(con, "Bellwether", "Oakland", "CA", "US")
    show = mk_show(con, "Marigold Static", "2027-04-02", v, status="upcoming")
    assert watch.run(con, NOW)["notified"] == 0
    (card,) = watch.sightings(con)
    assert card["have"] == show and card["artist"] == "Marigold Static"
    assert watch.artist_watch(con, ms)["sightings"][0]["have"] == show


def test_no_source_on_still_expires_and_pushes(con, net, monkeypatch):
    monkeypatch.setattr(settings, "TICKETMASTER_API_KEY", "")
    (ms,) = seed_watch(con, "Marigold Static")
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city) "
                "VALUES (?, 'email', 'e1', '2027-01-01', 'Granite Hall', 'Oakland')", (ms,))
    assert watch.run(con, NOW) == {"checked": 0, "new": 0, "notified": 1}
    assert net.of("tm") == []


# ---------------------------------------------------------------- notify
ITEMS = [{"id": 1, "artist": "Marigold Static", "date": "2027-03-14", "venue": "Old Mill Theatre", "city": "Portland",
          "region": "OR", "country": "US", "url": "https://www.ticketmaster.com/event/Z7MS0001", "source": "ticketmaster"},
         {"id": 2, "artist": "Cold Harbor", "date": "2027-02-20", "venue": "Granite Hall", "city": "Oakland",
          "region": "CA", "country": "US", "url": None, "source": "email"}]


def test_ntfy_body_and_headers():
    body, headers = notify.payload(ITEMS, "ntfy")
    assert body.decode() == ("Marigold Static · Mar 14, 2027 · Old Mill Theatre, Portland\n"
                             "https://www.ticketmaster.com/event/Z7MS0001\n"
                             "Cold Harbor · Feb 20, 2027 · Granite Hall, Oakland")
    assert headers == {"Content-Type": "text/plain; charset=utf-8", "Title": "Stubs: 2 new concerts",
                       "Click": "https://www.ticketmaster.com/event/Z7MS0001"}
    assert notify.payload(ITEMS[1:], "ntfy")[1] == {"Content-Type": "text/plain; charset=utf-8",
                                                    "Title": "Stubs: 1 new concert"}


def test_json_body():
    body, headers = notify.payload(ITEMS, "json")
    assert headers == {"Content-Type": "application/json"}
    data = json.loads(body)
    assert data["title"] == "Stubs: 2 new concerts"
    assert data["message"].startswith("Marigold Static · Mar 14, 2027 · Old Mill Theatre, Portland\n")
    assert data["items"][1] == {"artist": "Cold Harbor", "date": "2027-02-20", "venue": "Granite Hall",
                                "city": "Oakland", "region": "CA", "country": "US", "url": None, "source": "email"}


def test_json_format_is_sent_to_a_webhook(con, net, monkeypatch):
    monkeypatch.setattr(settings, "WATCH_NOTIFY_URL", "https://hooks.example.com/api/webhook/stubs-x1y2")
    monkeypatch.setattr(settings, "WATCH_NOTIFY_FORMAT", "json")
    area(con)
    seed_watch(con, "Marigold Static")
    watch.run(con, NOW)
    (push, _), = net.of("push")
    assert push.get_header("Content-type") == "application/json"
    assert [i["venue"] for i in json.loads(push.data)["items"]] == ["The Bellwether"]


# ---------------------------------------------------------------- the card: add and dismiss
def test_add_creates_an_upcoming_show_with_a_source_that_shows_under_coming_up(editor, con, net, clock):
    clock.set("2026-11-14")
    area(con)
    seed_watch(con, "Marigold Static")
    assert editor.post("/admin/api/watch/run").get_json() == {"checked": 1, "new": 1, "notified": 1}
    (s,) = editor.get("/admin/api/watch/sightings").get_json()["sightings"]
    assert (s["artist"], s["date"], s["venue"], s["have"]) == ("Marigold Static", "2027-04-02", "The Bellwether", None)
    r = editor.post(f"/admin/api/watch/sightings/{s['id']}/add")
    assert r.status_code == 201
    show = r.get_json()["show_id"]
    d = editor.get(f"/admin/api/shows/{show}").get_json()
    assert (d["status"], d["date"], d["billing"], d["venue"]["label"]) == ("upcoming", "2027-04-02", "Marigold Static",
                                                                          "The Bellwether")
    assert [(e["name"], e["role"]) for e in d["lineup"]] == [("Marigold Static", "headliner")]
    assert [(x["label"], x["url"]) for x in d["sources"]] == [("Ticketmaster", "https://www.ticketmaster.com/event/Z7MS0002")]
    assert [u["id"] for u in editor.get("/admin/api/upcoming").get_json()["shows"]] == [show]
    assert editor.get("/admin/api/watch/sightings").get_json()["sightings"] == []
    assert editor.post(f"/admin/api/watch/sightings/{s['id']}/add").status_code == 409
    assert editor.post("/admin/api/watch/sightings/999/add").status_code == 404


# ---------------------------------------------------------------- the email source
def email(*events):
    return {"source": "email", "events": list(events)}


EV = {"artist": "Cold Harbor", "date": "2027-02-20", "venue": "Granite Hall", "city": "Oakland", "region": "CA",
      "country": "us", "url": "https://concerts.example.com/presale", "note": "Presale Thursday"}


def test_email_sightings_are_filed_and_deduplicated(editor, con, net, clock):
    clock.set("2026-11-14")
    area(con)
    seed_watch(con, "Cold Harbor")
    net.replies["geo"] = ["nominatim/hit.json"]
    r = editor.post("/admin/api/watch/sightings", json=email(EV, {**EV, "artist": "Velvet Arcade"},
                                                            {**EV, "venue": "Old Mill Theatre", "city": "Portland",
                                                             "region": "OR"}))
    assert r.status_code == 200
    body = r.get_json()
    assert (body["new"], body["seen"]) == (1, 0)
    assert body["skipped"] == [{"index": 2, "artist": "Velvet Arcade",
                                "reason": "Not watched: add the artist on the watch page first."},
                               {"index": 3, "artist": "Cold Harbor", "reason": "Couldn't find where that is."}]
    (row,) = sighting_rows(con).values()
    assert (row["source"], row["country"], row["note"], row["status"]) == ("email", "US", "Presale Thursday", "new")
    again = editor.post("/admin/api/watch/sightings", json=email({**EV, "venue": "The Granite Hall"})).get_json()
    assert (again["new"], again["seen"]) == (0, 1)
    assert len(sighting_rows(con)) == 1


def test_email_sighting_of_an_event_ticketmaster_already_has_is_seen(editor, con, net, clock):
    clock.set("2026-11-14")
    area(con)
    seed_watch(con, "Marigold Static")
    editor.post("/admin/api/watch/run")
    ev = {"artist": "Marigold Static", "date": "2027-04-02", "venue": "Bellwether", "city": "oakland"}
    assert editor.post("/admin/api/watch/sightings", json=email(ev)).get_json()["seen"] == 1


@pytest.mark.parametrize("body, message", [
    ({"source": "gmail", "events": [EV]}, 'source must be "email".'),
    (email(), "events must be a list of 1 to 25 concerts."),
    (email(*[EV] * 26), "events must be a list of 1 to 25 concerts."),
    (email("Cold Harbor"), "Event 1: each event must be a JSON object."),
    (email(EV, {**EV, "artist": ""}), "Event 2: artist is required."),
    (email({**EV, "date": "2027-02"}), "Event 1: date must be a day (YYYY-MM-DD), today or later."),
    (email({**EV, "date": "2026-11-13"}), "Event 1: date must be a day (YYYY-MM-DD), today or later."),
    (email({**EV, "country": "USA"}), "Event 1: country must be at most 2 characters."),
    (email({**EV, "country": "1A"}), "Event 1: country must be a two-letter code such as US or CA."),
    (email({**EV, "url": "javascript:alert(1)"}), "Event 1: url must start with https:// or http://."),
    (email({**EV, "venue": "x" * 201}), "Event 1: venue must be at most 200 characters."),
    (email({**EV, "city": 7}), "Event 1: city must be text."),
])
def test_email_sightings_are_validated(editor, con, clock, body, message):
    clock.set("2026-11-14")
    seed_watch(con, "Cold Harbor")
    r = editor.post("/admin/api/watch/sightings", json=body)
    assert (r.status_code, r.get_json()) == (400, {"error": message})
    assert sighting_rows(con) == {}


def test_email_anywhere_artist_needs_no_area(editor, con, net, clock):
    clock.set("2026-11-14")
    seed_watch(con, "Cold Harbor", anywhere=True)
    r = editor.post("/admin/api/watch/sightings", json=email({**EV, "venue": "Old Mill Theatre", "city": "Portland"}))
    assert r.get_json()["new"] == 1


# ---------------------------------------------------------------- merge
def test_merge_moves_watch_and_sightings(con):
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    watch.watch_artist(con, drop, "manual", anywhere=True)
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city) "
                "VALUES (?, 'email', 'e1', '2027-01-01', 'Granite Hall', 'Oakland')", (drop,))
    merge.merge_artists(con, keep, drop)
    assert [tuple(r) for r in con.execute("SELECT artist_id, origin, anywhere FROM watch")] == [(keep, "manual", 1)]
    assert con.execute("SELECT artist_id FROM sighting").fetchone()[0] == keep


def test_merge_of_two_watched_artists_keeps_the_kept_row_and_either_anywhere(con):
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    watch.watch_artist(con, keep, "history")
    watch.watch_artist(con, drop, "lastfm", anywhere=True)
    con.execute("UPDATE watch SET muted = 1, last_checked = '2026-11-01 08:00:00' WHERE artist_id = ?", (keep,))
    merge.merge_artists(con, keep, drop)
    assert [tuple(r) for r in con.execute("SELECT artist_id, origin, anywhere, muted, last_checked FROM watch")] == \
        [(keep, "history", 1, 1, "2026-11-01 08:00:00")]


# ---------------------------------------------------------------- routes and access
def test_watch_page_lists_sources_but_never_a_key_or_the_url(editor, con, net, clock):
    clock.set("2026-11-14")
    area(con)
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Cold Harbor", "2011-06-20", v)
    harbor = mk_artist(con, "Cold Harbor")
    assert editor.post("/admin/api/watch", json={"artist_id": harbor, "origin": "history"}).status_code == 201
    assert editor.post("/admin/api/watch", json={"name": "Marigold Static", "anywhere": True}).get_json() == \
        {"artist_ids": [watch.add_watch(con, "Marigold Static")]}
    r = editor.get("/admin/api/watch")
    body = r.get_data(as_text=True)
    for secret in (TM_KEY, FM_KEY, PUSH, "stubs-watch-a1b2c3"):
        assert secret not in body
    d = r.get_json()
    assert d["sources"] == {"ticketmaster": True, "lastfm": True, "email": True, "notify": "ntfy"}
    assert [(w["name"], w["times"], w["last"], w["anywhere"]) for w in d["watched"]] == [
        ("Cold Harbor", 2, "2011-06-20", False), ("Marigold Static", 0, None, True)]
    assert [a["name"] for a in d["areas"]] == ["Home"]
    fm = editor.get("/admin/api/watch/lastfm").get_json()
    assert fm["enabled"] is True and [a["name"] for a in fm["artists"]] == ["Velvet Arcade"]
    assert FM_KEY not in editor.get("/admin/api/watch/lastfm").get_data(as_text=True)


def test_watch_routes_round_trip(editor, con):
    v = mk_venue(con, "Granite Hall")
    for d in ("2009-06-20", "2010-06-20"):
        mk_show(con, "Cold Harbor", d, v)
    mk_show(con, "Velvet Arcade", "2010-06-20", v)
    assert [s["name"] for s in editor.get("/admin/api/watch/suggestions?min_times=2").get_json()["artists"]] == \
        ["Cold Harbor"]
    assert editor.get("/admin/api/watch/suggestions?min_times=0").status_code == 400
    ids = [s["artist_id"] for s in editor.get("/admin/api/watch/suggestions").get_json()["artists"]]
    assert editor.post("/admin/api/watch", json={"artist_ids": ids, "origin": "history"}).status_code == 201
    assert editor.post("/admin/api/watch", json={"artist_ids": [ids[0], 9999]}).status_code == 404
    assert editor.post("/admin/api/watch", json={"artist_ids": "all"}).status_code == 400
    assert editor.post("/admin/api/watch", json={"name": ""}).status_code == 400
    assert editor.post("/admin/api/watch", json={"name": "Ninefold", "anywhere": "yes"}).status_code == 400
    a = editor.patch(f"/admin/api/watch/{ids[0]}", json={"muted": True}).get_json()
    assert (a["watching"], a["muted"], a["anywhere"], a["origin"]) == (True, True, False, "history")
    assert editor.delete(f"/admin/api/watch/{ids[0]}").status_code == 200
    assert editor.get(f"/admin/api/watch/artists/{ids[0]}").get_json()["watching"] is False
    assert editor.get("/admin/api/watch/artists/9999").status_code == 404
    ar = editor.post("/admin/api/watch/areas", json=HOME)
    assert ar.status_code == 201
    assert editor.post("/admin/api/watch/areas", json={**HOME, "lat": 100}).status_code == 400
    assert editor.delete(f"/admin/api/watch/areas/{ar.get_json()['id']}").status_code == 200
    assert editor.delete(f"/admin/api/watch/areas/{ar.get_json()['id']}").status_code == 404


def watch_routes():
    import app as stubs
    return [r for r in stubs.app.url_map.iter_rules() if "/watch" in r.rule]


def test_every_watch_route_is_editor_only_and_none_is_public(client, con):
    rules = watch_routes()
    assert len(rules) >= 13 and all(r.rule.startswith("/admin/api/watch") for r in rules)
    for rule in rules:
        url = rule.rule.replace("<int:artist_id>", "1").replace("<int:area_id>", "1").replace("<int:sighting_id>", "1")
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            r = client.open(url, method=method, json={})
            assert (r.status_code, r.get_json()) == (401, {"error": "Sign in to edit"}), (method, url)


def test_the_read_key_cannot_file_or_run(client, con):
    for url in ("/admin/api/watch/run", "/admin/api/watch/sightings"):
        r = client.post(url, json=email(EV), headers={"X-API-Key": READ_KEY})
        assert r.status_code == 403
    assert client.get("/admin/api/watch", headers={"X-API-Key": READ_KEY}).status_code == 200


def test_watch_only_artists_are_in_no_public_list_count_or_stat(client, con):
    v = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.27)
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    never = watch.add_watch(con, "Marigold Static")
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city) "
                "VALUES (?, 'email', 'e1', '2027-01-01', 'Granite Hall', 'Oakland')", (never,))
    assert client.get(f"/api/artists/{never}").status_code == 404
    assert client.get("/api/stats").get_json()["totals"]["artists"] == 1
    for url in ("/api/stats", "/api/shows", "/api/map", "/api/years", f"/api/venues/{v}", f"/api/shows?artist_id={never}"):
        assert "Marigold" not in client.get(url).get_data(as_text=True), url


def test_a_failing_geocoder_is_asked_once_per_run(con, net):
    area(con)
    seed_watch(con, "Marigold Static", "Cold Harbor")
    net.replies["tm"] = ["tm_hit.json", "tm_hit.json"]
    net.replies["geo"] = [TimeoutError("timed out")]
    watch.run(con, NOW)
    assert len(net.of("geo")) == 1
    assert set(sighting_rows(con)) == {"Z7MS0002"}


def test_a_run_stops_starting_lookups_after_its_budget(con, net, monkeypatch):
    seed_watch(con, "Cold Harbor", "Marigold Static", "Velvet Arcade")
    monkeypatch.setattr(watch, "BUDGET", -1.0)
    net.replies["tm"] = ["tm_empty.json"] * 3
    assert watch.run(con, NOW)["checked"] == 0
    assert net.of("tm") == []
