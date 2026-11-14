"""browse.py and the read routes under /api/ and /admin/api/: shows, artists, venues, years and cities. The public
sees attended shows only and nothing editor-only; an upcoming show's id is 404 to the public (review focus 4)."""
import pytest

import browse
import settings
from conftest import READ_KEY, mk_artist, mk_show, mk_venue

ROW_KEYS = {"id", "date", "precision", "sort_date", "billing", "status", "venue", "run", "headliners"}
EDITOR_SHOW_KEYS = {"notes", "rating", "confidence", "confidence_note", "version", "sources"}


def lineup_id(con, show_id, artist_name):
    return con.execute("SELECT l.id FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? AND a.name = ?",
                       (show_id, artist_name)).fetchone()[0]


def add_source(con, show_id, label, url=None, lineup=None):
    return con.execute("INSERT INTO source (show_id, lineup_id, label, url) VALUES (?, ?, ?, ?)",
                       (show_id, lineup, label, url)).lastrowid


def add_venue_name(con, venue_id, name):
    import common
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)", (venue_id, name, common.venue_norm(name)))


@pytest.fixture
def fest(con):
    """Cold Harbor as headliner (s1), opener (s2), festival performer (s3) and on an upcoming show (s4)."""
    v = mk_venue(con, "The Bellwether")
    park = mk_venue(con, "Harbor Park", "Concord")
    run = con.execute("INSERT INTO run (name) VALUES ('Harbor Days 2009')").lastrowid
    s1 = mk_show(con, "Cold Harbor", "2005-04-02", v)
    s2 = mk_show(con, "Marigold Static", "2007-06", v, lineup=(("Marigold Static", "headliner"), ("Cold Harbor", "opener")))
    s3 = mk_show(con, "Harbor Days", "2009-08-15", park, run_id=run,
                 lineup=(("The Paper Lanterns", "performer"), ("Cold Harbor", "performer")))
    s4 = mk_show(con, "Cold Harbor", "2027-03-01", v, status="upcoming")
    return {"artist": mk_artist(con, "Cold Harbor"), "venue": v, "park": park, "s": (s1, s2, s3, s4)}


# ---------------------------------------------------------------- the plan's tests (Step 1)
def test_times_seen_counts_every_role(client, con, fest):
    a = fest["artist"]
    assert client.get(f"/api/artists/{a}").get_json()["times"] == 3
    assert client.get(f"/api/artists/{a}").get_json()["by_role"] == {"headliner": 1, "opener": 1, "performer": 1}


def test_public_list_hides_upcoming_editor_sees_it(client, editor, con, fest):
    s1, s2, s3, s4 = fest["s"]
    assert [r["id"] for r in client.get("/api/shows").get_json()] == [s3, s2, s1]
    assert [r["id"] for r in editor.get("/admin/api/shows").get_json()] == [s4, s3, s2, s1]


# Review focus 4: a friend follows a link to an upcoming show
def test_public_upcoming_show_is_404_not_403(client, editor, con, fest):
    upcoming = fest["s"][3]
    r = client.get(f"/api/shows/{upcoming}")
    assert r.status_code == 404 and r.get_json() == {"error": "Not found"}
    missing = client.get("/api/shows/999999")
    assert (missing.status_code, missing.get_data()) == (r.status_code, r.get_data())  # indistinguishable
    seen = editor.get(f"/admin/api/shows/{upcoming}")
    assert seen.status_code == 200 and seen.get_json()["status"] == "upcoming"


def test_prev_next_skip_upcoming_for_public(client, editor, con):
    v = mk_venue(con, "Granite Hall")
    a = mk_show(con, "Cold Harbor", "2005-01-01", v)
    up = mk_show(con, "Marigold Static", "2005-02-01", v, status="upcoming")
    b = mk_show(con, "Ninefold", "2005-03-01", v)
    pub = client.get(f"/api/shows/{a}").get_json()
    assert (pub["prev_id"], pub["next_id"]) == (None, b)
    assert client.get(f"/api/shows/{b}").get_json()["prev_id"] == a
    ed = editor.get(f"/admin/api/shows/{a}").get_json()
    assert ed["next_id"] == up
    ed_up = editor.get(f"/admin/api/shows/{up}").get_json()
    assert (ed_up["prev_id"], ed_up["next_id"]) == (a, b)


def test_editor_detail_has_sources_and_confidence(editor, con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Marigold Static", "2005-04-02", v, lineup=(("Marigold Static", "headliner"), ("Ninefold", "opener")),
                notes="Met Alex at the door", rating=4, confidence="likely", confidence_note="Date approximate")
    lid = lineup_id(con, s, "Ninefold")
    con.execute("UPDATE lineup SET rating = 3, confidence = 'likely', confidence_note = 'From a poster' WHERE id = ?", (lid,))
    show_src = add_source(con, s, "Ticketmaster", "https://concerts.example.com/t/1")
    opener_src = add_source(con, s, "Last.fm", lineup=lid)
    d = editor.get(f"/admin/api/shows/{s}").get_json()
    assert EDITOR_SHOW_KEYS <= set(d)
    assert (d["notes"], d["rating"], d["confidence"], d["confidence_note"], d["version"]) == \
        ("Met Alex at the door", 4, "likely", "Date approximate", 1)
    assert d["sources"] == [{"id": show_src, "label": "Ticketmaster", "url": "https://concerts.example.com/t/1"}]
    head, opener = d["lineup"]
    assert head["sources"] == [] and head["confidence"] == "confirmed" and head["rating"] is None
    assert opener == {"id": lid, "artist_id": mk_artist(con, "Ninefold"), "name": "Ninefold", "role": "opener", "position": 2,
                      "rating": 3, "confidence": "likely", "confidence_note": "From a poster",
                      "sources": [{"id": opener_src, "label": "Last.fm", "url": None}]}


def test_public_detail_has_no_editor_keys(client, con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Marigold Static", "2005-04-02", v, lineup=(("Marigold Static", "headliner"), ("Ninefold", "opener")),
                notes="Met Alex", rating=4, confidence="likely", confidence_note="soft")
    add_source(con, s, "Ticketmaster")
    d = client.get(f"/api/shows/{s}").get_json()
    assert not {"notes", "rating", "confidence", "sources", "version"} & set(d)
    assert "confidence_note" not in d
    for entry in d["lineup"]:
        assert set(entry) == {"id", "artist_id", "name", "role", "position"}


def test_venue_detail_lists_former_names(client, con):
    v = mk_venue(con, "Summit Pavilion")
    add_venue_name(con, v, "Lakeside Amphitheatre")
    mk_show(con, "Cold Harbor", "2001-07-04", v, venue_label="Lakeside Amphitheatre")
    mk_show(con, "Marigold Static", "2012-07-04", v)
    d = client.get(f"/api/venues/{v}").get_json()
    assert d["name"] == "Summit Pavilion"
    assert d["names"] == ["Summit Pavilion", "Lakeside Amphitheatre"]
    assert d["times"] == 2 and [r["venue"]["label"] for r in d["shows"]] == ["Summit Pavilion", "Lakeside Amphitheatre"]


def test_years_counts_attended_only(client, editor, con, fest):
    expected = [{"year": 2005, "count": 1}, {"year": 2007, "count": 1}, {"year": 2009, "count": 1}]
    assert client.get("/api/years").get_json() == expected
    assert editor.get("/admin/api/years").get_json() == expected  # upcoming shows count nowhere, not even for editors


def test_year_only_show_lists_under_its_year(client, con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Cold Harbor", "2027", v)
    rows = client.get("/api/shows?year=2027").get_json()
    assert [(r["id"], r["date"], r["precision"], r["sort_date"]) for r in rows] == [(s, "2027", "year", "2027-01-01")]
    assert client.get("/api/years").get_json() == [{"year": 2027, "count": 1}]
    assert client.get("/api/shows?year=2026").get_json() == []


# ---------------------------------------------------------------- the show row and the show detail
def test_show_row_shape(client, con, fest):
    s3 = fest["s"][2]
    row = next(r for r in client.get("/api/shows").get_json() if r["id"] == s3)
    assert set(row) == ROW_KEYS
    assert row == {"id": s3, "date": "2009-08-15", "precision": "day", "sort_date": "2009-08-15", "billing": "Harbor Days",
                   "status": "attended", "run": "Harbor Days 2009", "headliners": [],
                   "venue": {"id": fest["park"], "label": "Harbor Park", "city": "Concord", "region": "CA", "country": "US"}}


def test_headliners_are_headliners_and_coheadliners_in_order(con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Cold Harbor + Marigold Static", "2010-05-05", v,
                lineup=(("Cold Harbor", "coheadliner"), ("Marigold Static", "coheadliner"), ("Ninefold", "opener")))
    rows = browse.list_shows(con, editor=False)
    assert rows[0]["id"] == s and rows[0]["headliners"] == ["Cold Harbor", "Marigold Static"]


def test_show_detail_shape(client, con, fest):
    s2 = fest["s"][1]
    d = client.get(f"/api/shows/{s2}").get_json()
    assert set(d) == ROW_KEYS | {"tour", "lineup", "prev_id", "next_id"}
    assert [(e["name"], e["role"], e["position"]) for e in d["lineup"]] == [("Marigold Static", "headliner", 1),
                                                                            ("Cold Harbor", "opener", 2)]
    assert (d["prev_id"], d["next_id"]) == (fest["s"][0], fest["s"][2])
    assert browse.show_detail(con, fest["s"][3], editor=False) is None
    assert browse.show_detail(con, fest["s"][3], editor=True)["status"] == "upcoming"


def test_unknown_ids_are_404(client, editor):
    for path in ("/shows/7", "/artists/7", "/venues/7", "/cities/US/CA/Nowhere"):
        for c, prefix in ((client, "/api"), (editor, "/admin/api")):
            r = c.get(prefix + path)
            assert r.status_code == 404 and r.get_json() == {"error": "Not found"}, prefix + path


# ---------------------------------------------------------------- lists and filters
def test_list_filters(client, editor, con, fest):
    s1, s2, s3, s4 = fest["s"]
    ids = lambda c, q: [r["id"] for r in c.get("/api/shows" + q).get_json()]  # noqa: E731
    assert ids(client, f"?artist_id={fest['artist']}") == [s3, s2, s1]
    assert [r["id"] for r in editor.get(f"/admin/api/shows?artist_id={fest['artist']}").get_json()] == [s4, s3, s2, s1]
    assert ids(client, f"?venue_id={fest['park']}") == [s3]
    assert ids(client, "?city=concord") == [s3]
    assert ids(client, "?year=2007") == [s2]
    assert ids(client, "?year=2027") == []
    assert [r["id"] for r in editor.get("/admin/api/shows?year=2027").get_json()] == [s4]


def test_bad_query_number_is_400(client):
    r = client.get("/api/shows?year=soon")
    assert r.status_code == 400 and r.get_json() == {"error": "year must be a whole number."}
    assert client.get("/api/shows?artist_id=x").status_code == 400


# ---------------------------------------------------------------- artists
def test_artist_detail(client, editor, con, fest):
    a, (s1, s2, s3, s4) = fest["artist"], fest["s"]
    d = client.get(f"/api/artists/{a}").get_json()
    assert (d["id"], d["name"], d["first"], d["last"]) == (a, "Cold Harbor", "2005-04-02", "2009-08-15")
    assert [(r["id"], r["role"]) for r in d["shows"]] == [(s3, "performer"), (s2, "opener"), (s1, "headliner")]
    assert "upcoming" not in d
    e = editor.get(f"/admin/api/artists/{a}").get_json()
    assert e["times"] == 3 and len(e["shows"]) == 3
    assert [(r["id"], r["role"], r["status"]) for r in e["upcoming"]] == [(s4, "headliner", "upcoming")]


def test_artist_on_upcoming_shows_only_is_404_to_the_public(client, editor, con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Juniper & The Owls", "2027-02-02", v, status="upcoming")
    a = mk_artist(con, "Juniper & The Owls")
    assert client.get(f"/api/artists/{a}").status_code == 404
    e = editor.get(f"/admin/api/artists/{a}").get_json()
    assert (e["times"], e["first"], e["last"], e["by_role"], e["shows"]) == (0, None, None, {}, [])
    assert len(e["upcoming"]) == 1


# ---------------------------------------------------------------- venues
def test_venue_detail(client, editor, con, fest):
    v, (s1, s2, _, s4) = fest["venue"], fest["s"]
    d = client.get(f"/api/venues/{v}").get_json()
    assert set(d) == {"id", "name", "names", "city", "region", "country", "lat", "lon", "times", "shows"}
    assert (d["name"], d["city"], d["times"]) == ("The Bellwether", "Oakland", 2)
    assert [r["id"] for r in d["shows"]] == [s2, s1]
    e = editor.get(f"/admin/api/venues/{v}").get_json()
    assert e["times"] == 2 and [r["id"] for r in e["upcoming"]] == [s4]


def test_venue_with_upcoming_shows_only_is_404_to_the_public(client, editor, con):
    v = mk_venue(con, "Copper Room")
    mk_show(con, "Cold Harbor", "2027-02-02", v, status="upcoming")
    assert client.get(f"/api/venues/{v}").status_code == 404
    assert editor.get(f"/admin/api/venues/{v}").get_json()["times"] == 0


def test_name_used_only_on_an_upcoming_ticket_is_editor_only(client, editor, con):
    v = mk_venue(con, "Summit Pavilion")
    mk_show(con, "Cold Harbor", "2012-07-04", v)
    add_venue_name(con, v, "Brightline Pavilion")
    mk_show(con, "Marigold Static", "2027-07-04", v, status="upcoming", venue_label="Brightline Pavilion")
    assert client.get(f"/api/venues/{v}").get_json()["names"] == ["Summit Pavilion"]
    assert editor.get(f"/admin/api/venues/{v}").get_json()["names"] == ["Summit Pavilion", "Brightline Pavilion"]


# ---------------------------------------------------------------- cities
def test_city_detail(client, editor, con, fest):
    s1, s2, s3, s4 = fest["s"]
    other = mk_venue(con, "Copper Room", "Oakland")
    s5 = mk_show(con, "Ninefold", "2011-11-11", other)
    mk_show(con, "Ninefold", "2011-11-12", mk_venue(con, "Oakland Arms", "Oakland", "MI"))  # another state's Oakland
    d = client.get("/api/cities/US/CA/Oakland").get_json()
    assert set(d) == {"city", "region", "country", "times", "shows"}
    assert (d["city"], d["region"], d["country"], d["times"]) == ("Oakland", "CA", "US", 3)
    assert [r["id"] for r in d["shows"]] == [s5, s2, s1]
    assert client.get("/api/cities/us/ca/oakland").get_json()["times"] == 3  # case doesn't matter
    e = editor.get("/admin/api/cities/US/CA/Oakland").get_json()
    assert [r["id"] for r in e["upcoming"]] == [s4]


def test_city_without_region_uses_a_dash(client, editor, con):
    """The orchestrator's link convention: a missing country or region is "-" in the path (#/city/FR/-/Lyon)."""
    lyon = mk_venue(con, "Salle Marigold", "Lyon", None, "FR")
    s = mk_show(con, "Cold Harbor", "2015-05-05", lyon)
    nowhere = mk_venue(con, "Field Stage", "Driftwood", None, None)
    t = mk_show(con, "Ninefold", "2016-06-06", nowhere)
    d = client.get("/api/cities/FR/-/Lyon").get_json()
    assert (d["city"], d["region"], d["country"], [r["id"] for r in d["shows"]]) == ("Lyon", None, "FR", [s])
    assert [r["id"] for r in client.get("/api/cities/-/-/Driftwood").get_json()["shows"]] == [t]
    assert editor.get("/admin/api/cities/FR/-/Lyon").get_json()["times"] == 1
    assert client.get("/api/cities/FR/ARA/Lyon").status_code == 404  # "-" is NULL, and a region is not "-"


def test_city_with_upcoming_shows_only_is_404_to_the_public(client, editor, con):
    v = mk_venue(con, "Granite Hall", "Vancouver", "BC", "CA")
    mk_show(con, "Cold Harbor", "2027-02-02", v, status="upcoming")
    assert client.get("/api/cities/CA/BC/Vancouver").status_code == 404
    assert editor.get("/admin/api/cities/CA/BC/Vancouver").get_json()["times"] == 0


# ---------------------------------------------------------------- access
def test_admin_reads_need_an_editor(client, con, fest):
    for path in ("/shows", f"/shows/{fest['s'][0]}", f"/artists/{fest['artist']}", f"/venues/{fest['venue']}", "/years",
                 "/cities/US/CA/Oakland"):
        r = client.get("/admin/api" + path)
        assert r.status_code == 401 and r.get_json() == {"error": "Sign in to edit"}, path


def test_read_key_reads_editor_view(client, con, fest):
    r = client.get(f"/admin/api/shows/{fest['s'][3]}", headers={"X-API-Key": READ_KEY})
    assert r.status_code == 200 and r.get_json()["version"] == 1


def test_public_routes_are_public_even_for_editors(editor, con, fest):
    """/api/ is the public view whoever asks: an editor's pages fetch /admin/api/."""
    assert editor.get(f"/api/shows/{fest['s'][3]}").status_code == 404
    assert "notes" not in editor.get(f"/api/shows/{fest['s'][0]}").get_json()


def test_public_view_off(client, editor, con, fest, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    assert client.get("/api/shows").status_code == 403
    assert editor.get("/api/shows").status_code == 200
