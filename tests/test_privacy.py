"""Spec section 6: nothing editor-only reaches a public reader. Seeds one of everything editor-only (an upcoming show,
a note, a likely flag, a source, a companion, a rating, a poster, a stub, an import, a stored setlist's source, a
stored setlist on the upcoming show), each carrying a marker string,
then walks every GET rule under /api/ in app.url_map as an anonymous client and asserts no marker, and no editor-only
key, appears in any body.

A new public route with a path parameter not listed in PARAM_SAMPLES fails test_every_public_param_has_a_sample: add
one line here for it (the only edit other tasks make to this file)."""
import itertools
import json
import re
import types

import pytest

import app as stubs
import settings
from conftest import mk_artist, mk_show, mk_venue

MARKERS = ("SECRET-UPCOMING", "SECRET-NOTE", "SECRET-CONF", "SECRET-SOURCE", "SECRET-PERSON", "secret-poster",
           "secret-stub", "SECRET-IMPORT", "SECRET-SETLIST")
UPCOMING_YEAR = "2099"  # the upcoming show's year: no public page may list it, not even as a year with 0 shows
EDITOR_KEYS = {"notes", "rating", "confidence", "confidence_note", "sources", "version", "source_label", "source_url"}

# Path parameter name -> the values to try, from the `seeded` namespace.
PARAM_SAMPLES = {
    "show_id": lambda s: [s.attended, s.upcoming],
    "artist_id": lambda s: [s.artist, s.opener],
    "venue_id": lambda s: [s.venue],
    "song_id": lambda s: [s.song, s.hidden_song],
    "year": lambda s: [2005, 2099],
    "country": lambda s: ["CA"],
    "region": lambda s: ["BC"],
    "city": lambda s: ["Vancouver"],
}
QUERIES = ("", "?year=2005", "?year=2099", "?artist_id={artist}", "?venue_id={venue}")
PARAM = re.compile(r"<(?:[^:<>]+:)?([^<>]+)>")


@pytest.fixture
def seeded(con):
    v = mk_venue(con, "Granite Hall", "Vancouver", "BC", "CA", lat=49.28, lon=-123.12)
    attended = mk_show(con, "Marigold Static", "2005-04-02", v, lineup=(("Marigold Static", "headliner"), ("Ninefold", "opener")),
                       notes="SECRET-NOTE about Alex", rating=4, confidence="likely", confidence_note="SECRET-CONF show",
                       tour="Static Tour")
    upcoming = mk_show(con, "SECRET-UPCOMING night", "2099-09-09", v, lineup=(("Marigold Static", "headliner"),),
                       status="upcoming")
    artist, opener = mk_artist(con, "Marigold Static"), mk_artist(con, "Ninefold")
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ? AND artist_id = ?", (attended, opener)).fetchone()[0]
    con.execute("UPDATE lineup SET rating = 5, confidence = 'likely', confidence_note = 'SECRET-CONF opener' WHERE id = ?", (lid,))
    con.execute("INSERT INTO source (show_id, label, url) VALUES (?, 'SECRET-SOURCE show', "
                "'https://concerts.example.com/SECRET-SOURCE')", (attended,))
    con.execute("INSERT INTO source (show_id, lineup_id, label) VALUES (?, ?, 'SECRET-SOURCE opener')", (attended, lid))
    p = con.execute("INSERT INTO person (name) VALUES ('SECRET-PERSON Sam')").lastrowid
    con.execute("INSERT INTO show_person (show_id, person_id) VALUES (?, ?)", (attended, p))
    con.execute("INSERT INTO image (show_id, kind, file, caption) VALUES (?, 'poster', 'secret-poster-1.jpg', "
                "'secret-poster caption')", (attended,))
    con.execute("INSERT INTO image (show_id, kind, file, caption) VALUES (?, 'stub', 'secret-stub-1.jpg', "
                "'secret-stub caption')", (attended,))
    con.execute("INSERT INTO image (show_id, kind, file, position) VALUES (?, 'photo', 'photo-1.jpg', 1)", (attended,))
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'photo', 'secret-poster-upcoming.jpg')", (upcoming,))
    # The owner's setlists: the attended show's opener has one (public, but never its source), and so does the
    # upcoming show (never public at all).
    song = con.execute("INSERT INTO song (artist_id, title, norm) VALUES (?, 'Ninefold Waltz', 'ninefold waltz')",
                       (opener,)).lastrowid
    con.execute("INSERT INTO setlist (lineup_id, source_label, source_url) VALUES (?, 'SECRET-SETLIST fan club', "
                "'https://concerts.example.com/SECRET-SETLIST')", (lid,))
    con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id) VALUES (?, 1, ?)", (lid, song))
    up_lid = con.execute("SELECT id FROM lineup WHERE show_id = ?", (upcoming,)).fetchone()[0]
    hidden = con.execute("INSERT INTO song (artist_id, title, norm) VALUES (?, 'SECRET-UPCOMING song', "
                         "'secret-upcoming song')", (artist,)).lastrowid
    con.execute("INSERT INTO setlist (lineup_id, source_label) VALUES (?, 'typed')", (up_lid,))
    con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id, note) VALUES (?, 1, ?, 'SECRET-UPCOMING')",
                (up_lid, hidden))
    b = con.execute("INSERT INTO import_batch (origin, label) VALUES ('csv', 'SECRET-IMPORT batch')").lastrowid
    con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict, include) "
                "VALUES (?, 1, '{\"artist\": \"SECRET-IMPORT\"}', '{\"billing\": \"SECRET-IMPORT\"}', 'new', 1)", (b,))
    return types.SimpleNamespace(venue=v, attended=attended, upcoming=upcoming, artist=artist, opener=opener,
                                 song=song, hidden_song=hidden)


def public_rules():
    return sorted((r for r in stubs.app.url_map.iter_rules() if r.rule.startswith("/api/") and "GET" in r.methods),
                  key=lambda r: r.rule)


def urls(rule, seeded):
    """Every URL for this rule: each combination of its path parameters' samples, with and without each query."""
    names = PARAM.findall(rule.rule)
    combos = itertools.product(*(PARAM_SAMPLES[n](seeded) for n in names)) if names else [()]
    for combo in combos:
        values = iter(combo)
        path = PARAM.sub(lambda m: str(next(values)), rule.rule)
        for q in QUERIES:
            yield path + q.format(artist=seeded.artist, venue=seeded.venue)


def editor_keys_in(data):
    """Editor-only keys anywhere in a JSON value."""
    if isinstance(data, dict):
        return (EDITOR_KEYS & set(data)) | set().union(*(editor_keys_in(v) for v in data.values()))
    if isinstance(data, list):
        return set().union(*(editor_keys_in(v) for v in data))
    return set()


def test_every_public_param_has_a_sample():
    missing = {n for r in public_rules() for n in PARAM.findall(r.rule)} - set(PARAM_SAMPLES)
    assert not missing, f"add these to PARAM_SAMPLES in tests/test_privacy.py: {sorted(missing)}"


def test_walker_found_routes(client, seeded):
    rules = [r.rule for r in public_rules()]
    assert "/api/session" in rules
    assert all(not r.startswith("/admin/") for r in rules)
    assert list(urls(public_rules()[0], seeded))


def test_no_editor_data_on_public_routes(client, seeded):
    assert not settings.PUBLIC_POSTERS
    walked = 0
    for rule in public_rules():
        for url in urls(rule, seeded):
            r = client.get(url)
            body = r.get_data(as_text=True)
            low = body.lower()
            for marker in MARKERS:
                assert marker.lower() not in low, f"{url} ({r.status_code}) shows {marker}"
            if UPCOMING_YEAR not in url:  # a page asked about 2099 may echo the year it was asked about
                assert UPCOMING_YEAR not in body, f"{url} ({r.status_code}) shows the upcoming show's year"
            if r.mimetype == "application/json":
                leaked = editor_keys_in(json.loads(body))
                assert not leaked, f"{url} has editor-only keys {sorted(leaked)}"
            walked += 1
    assert walked >= len(QUERIES)


def test_the_public_show_has_the_setlist_without_its_source(client, seeded):
    lineup = {e["name"]: e for e in client.get(f"/api/shows/{seeded.attended}").get_json()["lineup"]}
    sets = lineup["Ninefold"]["setlist"]["sets"]
    assert [s["title"] for s in sets[0]["songs"]] == ["Ninefold Waltz"]
    assert set(lineup["Ninefold"]["setlist"]) == {"sets"}


def test_every_marker_is_seeded(con, seeded):
    """Guards the guard: each marker is in the database, so a clean public walk means it was hidden."""
    dump = "\n".join(con.iterdump()).lower()
    for marker in MARKERS + (UPCOMING_YEAR,):
        assert marker.lower() in dump, marker


def test_public_view_off_hides_every_api_route(client, seeded, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    for rule in public_rules():
        if rule.rule == "/api/session":
            continue
        for url in urls(rule, seeded):
            r = client.get(url)
            assert r.status_code == 403 and r.get_json() == {"error": "Sign in to view"}, url
