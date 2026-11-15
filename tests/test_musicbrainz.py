"""Post-v1 #45: MusicBrainz links on artists (musicbrainz.py, its routes, merge, import and the public artist page).
urlopen and sleep are patched; the replies are recorded fixtures under tests/fixtures/musicbrainz/. Nothing touches the
network."""
import io
import os
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

import imports
import merge
import musicbrainz
import settings
from common import ApiError
from conftest import mk_artist, mk_show, mk_venue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "musicbrainz")
HARBOR = "11111111-1111-4111-8111-111111111111"
HARBOUR = "22222222-2222-4222-8222-222222222222"
LANTERNS = "33333333-3333-4333-8333-333333333333"


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
    """A queue of replies (fixture name, or an exception) for urlopen; records requests and sleeps."""
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

    monkeypatch.setattr(musicbrainz.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(musicbrainz.time, "sleep", sleep)
    monkeypatch.setattr(musicbrainz.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(musicbrainz, "_last_call", None)
    return state


def mb(con, artist_id):
    r = con.execute("SELECT mbid, mb_disambiguation FROM artist WHERE id = ?", (artist_id,)).fetchone()
    return (r["mbid"], r["mb_disambiguation"])


# ---------------------------------------------------------------- search
def test_search_hit_has_two_candidates(net):
    assert musicbrainz.search("Cold Harbor") == [
        {"mbid": HARBOR, "name": "Cold Harbor", "disambiguation": "UK post-punk band", "country": "GB", "score": 100},
        {"mbid": HARBOUR, "name": "Cold Harbour", "disambiguation": "US folk duo", "country": "US", "score": 86}]


def test_search_single_hit(net):
    net.replies = ["exact.json"]
    r = musicbrainz.search("The Paper Lanterns")
    assert [(c["mbid"], c["disambiguation"], c["score"]) for c in r] == [(LANTERNS, None, 100)]


def test_search_miss_is_empty(net):
    net.replies = ["miss.json"]
    assert musicbrainz.search("Nobody At All") == []


def test_search_request_has_ua_query_and_timeout(net):
    musicbrainz.search('The "Paper" Lanterns')
    req, timeout = net.requests[0]
    assert req.get_header("User-agent") == settings.UA
    assert timeout == 10
    url = urlsplit(req.full_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == settings.MUSICBRAINZ_URL + "/artist"
    assert parse_qs(url.query) == {"query": ['artist:"The \\"Paper\\" Lanterns"'], "fmt": ["json"], "limit": ["5"]}


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("https://musicbrainz.org/ws/2/artist", 503, "Service Unavailable", {}, None),
    TimeoutError("timed out"), urllib.error.URLError("refused")])
def test_search_failures_are_502(net, failure):
    net.replies = [failure]
    with pytest.raises(ApiError) as e:
        musicbrainz.search("Cold Harbor")
    assert (e.value.status, e.value.message) == (502, "Lookup failed")


def test_search_unreadable_answer_is_502(net, monkeypatch):
    monkeypatch.setattr(musicbrainz.urllib.request, "urlopen", lambda req, timeout=None: Resp(b"<html>nope</html>"))
    with pytest.raises(ApiError) as e:
        musicbrainz.search("Cold Harbor")
    assert e.value.status == 502


def test_two_searches_are_at_least_a_second_apart(net):
    musicbrainz.search("Cold Harbor")
    assert net.sleeps == []
    musicbrainz.search("Cold Harbor")
    assert len(net.sleeps) == 1 and net.sleeps[0] >= 1.0 - 1e-9
    net.now += 5  # a long gap needs no wait
    musicbrainz.search("Cold Harbor")
    assert len(net.sleeps) == 1


# ---------------------------------------------------------------- exact
def test_exact_needs_score_100_equal_norm_and_no_second_100(net):
    hit = musicbrainz.search("Cold Harbor")
    assert musicbrainz.exact("Cold Harbor", hit) == HARBOR
    assert musicbrainz.exact("Cold Harbour", hit) is None  # the top result is not norm-equal
    net.replies = ["tie.json"]
    tie = musicbrainz.search("Marigold Static")
    assert musicbrainz.exact("Marigold Static", tie) is None  # two results at 100
    assert musicbrainz.exact("Marigold Static", []) is None
    low = [dict(hit[0], score=99)]
    assert musicbrainz.exact("Cold Harbor", low) is None


# ---------------------------------------------------------------- link, unlink, unlinked
def test_link_stores_and_normalizes_the_uuid(con):
    a = mk_artist(con, "Cold Harbor")
    r = musicbrainz.link(con, a, HARBOR.upper(), "UK post-punk band")
    assert r == {"id": a, "name": "Cold Harbor", "mbid": HARBOR, "mb_disambiguation": "UK post-punk band"}
    assert mb(con, a) == (HARBOR, "UK post-punk band")


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", 7, "1111111111111111111111111111111111", HARBOR + "0"])
def test_link_rejects_a_bad_uuid(con, bad):
    a = mk_artist(con, "Cold Harbor")
    with pytest.raises(ApiError) as e:
        musicbrainz.link(con, a, bad, None)
    assert e.value.status == 400
    assert mb(con, a) == (None, None)


def test_link_unknown_artist_is_404(con):
    with pytest.raises(ApiError) as e:
        musicbrainz.link(con, 999, HARBOR, None)
    assert e.value.status == 404


def test_link_rejects_a_too_long_or_non_text_disambiguation(con):
    a = mk_artist(con, "Cold Harbor")
    for bad in ("x" * 501, 5):
        with pytest.raises(ApiError) as e:
            musicbrainz.link(con, a, HARBOR, bad)
        assert e.value.status == 400


def test_link_a_taken_mbid_is_409_naming_the_other_artist(con):
    a, b = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    musicbrainz.link(con, a, HARBOR, None)
    with pytest.raises(ApiError) as e:
        musicbrainz.link(con, b, HARBOR, None)
    assert (e.value.status, e.value.message) == (409, "Another artist has this MusicBrainz ID: merge them.")
    assert e.value.extra == {"artist_id": a}
    assert mb(con, b) == (None, None)


def test_relinking_the_same_artist_changes_it(con):
    a = mk_artist(con, "Cold Harbor")
    musicbrainz.link(con, a, HARBOR, "one")
    musicbrainz.link(con, a, HARBOR, "two")
    musicbrainz.link(con, a, HARBOUR, None)
    assert mb(con, a) == (HARBOUR, None)


def test_unlink(con):
    a = mk_artist(con, "Cold Harbor")
    musicbrainz.link(con, a, HARBOR, "x")
    assert musicbrainz.unlink(con, a)["mbid"] is None
    assert mb(con, a) == (None, None)
    musicbrainz.unlink(con, a)  # again: nothing to do
    with pytest.raises(ApiError):
        musicbrainz.unlink(con, 999)


def test_unlinked_lists_the_busiest_first(con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Cold Harbor", "2010-06-20", v)
    mk_show(con, "Marigold Static", "2011-06-20", v)
    linked = mk_artist(con, "Ninefold")
    musicbrainz.link(con, linked, HARBOR, None)
    got = musicbrainz.unlinked(con)
    assert [(r["name"], r["shows"]) for r in got] == [("Cold Harbor", 2), ("Marigold Static", 1)]
    assert set(got[0]) == {"id", "name", "shows"}


def test_suggest_searches_the_artists_name(con, net):
    a = mk_artist(con, "Cold Harbor")
    s = musicbrainz.suggest(con, a)
    assert s["exact"] == HARBOR and len(s["candidates"]) == 2
    assert parse_qs(urlsplit(net.requests[0][0].full_url).query)["query"] == ['artist:"Cold Harbor"']
    with pytest.raises(ApiError):
        musicbrainz.suggest(con, 999)


# ---------------------------------------------------------------- routes
def test_routes_need_an_editor(client):
    for method, url in (("get", "/admin/api/artists/unlinked"), ("get", "/admin/api/artists/1/musicbrainz/search"),
                        ("put", "/admin/api/artists/1/musicbrainz"), ("delete", "/admin/api/artists/1/musicbrainz")):
        assert getattr(client, method)(url).status_code == 401


def test_routes_link_search_and_unlink(editor, con, net):
    a = mk_artist(con, "Cold Harbor")
    assert editor.get("/admin/api/artists/unlinked").get_json() == {"artists": [{"id": a, "name": "Cold Harbor", "shows": 0}]}
    s = editor.get(f"/admin/api/artists/{a}/musicbrainz/search").get_json()
    assert s["exact"] == HARBOR and s["candidates"][1]["mbid"] == HARBOUR
    r = editor.put(f"/admin/api/artists/{a}/musicbrainz", json={"mbid": HARBOR, "disambiguation": "UK post-punk band"})
    assert r.get_json()["mbid"] == HARBOR
    assert editor.get("/admin/api/artists/unlinked").get_json() == {"artists": []}
    assert editor.get(f"/admin/api/artists/{a}").get_json()["mb_disambiguation"] == "UK post-punk band"
    assert editor.delete(f"/admin/api/artists/{a}/musicbrainz").get_json()["mbid"] is None


def test_route_409_carries_the_other_artist(editor, con):
    a, b = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    assert editor.put(f"/admin/api/artists/{a}/musicbrainz", json={"mbid": HARBOR}).status_code == 200
    r = editor.put(f"/admin/api/artists/{b}/musicbrainz", json={"mbid": HARBOR})
    assert r.status_code == 409
    assert r.get_json() == {"error": "Another artist has this MusicBrainz ID: merge them.", "artist_id": a}


def test_route_bad_uuid_is_400_and_a_lookup_failure_502(editor, con, net):
    a = mk_artist(con, "Cold Harbor")
    assert editor.put(f"/admin/api/artists/{a}/musicbrainz", json={"mbid": "nope"}).status_code == 400
    net.replies = [urllib.error.HTTPError("u", 503, "x", {}, None)]
    r = editor.get(f"/admin/api/artists/{a}/musicbrainz/search")
    assert (r.status_code, r.get_json()) == (502, {"error": "Lookup failed"})


# ---------------------------------------------------------------- public and editor reads
def test_the_public_artist_carries_the_mbid_but_not_the_note(client, con, editor):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    a = mk_artist(con, "Cold Harbor")
    assert client.get(f"/api/artists/{a}").get_json()["mbid"] is None
    musicbrainz.link(con, a, HARBOR, "UK post-punk band")
    pub = client.get(f"/api/artists/{a}").get_json()
    assert pub["mbid"] == HARBOR and "mb_disambiguation" not in pub
    adm = editor.get(f"/admin/api/artists/{a}").get_json()
    assert (adm["mbid"], adm["mb_disambiguation"]) == (HARBOR, "UK post-punk band")


# ---------------------------------------------------------------- merge
def test_merge_keeps_the_kept_artists_mbid(con):
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    musicbrainz.link(con, keep, HARBOR, "kept")
    musicbrainz.link(con, drop, HARBOUR, "dropped")
    merge.merge_artists(con, keep, drop)
    assert mb(con, keep) == (HARBOR, "kept")


def test_merge_adopts_the_dropped_artists_mbid(con):
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    musicbrainz.link(con, drop, HARBOUR, "dropped")
    merge.merge_artists(con, keep, drop)
    assert mb(con, keep) == (HARBOUR, "dropped")


def test_merge_with_neither_linked_stays_unlinked(con):
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    merge.merge_artists(con, keep, drop)
    assert mb(con, keep) == (None, None)


# ---------------------------------------------------------------- resolve and import
def test_resolve_artist_checks_the_mbid_first(con):
    import records
    a = mk_artist(con, "Cold Harbor")
    musicbrainz.link(con, a, HARBOR, None)
    other = mk_artist(con, "Cold Harbour")
    assert records.resolve_artist(con, "Cold Harbour", HARBOR) == a  # the id beats the name
    assert records.resolve_artist(con, "Cold Harbour") == other
    assert records.resolve_artist(con, "Cold Harbour", HARBOUR) == other  # an unknown id falls back to the name
    assert records.resolve_artist(con, "", HARBOR) == a
    assert records.resolve_artist(con, "Nobody", HARBOUR) is None


def test_a_new_artist_made_from_an_mbid_act_stores_it(con):
    import records
    a = records.get_or_create_artist(con, "The Paper Lanterns", LANTERNS)
    assert mb(con, a) == (LANTERNS, None)
    assert records.get_or_create_artist(con, "Paper Lanterns, The", LANTERNS) == a


def test_validate_proposal_checks_an_acts_mbid():
    base = {"date": "2025-07-19", "billing": "X", "venue": "V", "city": "C", "status": "attended"}
    ok = imports.validate_proposal({**base, "acts": [{"name": "Cold Harbor", "mbid": HARBOR.upper()}]})
    assert ok["acts"][0]["mbid"] == HARBOR
    assert "mbid" not in imports.validate_proposal({**base, "acts": [{"name": "Cold Harbor"}]})["acts"][0]
    for bad in ("nope", 5, [HARBOR]):
        with pytest.raises(ApiError) as e:
            imports.validate_proposal({**base, "acts": [{"name": "Cold Harbor", "mbid": bad}]})
        assert e.value.status == 400


def batch(c, acts):
    show = {"date": "2016-12-01", "status": "attended", "billing": acts[0]["name"], "venue": "The Bellwether",
            "city": "Oakland", "region": "CA", "country": "US", "acts": acts}
    r = c.post("/admin/api/imports", json={"label": "Batch", "shows": [show]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_an_import_act_with_an_mbid_matches_a_different_spelling(editor, con):
    a = mk_artist(con, "Cold Harbor")
    musicbrainz.link(con, a, HARBOR, None)
    bid = batch(editor, [{"name": "Coldharbor Collective", "mbid": HARBOR}])
    assert editor.post(f"/admin/api/imports/{bid}/apply").get_json() == {"created": 1}
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 1
    assert con.execute("SELECT artist_id FROM lineup").fetchone()[0] == a


def test_an_import_with_an_mbid_marks_the_show_as_matching_by_artist(editor, con):
    v = mk_venue(con, "The Bellwether")
    a = mk_artist(con, "Cold Harbor")
    musicbrainz.link(con, a, HARBOR, None)
    mk_show(con, "Cold Harbor", "2016-12-01", v)
    bid = batch(editor, [{"name": "Coldharbor Collective", "mbid": HARBOR}])
    assert editor.get(f"/admin/api/imports/{bid}").get_json()["rows"][0]["verdict"] == "have"


def test_an_import_creates_a_new_artist_with_the_mbid(editor, con):
    bid = batch(editor, [{"name": "The Paper Lanterns", "mbid": LANTERNS}])
    editor.post(f"/admin/api/imports/{bid}/apply")
    assert con.execute("SELECT mbid FROM artist WHERE name = 'The Paper Lanterns'").fetchone()[0] == LANTERNS


def test_an_import_with_a_bad_mbid_is_400(editor):
    r = editor.post("/admin/api/imports", json={"label": "B", "shows": [
        {"date": "2016-12-01", "status": "attended", "billing": "X", "venue": "V", "city": "C",
         "acts": [{"name": "X", "mbid": "nope"}]}]})
    assert r.status_code == 400
