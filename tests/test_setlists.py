"""Post-v1 #43: stored setlists. The paste parser (setlists.parse_setlist), the editor's preview, save and delete under
/admin/api/lineup/<id>/setlist, songs per act (records.resolve_song / get_or_create_song), the setlist on the show
detail (public without its source), and what lineup edits, artist merges and show deletes do to setlists and songs.
Fixture pastes live in tests/fixtures/setlists/; every act and song is invented."""
import io
import os

import pytest

import records
import setlistfm
import settings
from common import ApiError
from conftest import mk_artist, mk_show, mk_venue
from merge import merge_artists
from setlists import parse_setlist

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "setlists")
STALE = "Someone else just changed this show. Reload to see it."


def paste(name):
    with open(os.path.join(FIX, name), encoding="utf-8", newline="") as f:
        return f.read()


def flat(entries):
    return [(e["title"], e["set_name"], e["encore"], e["note"], e["segue"]) for e in entries]


# ---------------------------------------------------------------- the parser
def test_parser_strips_numbering_and_bullets():
    assert [e["title"] for e in parse_setlist(paste("numbered.txt"))] == [
        "Lantern Light", "Paper Boats", "Low Tide Hymn", "Ferris Wheel Static", "Night Ferry", "Candlewick",
        "Saltwater Choir", "Moth Orchard",
        "99 Lanterns",  # a number that is not the next song's number is part of the title
    ]


def test_parser_headings_start_sets_and_encores():
    assert [(e["title"], e["set_name"], e["encore"]) for e in parse_setlist(paste("encores.txt"))] == [
        ("Lantern Light", None, False),
        ("Paper Boats", None, False),
        ("Low Tide Hymn", "Set II", False),
        ("Ferris Wheel Static", "Acoustic Set", False),
        ("Night Ferry", "Under the Pier", False),
        ("Candlewick", None, True),
        ("Saltwater Choir", "Encore 2", True),
        ("Moth Orchard", "Set 3", False),
        ("Paper Crown", "Encore 3", True),  # a third encore, named so it stays its own set
        ("Last Tram Home", "Encore 4", True),
    ]


def test_parser_segues():
    assert [(e["title"], e["segue"]) for e in parse_setlist(paste("segues.txt"))] == [
        ("Lantern Light", True), ("Paper Boats", False),
        ("Low Tide Hymn", True), ("Ferris Wheel Static", True), ("Night Ferry", False),
        ("Candlewick", True), ("Saltwater Choir", False),
        ("Moth Orchard", True), ("Paper Crown", False),
    ]


def test_parser_notes_and_quotes():
    assert [(e["title"], e["note"]) for e in parse_setlist(paste("notes.txt"))] == [
        ("Paper Boats", "with a guest"),
        ("Low Tide Hymn", "tease"),
        ("Night Ferry", None),
        ("Candlewick", "first time"),
        ("Ruth’s Song", "cover; extended"),
        ("(Untitled)", None),  # nothing left without the brackets: they are the title
        ("Moth (Orchard) Waltz", None),  # brackets inside a title stay
        ("Saltwater Choir", None),
    ]


def test_parser_crlf():
    assert flat(parse_setlist(paste("crlf.txt"))) == [
        ("Lantern Light", "Set 1", False, None, False),
        ("Paper Boats", "Set 1", False, None, True),
        ("Night Ferry", None, True, None, False),
    ]


def test_parser_note_and_segue_on_one_line():
    assert flat(parse_setlist("3. Paper Boats (tease) > Night Ferry [with a guest] >\nCandlewick")) == [
        ("Paper Boats", None, False, "tease", True),
        ("Night Ferry", None, False, "with a guest", True),
        ("Candlewick", None, False, None, False),
    ]


def test_parser_ignores_blank_lines_and_nothing_is_empty():
    assert parse_setlist("\n\n   \n") == []
    assert parse_setlist("Encore:\n") == []
    assert [e["title"] for e in parse_setlist("  Lantern  Light  \n\n\nPaper Boats")] == ["Lantern Light", "Paper Boats"]


def test_parser_refuses_a_long_line_with_its_number():
    with pytest.raises(ApiError) as e:
        parse_setlist("Lantern Light\n\n" + "x" * 201)
    assert e.value.status == 400 and "Line 3" in e.value.message
    assert parse_setlist("x" * 200)[0]["title"] == "x" * 200


def test_parser_refuses_more_than_300_songs():
    assert len(parse_setlist("\n".join(f"Song {i}" for i in range(300)))) == 300
    with pytest.raises(ApiError) as e:
        parse_setlist("Encore:\n" + "\n".join(f"Song {i}" for i in range(301)))
    assert e.value.status == 400 and "Line 302" in e.value.message and "300" in e.value.message


# ---------------------------------------------------------------- fixtures for the routes
@pytest.fixture
def show(con):
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "The Paper Lanterns", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))


def lineup_id(con, show_id, name):
    return con.execute("SELECT l.id FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? AND a.name = ?",
                       (show_id, name)).fetchone()[0]


def version(con, show_id):
    return con.execute("SELECT version FROM show WHERE id = ?", (show_id,)).fetchone()[0]


def url(lid, tail=""):
    return f"/admin/api/lineup/{lid}/setlist{tail}"


def preview(editor, lid, text):
    r = editor.post(url(lid, "/preview"), json={"text": text})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["entries"]


def save(editor, con, show_id, lid, text, label="typed", source_url=None, ver=None):
    entries = preview(editor, lid, text)
    body = {"entries": entries, "source_label": label, "source_url": source_url,
            "version": version(con, show_id) if ver is None else ver}
    return editor.put(url(lid), json=body)


def counts(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("song", "song_alias", "setlist", "setlist_entry")}


def songs(con, artist):
    return [r[0] for r in con.execute("SELECT s.title FROM song s JOIN artist a ON a.id = s.artist_id "
                                      "WHERE a.name = ? ORDER BY s.id", (artist,))]


# ---------------------------------------------------------------- preview
def test_preview_parses_and_stores_nothing(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    before, ver = counts(con), version(con, show)
    entries = preview(editor, lid, paste("crlf.txt"))
    assert entries == [
        {"title": "Lantern Light", "set_name": "Set 1", "encore": False, "note": None, "segue": False, "song_id": None},
        {"title": "Paper Boats", "set_name": "Set 1", "encore": False, "note": None, "segue": True, "song_id": None},
        {"title": "Night Ferry", "set_name": None, "encore": True, "note": None, "segue": False, "song_id": None},
    ]
    assert counts(con) == before and version(con, show) == ver


def test_preview_finds_the_acts_existing_songs_by_name_and_alias(editor, con, show):
    lanterns = mk_artist(con, "The Paper Lanterns")
    light = records.get_or_create_song(con, lanterns, "Lantern Light")
    boats = records.get_or_create_song(con, lanterns, "Paper Boats")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'paper boat', ?)", (lanterns, boats))
    records.get_or_create_song(con, mk_artist(con, "Marigold Static"), "Night Ferry")  # another act's song
    entries = preview(editor, lineup_id(con, show, "The Paper Lanterns"), "LANTERN LIGHT\nPaper Boat\nNight Ferry")
    assert [e["song_id"] for e in entries] == [light, boats, None]


def test_preview_errors(client, editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    assert editor.post(url(99999, "/preview"), json={"text": "Lantern Light"}).status_code == 404
    r = editor.post(url(lid, "/preview"), json={"text": "x" * 300})
    assert r.status_code == 400 and "Line 1" in r.get_json()["error"]
    assert editor.post(url(lid, "/preview"), json={"text": ""}).status_code == 400
    assert editor.post(url(lid, "/preview"), json={"text": 5}).status_code == 400
    assert client.post(url(lid, "/preview"), json={"text": "Lantern Light"}).status_code == 401


# ---------------------------------------------------------------- save
def test_save_stores_the_setlist_and_bumps_the_version(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    ver = version(con, show)
    r = save(editor, con, show, lid, paste("crlf.txt"), label="Lantern fan club", source_url="https://example.com/sl")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["version"] == ver + 1 == version(con, show)
    ids = {t: i for i, t in con.execute("SELECT id, title FROM song")}
    assert body["setlist"] == {
        "source_label": "Lantern fan club", "source_url": "https://example.com/sl",
        "sets": [
            {"name": "Set 1", "encore": False, "songs": [
                {"song_id": ids["Lantern Light"], "title": "Lantern Light", "note": None, "segue": False},
                {"song_id": ids["Paper Boats"], "title": "Paper Boats", "note": None, "segue": True}]},
            {"name": None, "encore": True, "songs": [
                {"song_id": ids["Night Ferry"], "title": "Night Ferry", "note": None, "segue": False}]},
        ]}
    assert [tuple(r) for r in con.execute("SELECT position, set_name, encore, segue FROM setlist_entry "
                                          "WHERE lineup_id = ? ORDER BY position", (lid,))] == [
        (1, "Set 1", 0, 0), (2, "Set 1", 0, 1), (3, None, 1, 0)]


def test_save_creates_each_song_once_per_act(editor, con, show):
    lanterns, marigold = lineup_id(con, show, "The Paper Lanterns"), lineup_id(con, show, "Marigold Static")
    assert save(editor, con, show, lanterns, "Ruth’s Song\nLantern Light\nRuth's Song (reprise)").status_code == 200
    assert save(editor, con, show, marigold, "Ruth's Song").status_code == 200
    assert songs(con, "The Paper Lanterns") == ["Ruth’s Song", "Lantern Light"]  # curly and straight are one song
    assert songs(con, "Marigold Static") == ["Ruth's Song"]  # another act's song with the same title is another song
    played = [r[0] for r in con.execute("SELECT song_id FROM setlist_entry WHERE lineup_id = ? ORDER BY position",
                                        (lanterns,))]
    assert played[0] == played[2] != played[1]
    # Saving again finds the songs it made.
    assert save(editor, con, show, lanterns, "ruth's song\nLANTERN LIGHT").status_code == 200
    assert counts(con)["song"] == 3


def test_save_replaces_and_drops_songs_nothing_plays(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    assert save(editor, con, show, lid, "Lantern Lihgt\nPaper Boats").status_code == 200
    keeper = records.get_or_create_song(con, mk_artist(con, "The Paper Lanterns"), "Moth Orchard")
    con.execute("UPDATE song SET in_catalog = 1 WHERE id = ?", (keeper,))
    r = save(editor, con, show, lid, "Lantern Light\nPaper Boats", label="my notes")
    assert r.status_code == 200
    # The typo is gone (nothing plays it); a catalog song stays.
    assert songs(con, "The Paper Lanterns") == ["Paper Boats", "Moth Orchard", "Lantern Light"]
    assert con.execute("SELECT source_label FROM setlist WHERE lineup_id = ?", (lid,)).fetchone()[0] == "my notes"
    assert counts(con)["setlist_entry"] == 2


def test_save_uses_a_given_song_id_of_the_same_act(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    mine = records.get_or_create_song(con, mk_artist(con, "The Paper Lanterns"), "Lantern Light")
    theirs = records.get_or_create_song(con, mk_artist(con, "Marigold Static"), "Static Hymn")
    body = {"entries": [{"title": "Lantern Light (live)", "song_id": mine}], "source_label": "typed",
            "version": version(con, show)}
    assert editor.put(url(lid), json=body).status_code == 200
    assert con.execute("SELECT song_id FROM setlist_entry").fetchone()[0] == mine
    body = {"entries": [{"title": "Static Hymn", "song_id": theirs}], "source_label": "typed",
            "version": version(con, show)}
    r = editor.put(url(lid), json=body)
    assert r.status_code == 400 and "song" in r.get_json()["error"]


@pytest.mark.parametrize("change, status", [
    ({"source_label": ""}, 400),
    ({"source_label": None}, 400),
    ({"source_label": "x" * 201}, 400),
    ({"source_url": "ftp://example.com/x"}, 400),
    ({"entries": []}, 400),
    ({"entries": "Lantern Light"}, 400),
    ({"entries": [{"title": ""}]}, 400),
    ({"entries": [{"title": "x" * 201}]}, 400),
    ({"entries": [{"title": "Lantern Light", "note": "x" * 201}]}, 400),
    ({"entries": [{"title": "Lantern Light", "encore": "yes"}]}, 400),
    ({"entries": [{"title": "Lantern Light", "colour": "red"}]}, 400),
    ({"entries": [{"title": f"Song {i}"} for i in range(301)]}, 400),
    ({"setlist": "x"}, 400),
    ({"version": None}, 400),
])
def test_save_refuses_bad_input(editor, con, show, change, status):
    lid = lineup_id(con, show, "The Paper Lanterns")
    body = {"entries": [{"title": "Lantern Light"}], "source_label": "typed", "version": version(con, show), **change}
    before, ver = counts(con), version(con, show)
    r = editor.put(url(lid), json=body)
    assert r.status_code == status, r.get_json()
    assert counts(con) == before and version(con, show) == ver


def test_save_with_a_stale_version_is_409_and_writes_nothing(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    old = version(con, show)
    assert save(editor, con, show, lid, "Lantern Light").status_code == 200
    before = counts(con)
    r = save(editor, con, show, lid, "Paper Boats", ver=old)
    assert r.status_code == 409 and r.get_json() == {"error": STALE}
    assert counts(con) == before
    r = editor.delete(url(lid), json={"version": old})
    assert r.status_code == 409 and counts(con) == before


def test_upcoming_show_is_409(editor, con):
    v = mk_venue(con, "Granite Hall")
    later = mk_show(con, "Cold Harbor", "2099-01-02", v, status="upcoming")
    lid = lineup_id(con, later, "Cold Harbor")
    body = {"entries": [{"title": "Harbor Lights"}], "source_label": "typed", "version": version(con, later)}
    r = editor.put(url(lid), json=body)
    assert r.status_code == 409 and r.get_json() == {"error": "Add a setlist after the show."}
    r = editor.post(url(lid, "/preview"), json={"text": "Harbor Lights"})
    assert r.status_code == 409 and r.get_json() == {"error": "Add a setlist after the show."}
    assert counts(con)["song"] == 0


def test_unknown_lineup_is_404(editor):
    body = {"entries": [{"title": "Harbor Lights"}], "source_label": "typed", "version": 1}
    assert editor.put(url(99999), json=body).status_code == 404
    assert editor.delete(url(99999), json={"version": 1}).status_code == 404


def test_writes_need_an_editor_with_the_write_key(client, con, show):
    import app as stubs
    from conftest import READ_KEY
    lid = lineup_id(con, show, "The Paper Lanterns")
    body = {"entries": [{"title": "Lantern Light"}], "source_label": "typed", "version": version(con, show)}
    assert client.put(url(lid), json=body).status_code == 401
    assert client.delete(url(lid), json={"version": 1}).status_code == 401
    reader = stubs.app.test_client()
    reader.environ_base.update(HTTP_X_API_KEY=READ_KEY)
    assert reader.put(url(lid), json=body).status_code == 403
    assert reader.post(url(lid, "/preview"), json={"text": "x"}).status_code == 403


# ---------------------------------------------------------------- delete
def test_delete_removes_the_setlist_and_bumps_the_version(editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    assert save(editor, con, show, lid, "Lantern Light\nPaper Boats").status_code == 200
    ver = version(con, show)
    r = editor.delete(url(lid), json={"version": ver})
    assert r.status_code == 200 and r.get_json() == {"version": ver + 1}
    assert counts(con) == {"song": 0, "song_alias": 0, "setlist": 0, "setlist_entry": 0}
    r = editor.delete(url(lid), json={"version": ver + 1})
    assert r.status_code == 404  # nothing left to delete


# ---------------------------------------------------------------- the show detail
def test_show_detail_has_each_acts_setlist_and_editors_get_the_source(client, editor, con, show):
    lanterns = lineup_id(con, show, "The Paper Lanterns")
    assert save(editor, con, show, lanterns, "Lantern Light > Paper Boats\nEncore:\nNight Ferry (with a guest)",
                label="Lantern fan club", source_url="https://example.com/sl").status_code == 200
    public = client.get(f"/api/shows/{show}").get_json()
    by_name = {e["name"]: e for e in public["lineup"]}
    assert by_name["Marigold Static"]["setlist"] is None
    sl = by_name["The Paper Lanterns"]["setlist"]
    assert set(sl) == {"sets"}  # no source for the public
    assert [(s["name"], s["encore"], [(x["title"], x["note"], x["segue"]) for x in s["songs"]]) for s in sl["sets"]] == [
        (None, False, [("Lantern Light", None, True), ("Paper Boats", None, False)]),
        (None, True, [("Night Ferry", "with a guest", False)]),
    ]
    body = client.get(f"/api/shows/{show}").get_data(as_text=True)
    assert "Lantern fan club" not in body and "example.com/sl" not in body
    mine = {e["name"]: e for e in editor.get(f"/admin/api/shows/{show}").get_json()["lineup"]}
    assert mine["The Paper Lanterns"]["setlist"]["source_label"] == "Lantern fan club"
    assert mine["The Paper Lanterns"]["setlist"]["source_url"] == "https://example.com/sl"
    assert mine["The Paper Lanterns"]["setlist"]["sets"] == sl["sets"]


def test_an_upcoming_shows_setlist_never_reaches_the_public(client, editor, con, show):
    lid = lineup_id(con, show, "The Paper Lanterns")
    assert save(editor, con, show, lid, "Lantern Light").status_code == 200
    con.execute("UPDATE show SET status = 'upcoming' WHERE id = ?", (show,))
    assert client.get(f"/api/shows/{show}").status_code == 404
    assert editor.get(f"/admin/api/shows/{show}").get_json()["lineup"][1]["setlist"]["sets"]


# ---------------------------------------------------------------- what other writes do to setlists
def test_deleting_a_show_or_a_lineup_entry_removes_its_setlist(editor, con, show):
    lanterns, marigold = lineup_id(con, show, "The Paper Lanterns"), lineup_id(con, show, "Marigold Static")
    assert save(editor, con, show, lanterns, "Lantern Light").status_code == 200
    assert save(editor, con, show, marigold, "Static Hymn").status_code == 200
    detail = editor.get(f"/admin/api/shows/{show}").get_json()
    keep = [{"id": e["id"], "name": e["name"], "role": e["role"]} for e in detail["lineup"] if e["id"] == lanterns]
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": detail["version"], "lineup": keep})
    assert r.status_code == 200
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist")] == [lanterns]
    assert songs(con, "Marigold Static") == []  # nothing plays it any more
    assert editor.delete(f"/admin/api/shows/{show}").status_code == 200
    assert counts(con) == {"song": 0, "song_alias": 0, "setlist": 0, "setlist_entry": 0}


def test_a_lineup_entry_renamed_to_another_act_takes_its_songs_along(editor, con, show):
    marigold = lineup_id(con, show, "Marigold Static")
    assert save(editor, con, show, marigold, "Static Hymn\nPaper Boats").status_code == 200
    detail = editor.get(f"/admin/api/shows/{show}").get_json()
    lineup = [{"id": e["id"], "name": "Ninefold" if e["id"] == marigold else e["name"], "role": e["role"]}
              for e in detail["lineup"]]
    assert editor.put(f"/admin/api/shows/{show}/lineup", json={"version": detail["version"], "lineup": lineup}).status_code == 200
    assert songs(con, "Ninefold") == ["Static Hymn", "Paper Boats"]
    assert songs(con, "Marigold Static") == []
    after = {e["name"]: e for e in editor.get(f"/admin/api/shows/{show}").get_json()["lineup"]}
    assert [x["title"] for x in after["Ninefold"]["setlist"]["sets"][0]["songs"]] == ["Static Hymn", "Paper Boats"]


def test_artist_merge_folds_songs_and_keeps_setlists(editor, con):
    v = mk_venue(con, "Summit Pavilion", "Concord")
    one = mk_show(con, "Cold Harbor", "2009-06-20", v)
    two = mk_show(con, "Cold Harbour", "2010-01-02", v)
    both = mk_show(con, "Cold Harbor", "2011-03-04", v, lineup=(("Cold Harbor", "headliner"), ("Cold Harbour", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    assert save(editor, con, one, lineup_id(con, one, "Cold Harbor"), "Harbor Lights\nTide Clock").status_code == 200
    assert save(editor, con, two, lineup_id(con, two, "Cold Harbour"), "Harbour Lights\nharbor lights\nSalt Road").status_code == 200
    harbour = records.resolve_song(con, drop, "Harbour Lights")
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'harbour light', ?)", (drop, harbour))
    dropped_entry = lineup_id(con, both, "Cold Harbour")
    assert save(editor, con, both, dropped_entry, "Salt Road").status_code == 200

    merge_artists(con, keep, drop)

    assert songs(con, "Cold Harbor") == ["Harbor Lights", "Tide Clock", "Harbour Lights", "Salt Road"]
    assert con.execute("SELECT COUNT(*) FROM song WHERE artist_id = ?", (drop,)).fetchone()[0] == 0
    assert records.resolve_song(con, keep, "Harbour Light") == records.resolve_song(con, keep, "Harbour Lights")
    # The second show's setlist now plays the kept act's songs: "harbor lights" became the kept act's Harbor Lights.
    two_songs = [r[0] for r in con.execute(
        "SELECT s.title FROM setlist_entry se JOIN song s ON s.id = se.song_id WHERE se.lineup_id = ? "
        "ORDER BY se.position", (lineup_id(con, two, "Cold Harbor"),))]
    assert two_songs == ["Harbour Lights", "Harbor Lights", "Salt Road"]
    # The folded show had a setlist only on the dropped entry: it moves to the kept one.
    kept_entry = lineup_id(con, both, "Cold Harbor")
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist WHERE lineup_id IN (?, ?)",
                                      (kept_entry, dropped_entry))] == [kept_entry]
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_artist_merge_on_one_show_keeps_the_kept_entrys_setlist(editor, con):
    v = mk_venue(con, "Summit Pavilion", "Concord")
    s = mk_show(con, "Cold Harbor", "2011-03-04", v, lineup=(("Cold Harbor", "headliner"), ("Cold Harbour", "opener")))
    keep, drop = mk_artist(con, "Cold Harbor"), mk_artist(con, "Cold Harbour")
    kept = lineup_id(con, s, "Cold Harbor")
    assert save(editor, con, s, kept, "Harbor Lights").status_code == 200
    assert save(editor, con, s, lineup_id(con, s, "Cold Harbour"), "Tide Clock").status_code == 200
    merge_artists(con, keep, drop)
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist")] == [kept]
    assert songs(con, "Cold Harbor") == ["Harbor Lights"]  # nothing plays Tide Clock any more


# ---------------------------------------------------------------- setlist.fm stays unstored
class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_setlistfm_songs_are_still_never_stored_next_to_the_owners(editor, client, con, show, monkeypatch):
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "setlistfm", "setlist.json")
    with open(fix, "rb") as f:
        data = f.read()
    monkeypatch.setattr(setlistfm, "urlopen", lambda req, timeout=None: Resp(data))
    monkeypatch.setattr(settings, "SETLISTFM_API_KEY", "test-setlist-key")
    setlistfm._cache.clear()
    lid = lineup_id(con, show, "The Paper Lanterns")
    assert save(editor, con, show, lid, "Paper Crown\nLast Tram Home").status_code == 200
    assert editor.put(f"/admin/api/shows/{show}/lineup/{lid}/setlistfm",
                      json={"id": "63ab1c4e", "url": "https://www.setlist.fm/setlist/x/2025/y-63ab1c4e.html"}
                      ).status_code == 200
    panel = client.get(f"/api/shows/{show}/setlist").get_json()
    theirs = [s for act in panel["acts"] for st in act["sets"] for s in st["songs"]]
    assert theirs and not {"Paper Crown", "Last Tram Home"} & set(theirs)
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for row in con.execute(f'SELECT * FROM "{table}"').fetchall():
            for value in tuple(row):
                if isinstance(value, str):
                    assert not any(song in value for song in theirs), f"{table} holds {value!r}"
    setlistfm._cache.clear()
