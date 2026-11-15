"""Post-v1 #44: song counts, the artist song catalog, song pages and song merging (songs.py and its routes). Plays are
setlist entries on attended shows; every act and song here is invented."""
import pytest

import records
import songs
from common import ApiError
from conftest import READ_KEY, mk_artist, mk_show, mk_venue
from merge import merge_artists


def play(con, show_id, artist, titles, source="typed"):
    """Store a setlist for `artist` on the show. A title is "Song" or ("Song", set_name, encore, note)."""
    aid = mk_artist(con, artist)
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ? AND artist_id = ?", (show_id, aid)).fetchone()["id"]
    con.execute("INSERT OR IGNORE INTO setlist (lineup_id, source_label) VALUES (?, ?)", (lid, source))
    start = con.execute("SELECT COALESCE(MAX(position), 0) FROM setlist_entry WHERE lineup_id = ?", (lid,)).fetchone()[0]
    for i, t in enumerate(titles, start=start + 1):
        title, set_name, encore, note = (t, None, 0, None) if isinstance(t, str) else t
        con.execute("INSERT INTO setlist_entry (lineup_id, position, set_name, encore, song_id, note) VALUES (?, ?, ?, ?, ?, ?)",
                    (lid, i, set_name, encore, records.get_or_create_song(con, aid, title), note))
    return aid


def by_title(rows):
    return {r["title"]: r for r in rows}


@pytest.fixture
def venue(con):
    return mk_venue(con, "Granite Hall")


@pytest.fixture
def lanterns(con, venue):
    """Three attended nights, and an upcoming one with no setlist."""
    a = mk_show(con, "The Paper Lanterns", "2019-05-04", venue)
    b = mk_show(con, "The Paper Lanterns", "2021-06-05", venue)
    c = mk_show(con, "The Paper Lanterns", "2023-07-06", venue)
    play(con, a, "The Paper Lanterns", ["Lantern Light", "Paper Boats", "Night Ferry"])
    play(con, b, "The Paper Lanterns", ["Lantern Light", "Paper Boats"])
    play(con, c, "The Paper Lanterns", ["Lantern Light", "Candlewick"])
    return mk_artist(con, "The Paper Lanterns")


# ---------------------------------------------------------------- counting
def test_artist_songs_counts_plays_shows_first_and_last(con, lanterns):
    rows = songs.artist_songs(con, lanterns)
    assert [(r["title"], r["plays"], r["shows"]) for r in rows] == [
        ("Lantern Light", 3, 3), ("Paper Boats", 2, 2), ("Candlewick", 1, 1), ("Night Ferry", 1, 1)]
    top = rows[0]
    assert (top["first"], top["last"], top["in_catalog"]) == ("2019-05-04", "2023-07-06", False)
    assert set(top) == {"id", "title", "plays", "shows", "first", "last", "in_catalog"}


def test_a_song_played_twice_in_one_night_is_two_plays_one_show(con, venue):
    s = mk_show(con, "Cold Harbor", "2020-01-02", venue)
    play(con, s, "Cold Harbor", ["Low Tide Hymn", "Night Ferry", "Low Tide Hymn"])
    row = by_title(songs.artist_songs(con, mk_artist(con, "Cold Harbor")))["Low Tide Hymn"]
    assert (row["plays"], row["shows"]) == (2, 1)
    detail = songs.song_detail(con, row["id"])
    assert detail["plays"] == 2 and len(detail["shows"]) == 2 and len({s["id"] for s in detail["shows"]}) == 1


def test_openers_and_festival_performers_setlists_count(con, venue):
    s = mk_show(con, "Fog Fest", "2022-08-01", venue,
                lineup=(("Cold Harbor", "headliner"), ("Marigold Static", "opener"), ("Ninefold", "performer")))
    play(con, s, "Marigold Static", ["Glass Orchard"])
    play(con, s, "Ninefold", ["Ninefold Waltz"])
    for name, title in (("Marigold Static", "Glass Orchard"), ("Ninefold", "Ninefold Waltz")):
        assert [r["plays"] for r in songs.artist_songs(con, mk_artist(con, name))] == [1], title


def test_only_attended_shows_count(con, venue, lanterns):
    up = mk_show(con, "The Paper Lanterns", "2099-01-01", venue, status="upcoming")
    # Upcoming shows can't get a setlist through the app; force one to prove the queries filter anyway.
    play(con, up, "The Paper Lanterns", ["Lantern Light", "Hidden Verse"])
    rows = by_title(songs.artist_songs(con, lanterns))
    assert rows["Lantern Light"]["plays"] == 3
    assert "Hidden Verse" not in rows
    assert songs.song_detail(con, records.resolve_song(con, lanterns, "Hidden Verse")) is None
    assert all(s["status"] == "attended" for s in songs.song_detail(con, rows["Lantern Light"]["id"])["shows"])


def test_song_detail_shape(con, venue):
    a = mk_show(con, "The Paper Lanterns", "2019-05-04", venue)
    b = mk_show(con, "The Paper Lanterns", "2021-06-05", venue)
    play(con, a, "The Paper Lanterns", ["Intro", ("Lantern Light", "Set 2", 0, None)])
    play(con, b, "The Paper Lanterns", [("Lantern Light", None, 1, "with strings")])
    art = mk_artist(con, "The Paper Lanterns")
    d = songs.song_detail(con, records.resolve_song(con, art, "Lantern Light"))
    assert (d["title"], d["plays"], d["artist"]) == ("Lantern Light", 2, {"id": art, "name": "The Paper Lanterns"})
    assert (d["first"], d["last"], d["aliases"], d["in_catalog"]) == ("2019-05-04", "2021-06-05", [], False)
    newest, oldest = d["shows"]  # newest night first
    assert (newest["id"], newest["set_name"], newest["encore"], newest["note"]) == (b, None, True, "with strings")
    assert (oldest["id"], oldest["set_name"], oldest["encore"], oldest["note"]) == (a, "Set 2", False, None)
    assert oldest["venue"]["label"] == "Granite Hall" and oldest["date"] == "2019-05-04"


def test_song_detail_unknown_is_none(con):
    assert songs.song_detail(con, 999) is None


# ---------------------------------------------------------------- the catalog
def test_catalog_paste_creates_and_flags_songs(con, lanterns):
    r = songs.set_catalog(con, lanterns, ["Lantern Light", "Moth Orchard", "Saltwater Choir"])
    assert r == {"added": 3, "already": 0}
    rows = by_title(songs.artist_songs(con, lanterns))
    assert rows["Moth Orchard"]["plays"] == 0 and rows["Moth Orchard"]["in_catalog"]
    assert rows["Lantern Light"]["in_catalog"] and rows["Lantern Light"]["plays"] == 3
    assert not rows["Candlewick"]["in_catalog"]


def test_catalog_paste_twice_adds_nothing(con, lanterns):
    first = songs.set_catalog(con, lanterns, ["Moth Orchard", "Lantern Light"])
    assert first == {"added": 2, "already": 0}
    before = con.execute("SELECT COUNT(*) FROM song").fetchone()[0]
    assert songs.set_catalog(con, lanterns, ["Moth Orchard", "Lantern Light"]) == {"added": 0, "already": 2}
    assert con.execute("SELECT COUNT(*) FROM song").fetchone()[0] == before


def test_catalog_resolves_aliases(con, lanterns):
    boats = records.resolve_song(con, lanterns, "Paper Boats")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'paper boat', ?)", (lanterns, boats))
    before = con.execute("SELECT COUNT(*) FROM song").fetchone()[0]
    assert songs.set_catalog(con, lanterns, ["Paper Boat", "Moth Orchard"]) == {"added": 2, "already": 0}
    assert con.execute("SELECT COUNT(*) FROM song").fetchone()[0] == before + 1


def test_parse_catalog_strips_numbering_and_skips_headings(con):
    text = "Album: Low Tide\n1. Lantern Light\n02) Paper Boats\n\n  Night Ferry  \nSide B:\n- Candlewick\nSet 2\n#4 Moth Orchard\n\"Paper Boats\" (live)\n"
    assert songs.parse_catalog(text) == ["Lantern Light", "Paper Boats", "Night Ferry", "Candlewick", "Moth Orchard"]


def test_never_heard_lists_unplayed_catalog_songs_only(con, lanterns):
    songs.set_catalog(con, lanterns, ["Lantern Light", "Moth Orchard", "Saltwater Choir"])
    assert [r["title"] for r in songs.never_heard(con, lanterns)] == ["Moth Orchard", "Saltwater Choir"]
    # a heard song outside the catalog is not "never heard", and an artist without a catalog has none
    assert songs.never_heard(con, mk_artist(con, "Cold Harbor")) == []


def test_removing_a_played_song_from_the_catalog_keeps_it_and_its_plays(con, lanterns):
    songs.set_catalog(con, lanterns, ["Lantern Light"])
    sid = records.resolve_song(con, lanterns, "Lantern Light")
    assert songs.remove_from_catalog(con, sid) == {"id": sid, "in_catalog": False, "deleted": False}
    row = by_title(songs.artist_songs(con, lanterns))["Lantern Light"]
    assert row["plays"] == 3 and not row["in_catalog"]


def test_removing_an_unplayed_unaliased_song_deletes_it_but_an_aliased_one_stays(con, lanterns):
    songs.set_catalog(con, lanterns, ["Moth Orchard", "Saltwater Choir"])
    moth = records.resolve_song(con, lanterns, "Moth Orchard")
    salt = records.resolve_song(con, lanterns, "Saltwater Choir")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'salt choir', ?)", (lanterns, salt))
    assert songs.remove_from_catalog(con, moth)["deleted"] is True
    assert con.execute("SELECT 1 FROM song WHERE id = ?", (moth,)).fetchone() is None
    assert songs.remove_from_catalog(con, salt)["deleted"] is False
    assert con.execute("SELECT 1 FROM song WHERE id = ?", (salt,)).fetchone() is not None


def test_remove_unknown_song_is_404(con):
    with pytest.raises(ApiError) as e:
        songs.remove_from_catalog(con, 999)
    assert e.value.status == 404


def test_set_catalog_unknown_artist_is_404(con):
    with pytest.raises(ApiError) as e:
        songs.set_catalog(con, 999, ["Lantern Light"])
    assert e.value.status == 404


# ---------------------------------------------------------------- merging
def test_merge_moves_entries_records_the_alias_and_later_pastes_resolve(con, lanterns):
    keep = records.resolve_song(con, lanterns, "Lantern Light")
    drop = records.get_or_create_song(con, lanterns, "Lantern Lite")
    play(con, con.execute("SELECT id FROM show WHERE date = '2019-05-04'").fetchone()[0], "The Paper Lanterns", ["Lantern Lite"])
    out = songs.merge_songs(con, keep, drop)
    assert out["moved"] == 1
    assert con.execute("SELECT 1 FROM song WHERE id = ?", (drop,)).fetchone() is None
    assert by_title(songs.artist_songs(con, lanterns))["Lantern Light"]["plays"] == 4
    assert songs.song_detail(con, keep)["aliases"] == ["lantern lite"]
    assert records.get_or_create_song(con, lanterns, "lantern LITE") == keep  # a later paste of the old title


def test_merge_across_artists_is_400(con, lanterns, venue):
    s = mk_show(con, "Cold Harbor", "2020-01-02", venue)
    play(con, s, "Cold Harbor", ["Lantern Light"])
    other = records.resolve_song(con, mk_artist(con, "Cold Harbor"), "Lantern Light")
    with pytest.raises(ApiError) as e:
        songs.merge_songs(con, records.resolve_song(con, lanterns, "Lantern Light"), other)
    assert e.value.status == 400
    assert con.execute("SELECT 1 FROM song WHERE id = ?", (other,)).fetchone() is not None


def test_merge_same_or_unknown_song_errors(con, lanterns):
    one = records.resolve_song(con, lanterns, "Lantern Light")
    for keep, drop, status in ((one, one, 400), (one, 999, 404), (999, one, 404)):
        with pytest.raises(ApiError) as e:
            songs.merge_songs(con, keep, drop)
        assert e.value.status == status


def test_merge_keeps_the_catalog_flag_if_either_had_it_and_moves_aliases(con, lanterns):
    keep = records.resolve_song(con, lanterns, "Paper Boats")
    drop = records.get_or_create_song(con, lanterns, "Paper Boat")
    songs.set_catalog(con, lanterns, ["Paper Boat"])
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'the boats', ?)", (lanterns, drop))
    songs.merge_songs(con, keep, drop)
    d = songs.song_detail(con, keep)
    assert d["in_catalog"] and d["aliases"] == ["paper boat", "the boats"]


def test_merge_with_both_on_one_setlist_keeps_two_entries_and_bumps_versions(con, venue):
    s = mk_show(con, "Cold Harbor", "2020-01-02", venue)
    play(con, s, "Cold Harbor", ["Low Tide Hymn", "Low Tide Hymn (reprise)"])
    art = mk_artist(con, "Cold Harbor")
    keep, drop = (records.resolve_song(con, art, t) for t in ("Low Tide Hymn", "Low Tide Hymn (reprise)"))
    before = con.execute("SELECT version FROM show WHERE id = ?", (s,)).fetchone()[0]
    songs.merge_songs(con, keep, drop)
    assert con.execute("SELECT COUNT(*) FROM setlist_entry WHERE song_id = ?", (keep,)).fetchone()[0] == 2
    assert con.execute("SELECT version FROM show WHERE id = ?", (s,)).fetchone()[0] == before + 1


# ---------------------------------------------------------------- search
def test_search_songs_by_title_and_alias(con, lanterns):
    boats = records.resolve_song(con, lanterns, "Paper Boats")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'sailing', ?)", (lanterns, boats))
    assert [r["title"] for r in songs.search_songs(con, lanterns, "LIGHT")] == ["Lantern Light"]
    assert [r["title"] for r in songs.search_songs(con, lanterns, "sail")] == ["Paper Boats"]
    assert [r["title"] for r in songs.search_songs(con, lanterns, "")] == ["Lantern Light", "Paper Boats", "Candlewick", "Night Ferry"]
    assert set(songs.search_songs(con, lanterns, "light")[0]) == {"id", "title", "plays"}


# ---------------------------------------------------------------- stats and artist merge
def test_top_songs_in_stats_attended_only(con, lanterns):
    import stats
    top = stats.stats(con)["top_songs"]
    assert top[0] == {"song_id": records.resolve_song(con, lanterns, "Lantern Light"), "title": "Lantern Light",
                      "artist_id": lanterns, "artist": "The Paper Lanterns", "count": 3}
    assert len(top) == 4 and len(stats.stats(con)["top_songs"]) <= stats.TOP


def test_songs_survive_an_artist_merge(con, lanterns):
    dup = mk_artist(con, "Paper Lanterns")
    songs.set_catalog(con, dup, ["Moth Orchard"])
    merge_artists(con, lanterns, dup)
    assert by_title(songs.artist_songs(con, lanterns))["Moth Orchard"]["in_catalog"]


# ---------------------------------------------------------------- routes
def test_public_artist_songs_and_song_routes(client, con, lanterns):
    sid = records.resolve_song(con, lanterns, "Lantern Light")
    r = client.get(f"/api/artists/{lanterns}/songs")
    assert r.status_code == 200 and r.get_json()[0]["id"] == sid
    d = client.get(f"/api/songs/{sid}").get_json()
    assert d["plays"] == 3 and d["artist"]["name"] == "The Paper Lanterns"
    assert client.get("/api/songs/999").status_code == 404
    assert client.get("/api/artists/999/songs").status_code == 404
    assert client.get("/api/songs/999").get_json() == {"error": "Not found"}


def test_public_404_for_a_song_unknown_to_attended_shows_and_catalog(client, con, venue):
    s = mk_show(con, "Cold Harbor", "2020-01-02", venue)
    play(con, s, "Cold Harbor", ["Low Tide Hymn"])
    art = mk_artist(con, "Cold Harbor")
    sid = records.resolve_song(con, art, "Low Tide Hymn")
    assert client.get(f"/api/songs/{sid}").status_code == 200
    from records import delete_show
    delete_show(con, s)  # the song only that show played goes with it
    assert client.get(f"/api/songs/{sid}").status_code == 404
    # a song left behind with no plays and not in the catalog (an alias keeps it) is still 404 to everyone
    keep = records.get_or_create_song(con, art, "Ghost Train")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'gt', ?)", (art, keep))
    assert client.get(f"/api/songs/{keep}").status_code == 404


def test_public_catalog_song_with_no_plays_is_visible_but_not_an_artist_with_no_attended_show(client, con, lanterns, venue):
    songs.set_catalog(con, lanterns, ["Moth Orchard"])
    moth = records.resolve_song(con, lanterns, "Moth Orchard")
    assert client.get(f"/api/songs/{moth}").get_json()["plays"] == 0
    up = mk_show(con, "Upcoming Only", "2099-01-01", venue, status="upcoming")
    art = mk_artist(con, "Upcoming Only")
    songs.set_catalog(con, art, ["Secret Verse"])
    assert up
    assert client.get(f"/api/artists/{art}/songs").status_code == 404
    assert client.get(f"/api/songs/{records.resolve_song(con, art, 'Secret Verse')}").status_code == 404


def test_public_song_hides_editor_only_fields(client, con, lanterns, venue):
    s = mk_show(con, "Marigold Static", "2020-01-02", venue, notes="SECRET-NOTE", rating=5)
    play(con, s, "Marigold Static", ["Glass Orchard"], source="SECRET-SOURCE")
    sid = records.resolve_song(con, mk_artist(con, "Marigold Static"), "Glass Orchard")
    body = client.get(f"/api/songs/{sid}").get_data(as_text=True)
    assert "SECRET" not in body and '"notes"' not in body and '"rating"' not in body


def test_editor_routes_mirror_and_public_view_off(client, editor, con, lanterns, monkeypatch):
    import settings
    sid = records.resolve_song(con, lanterns, "Lantern Light")
    assert editor.get(f"/admin/api/artists/{lanterns}/songs").status_code == 200
    assert editor.get(f"/admin/api/songs/{sid}").status_code == 200
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    assert client.get(f"/api/songs/{sid}").status_code == 403
    assert client.get(f"/api/artists/{lanterns}/songs").get_json() == {"error": "Sign in to view"}


def test_editor_artist_songs_search(editor, con, lanterns):
    r = editor.get(f"/admin/api/artists/{lanterns}/songs?q=ferry")
    assert [s["title"] for s in r.get_json()] == ["Night Ferry"]
    assert set(r.get_json()[0]) == {"id", "title", "plays"}
    assert editor.get("/admin/api/artists/999/songs?q=x").status_code == 404


def test_catalog_route_adds_then_removes(editor, con, lanterns):
    r = editor.post(f"/admin/api/artists/{lanterns}/catalog", json={"text": "1. Moth Orchard\n2. Lantern Light\n"})
    assert r.status_code == 200 and r.get_json() == {"added": 2, "already": 0}
    again = editor.post(f"/admin/api/artists/{lanterns}/catalog", json={"text": "Moth Orchard"})
    assert again.get_json() == {"added": 0, "already": 1}
    moth = records.resolve_song(con, lanterns, "Moth Orchard")
    assert editor.delete(f"/admin/api/songs/{moth}/catalog").get_json()["deleted"] is True
    assert editor.delete(f"/admin/api/songs/{moth}/catalog").status_code == 404


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": 5}, {"text": "x" * 201}, {"text": "Album: only\n"}])
def test_catalog_route_refuses_bad_text(editor, con, lanterns, body):
    assert editor.post(f"/admin/api/artists/{lanterns}/catalog", json=body).status_code == 400


def test_merge_route(editor, con, lanterns):
    keep = records.resolve_song(con, lanterns, "Lantern Light")
    drop = records.resolve_song(con, lanterns, "Candlewick")
    r = editor.post("/admin/api/songs/merge", json={"keep": keep, "drop": drop})
    assert r.status_code == 200 and r.get_json() == {"keep": keep, "moved": 1}
    for body in ({"keep": keep}, {"keep": keep, "drop": "x"}, {"keep": keep, "drop": keep}, {"keep": True, "drop": 2}):
        assert editor.post("/admin/api/songs/merge", json=body).status_code == 400
    assert editor.post("/admin/api/songs/merge", json={"keep": keep, "drop": 999}).status_code == 404


def test_writes_need_an_editor_with_the_write_key(client, con, lanterns):
    sid = records.resolve_song(con, lanterns, "Lantern Light")
    assert client.post(f"/admin/api/artists/{lanterns}/catalog", json={"text": "x"}).status_code == 401
    assert client.delete(f"/admin/api/songs/{sid}/catalog").status_code == 401
    assert client.post("/admin/api/songs/merge", json={"keep": sid, "drop": sid}).status_code == 401
    import app as stubs
    reader = stubs.app.test_client()
    reader.environ_base.update(HTTP_X_API_KEY=READ_KEY)
    assert reader.post(f"/admin/api/artists/{lanterns}/catalog", json={"text": "x"}).status_code == 403
