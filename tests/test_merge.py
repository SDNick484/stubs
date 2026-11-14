"""Task 15: merging duplicate artists and venues (merge.py) and its routes. Review focus 3: two spellings of one artist
on the same show become one lineup entry carrying both entries' sources, never an integrity error."""
import sqlite3

import pytest

import common
from common import ApiError
from conftest import READ_KEY, mk_artist, mk_show, mk_venue
from merge import merge_artists, merge_venues, search_artists, search_venues


# ---------------------------------------------------------------- helpers (this file only)
def lineup_of(con, show_id):
    """[(artist name, role), ...] in position order."""
    return [tuple(r) for r in con.execute(
        "SELECT a.name, l.role FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? "
        "ORDER BY l.position, l.id", (show_id,))]


def entry(con, show_id, artist_id):
    return con.execute("SELECT * FROM lineup WHERE show_id = ? AND artist_id = ?", (show_id, artist_id)).fetchone()


def add_source(con, show_id, label, lineup_id=None):
    return con.execute("INSERT INTO source (show_id, lineup_id, label) VALUES (?, ?, ?)",
                       (show_id, lineup_id, label)).lastrowid


def exists(con, table, row_id):
    return con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone() is not None


def version(con, show_id):
    return con.execute("SELECT version FROM show WHERE id = ?", (show_id,)).fetchone()[0]


def names_of(con, venue_id):
    return sorted(r[0] for r in con.execute("SELECT name FROM venue_name WHERE venue_id = ?", (venue_id,)))


# ---------------------------------------------------------------- artists
def test_merge_artists_moves_lineups_and_records_alias(con):
    v = mk_venue(con, "Granite Hall")
    s1 = mk_show(con, "Cold Harbor", "2009-06-20", v)
    s2 = mk_show(con, "Velvet Arcade", "2011-03-04", v, lineup=(("Velvet Arcade", "headliner"), ("Cold Harbour", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")

    merge_artists(con, keep, drop)

    assert lineup_of(con, s1) == [("Cold Harbor", "headliner")]
    assert lineup_of(con, s2) == [("Velvet Arcade", "headliner"), ("Cold Harbor", "opener")]
    assert not exists(con, "artist", drop)
    alias = con.execute("SELECT artist_id FROM artist_alias WHERE norm = ?", (common.norm("Cold Harbour"),)).fetchone()
    assert alias is not None and alias[0] == keep
    assert con.execute("SELECT COUNT(*) FROM lineup WHERE artist_id = ?", (drop,)).fetchone()[0] == 0


def test_merge_artists_moves_aliases(con):
    keep, drop = mk_artist(con, "Marigold Static"), mk_artist(con, "Marigold Statik")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('marigold static band', ?)", (drop,))
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('the marigold static', ?)", (keep,))

    merge_artists(con, keep, drop)

    aliases = dict(con.execute("SELECT norm, artist_id FROM artist_alias").fetchall())
    assert aliases == {"marigold static band": keep, "the marigold static": keep, "marigold statik": keep}


def test_merge_artists_drops_an_alias_that_names_the_kept_artist(con):
    """An alias of the dropped artist spelled like the kept one would shadow it; it goes."""
    keep, drop = mk_artist(con, "Ninefold"), mk_artist(con, "Nine Fold")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('ninefold', ?)", (drop,))
    merge_artists(con, keep, drop)
    assert dict(con.execute("SELECT norm, artist_id FROM artist_alias").fetchall()) == {"nine fold": keep}


# Review focus 3: both artists on the same show
def test_merge_artists_on_the_same_show_keeps_one_entry(con):
    v = mk_venue(con, "Summit Pavilion", "Concord")
    s = mk_show(con, "Cold Harbor", "2009-06-20", v,
                lineup=(("Cold Harbor", "headliner"), ("Cold Harbour", "opener"), ("Ninefold", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    kept, dropped = entry(con, s, keep)["id"], entry(con, s, drop)["id"]
    src_kept = add_source(con, s, "Ticketmaster", kept)
    src_dropped = add_source(con, s, "Last.fm", dropped)
    src_show = add_source(con, s, "stub scan")

    merge_artists(con, keep, drop)  # no IntegrityError from UNIQUE(show_id, artist_id)

    assert lineup_of(con, s) == [("Cold Harbor", "headliner"), ("Ninefold", "opener")]
    assert entry(con, s, keep)["id"] == kept and not exists(con, "lineup", dropped)
    sources = dict(con.execute("SELECT id, lineup_id FROM source WHERE show_id = ?", (s,)).fetchall())
    assert sources == {src_kept: kept, src_dropped: kept, src_show: None}


def test_same_show_merge_keeps_the_kept_entry_and_fills_a_missing_rating(con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Cold Harbor", "2009-06-20", v, lineup=(("Cold Harbor", "opener"), ("Cold Harbour", "headliner")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    con.execute("UPDATE lineup SET rating = 4, confidence = 'likely', confidence_note = 'from memory' "
                "WHERE show_id = ? AND artist_id = ?", (s, drop))

    merge_artists(con, keep, drop)

    e = entry(con, s, keep)
    assert (e["role"], e["position"], e["confidence"], e["confidence_note"], e["rating"]) == \
        ("opener", 1, "confirmed", None, 4)


def test_merge_artists_bumps_the_version_of_changed_shows_only(con):
    v = mk_venue(con, "Granite Hall")
    moved = mk_show(con, "Cold Harbour", "2009-06-20", v)
    untouched = mk_show(con, "Velvet Arcade", "2010-01-02", v)
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    merge_artists(con, keep, drop)
    assert (version(con, moved), version(con, untouched)) == (2, 1)


def test_merge_into_itself_is_400(con):
    a = mk_artist(con, "Cold Harbor")
    v = mk_venue(con, "Granite Hall")
    with pytest.raises(ApiError) as e:
        merge_artists(con, a, a)
    assert e.value.status == 400
    with pytest.raises(ApiError) as e:
        merge_venues(con, v, v)
    assert e.value.status == 400
    assert exists(con, "artist", a) and exists(con, "venue", v)


def test_merge_missing_record_is_404(con):
    a = mk_artist(con, "Cold Harbor")
    v = mk_venue(con, "Granite Hall")
    for fn, real in ((merge_artists, a), (merge_venues, v)):
        for keep, drop in ((real, 999), (999, real)):
            with pytest.raises(ApiError) as e:
                fn(con, keep, drop)
            assert e.value.status == 404
    assert exists(con, "artist", a) and exists(con, "venue", v)


def test_a_failed_merge_changes_nothing(con):
    """Atomic: a failure at the last step (deleting the dropped artist) rolls every earlier step back."""
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Cold Harbor", "2009-06-20", v, lineup=(("Cold Harbor", "headliner"), ("Cold Harbour", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    dropped = entry(con, s, drop)["id"]
    src = add_source(con, s, "Last.fm", dropped)
    con.execute("CREATE TEMP TRIGGER no_artist_delete BEFORE DELETE ON main.artist BEGIN SELECT RAISE(ABORT, 'boom'); END")

    with pytest.raises(sqlite3.IntegrityError):
        merge_artists(con, keep, drop)

    assert not con.in_transaction
    assert lineup_of(con, s) == [("Cold Harbor", "headliner"), ("Cold Harbour", "opener")]
    assert con.execute("SELECT lineup_id FROM source WHERE id = ?", (src,)).fetchone()[0] == dropped
    assert con.execute("SELECT COUNT(*) FROM artist_alias").fetchone()[0] == 0
    assert version(con, s) == 1


# ---------------------------------------------------------------- venues
def test_merge_venues_keeps_ticket_labels_and_names(con):
    keep = mk_venue(con, "Harbor Amphitheatre", "Concord")
    drop = mk_venue(con, "Summit Pavilion", "Concord")
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, 'Concord Pavilion Grounds', ?)",
                (drop, common.venue_norm("Concord Pavilion Grounds")))
    s1 = mk_show(con, "Cold Harbor", "2004-08-01", drop)                       # venue_label "Summit Pavilion"
    s2 = mk_show(con, "Velvet Arcade", "2012-05-05", keep)

    merge_venues(con, keep, drop)

    row = con.execute("SELECT venue_id, venue_label FROM show WHERE id = ?", (s1,)).fetchone()
    assert tuple(row) == (keep, "Summit Pavilion")
    assert con.execute("SELECT venue_id FROM show WHERE id = ?", (s2,)).fetchone()[0] == keep
    assert names_of(con, keep) == ["Concord Pavilion Grounds", "Harbor Amphitheatre", "Summit Pavilion"]
    assert not exists(con, "venue", drop) and names_of(con, drop) == []
    assert con.execute("SELECT name FROM venue WHERE id = ?", (keep,)).fetchone()[0] == "Harbor Amphitheatre"
    assert (version(con, s1), version(con, s2)) == (2, 1)


def test_merge_venues_that_differ_only_by_the(con):
    """"Bellwether" and "The Bellwether" share a venue_norm: UNIQUE(norm, venue_id) must not trip."""
    keep, drop = mk_venue(con, "The Bellwether"), mk_venue(con, "Bellwether")
    s = mk_show(con, "Marigold Static", "2015-02-14", drop)
    merge_venues(con, keep, drop)
    assert tuple(con.execute("SELECT venue_id, venue_label FROM show WHERE id = ?", (s,)).fetchone()) == (keep, "Bellwether")
    assert names_of(con, keep) == ["The Bellwether"]


def test_merge_venues_coordinates(con):
    # The kept venue's coordinates win...
    keep = mk_venue(con, "The Bellwether", lat=37.80, lon=-122.27)
    drop = mk_venue(con, "Bellwether", lat=40.0, lon=-120.0)
    merge_venues(con, keep, drop)
    assert tuple(con.execute("SELECT lat, lon FROM venue WHERE id = ?", (keep,)).fetchone()) == (37.80, -122.27)
    # ...else the dropped venue's (with where they came from), and missing region/country are filled in.
    keep = con.execute("INSERT INTO venue (name, city) VALUES ('Old Mill Theatre', 'Oakland')").lastrowid
    drop = mk_venue(con, "Old Mill", lat=37.81, lon=-122.26)
    con.execute("UPDATE venue SET geocode_source = 'nominatim' WHERE id = ?", (drop,))
    merge_venues(con, keep, drop)
    row = con.execute("SELECT lat, lon, geocode_source, region, country FROM venue WHERE id = ?", (keep,)).fetchone()
    assert tuple(row) == (37.81, -122.26, "nominatim", "CA", "US")
    # Neither has coordinates: still none.
    keep, drop = mk_venue(con, "Granite Hall"), mk_venue(con, "Granite Hall Annex")
    merge_venues(con, keep, drop)
    assert tuple(con.execute("SELECT lat, lon FROM venue WHERE id = ?", (keep,)).fetchone()) == (None, None)


# ---------------------------------------------------------------- search
def test_search_artists_by_norm_substring_and_alias(con):
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Ruth's Garden", "2001-01-01", v)
    mk_show(con, "Ruth's Garden", "2002-01-01", v)
    ruth = mk_artist(con, "Ruth's Garden")
    juniper = mk_artist(con, "Juniper & The Owls")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('the night birds', ?)", (juniper,))
    mk_artist(con, "Cold Harbor")

    assert search_artists(con, "RUTH’S") == [{"id": ruth, "name": "Ruth's Garden", "shows": 2}]
    assert [a["id"] for a in search_artists(con, "Night Birds")] == [juniper]
    assert search_artists(con, "nobody") == []
    assert [a["name"] for a in search_artists(con, "")] == ["Cold Harbor", "Juniper & The Owls", "Ruth's Garden"]


def test_search_venues_matches_former_names_and_the(con):
    bell = mk_venue(con, "The Bellwether", "Oakland", "CA", "US")
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, 'Lantern Room', 'lantern room')", (bell,))
    mk_show(con, "Cold Harbor", "2009-06-20", bell)
    mk_venue(con, "Granite Hall", "Vancouver", "BC", "CA")
    want = {"id": bell, "name": "The Bellwether", "city": "Oakland", "region": "CA", "country": "US", "shows": 1}
    assert search_venues(con, "bellwether") == [want]
    assert search_venues(con, "The Bell") == [want]
    assert search_venues(con, "lantern") == [want]
    assert [v["name"] for v in search_venues(con, "")] == ["Granite Hall", "The Bellwether"]


def test_search_returns_at_most_20_prefix_matches_first(con):
    for i in range(25):
        mk_artist(con, f"Band {i:02d} Paper Lanterns")
    lanterns = mk_artist(con, "Paper Lanterns")
    found = search_artists(con, "paper lanterns")
    assert len(found) == 20 and found[0]["id"] == lanterns


# ---------------------------------------------------------------- routes
def test_merge_routes(editor, con):
    v = mk_venue(con, "Granite Hall")
    s = mk_show(con, "Cold Harbor", "2009-06-20", v, lineup=(("Cold Harbor", "headliner"), ("Cold Harbour", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")

    r = editor.post("/admin/api/artists/merge", json={"keep": keep, "drop": drop})
    assert r.status_code == 200 and r.get_json() == {"id": keep, "name": "Cold Harbor", "shows": 1}
    assert lineup_of(con, s) == [("Cold Harbor", "headliner")]
    assert editor.post("/admin/api/artists/merge", json={"keep": keep, "drop": drop}).status_code == 404

    v2 = mk_venue(con, "Granite Hall Annex")
    r = editor.post("/admin/api/venues/merge", json={"keep": v, "drop": v2})
    assert r.status_code == 200 and r.get_json()["id"] == v and r.get_json()["shows"] == 1

    for body in ({"keep": keep}, {"keep": keep, "drop": "x"}, {"keep": True, "drop": keep}, {"keep": keep, "drop": keep},
                 {"keep": keep, "drop": 2 ** 70}, {"keep": -1, "drop": keep}):
        r = editor.post("/admin/api/artists/merge", json=body)
        assert r.status_code == 400 and r.get_json()["error"], body
    assert editor.post("/admin/api/artists/merge", data="nope").status_code == 400


def test_search_routes(editor, con):
    v = mk_venue(con, "The Bellwether")
    a = mk_artist(con, "Cold Harbor")
    assert editor.get("/admin/api/artists?q=harb").get_json() == [{"id": a, "name": "Cold Harbor", "shows": 0}]
    venues = editor.get("/admin/api/venues?q=bell").get_json()
    assert [x["id"] for x in venues] == [v] and venues[0]["city"] == "Oakland"
    assert editor.get("/admin/api/venues").status_code == 200
    r = editor.get("/admin/api/venues?q=" + "x" * 201)
    assert r.status_code == 400 and r.get_json()["error"]
    assert editor.get("/admin/api/artists?q=harb").headers["Cache-Control"] == "no-store"


def test_merge_is_editor_only(client, con):
    a, b = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    v, w = mk_venue(con, "The Bellwether"), mk_venue(con, "Bellwether")
    for path, body in (("/admin/api/artists/merge", {"keep": a, "drop": b}),
                       ("/admin/api/venues/merge", {"keep": v, "drop": w})):
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers={"X-API-Key": READ_KEY}).status_code == 403
    for path in ("/admin/api/artists?q=c", "/admin/api/venues?q=b"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Key": READ_KEY}).status_code == 200
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM venue").fetchone()[0] == 2
