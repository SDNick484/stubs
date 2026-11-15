"""The write-side core (spec sections 5 and 7.2): resolve or create artists, venues, runs and songs, and create shows,
lineup entries and sources. Every function takes a sqlite3.Connection and imports no route code, so imports, the
editor and the API share one set of writers. Bad input raises common.ApiError."""
import os
import sqlite3

import settings
from common import ApiError, norm, parse_date, venue_norm
from db import transaction

STATUSES = ("attended", "upcoming")
ROLES = ("headliner", "coheadliner", "opener", "performer")
CONFIDENCES = ("confirmed", "likely")


def _name(value, what):
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if not name:
        raise ApiError(400, f"{what} is required.")
    return name


def _choice(value, allowed, what):
    if value not in allowed:
        raise ApiError(400, f"{what} must be one of: {', '.join(allowed)}.")
    return value


# ---------------------------------------------------------------- artists
def resolve_artist(con, name) -> "int | None":
    """The artist this name means: `artist.norm` first, then `artist_alias.norm`. None when unknown or blank."""
    n = norm(name)
    if not n:
        return None
    row = con.execute("SELECT id FROM artist WHERE norm = ?", (n,)).fetchone()
    if row is None:
        row = con.execute("SELECT artist_id AS id FROM artist_alias WHERE norm = ?", (n,)).fetchone()
    return row["id"] if row else None


def get_or_create_artist(con, name) -> int:
    name = _name(name, "Artist name")
    with transaction(con):
        found = resolve_artist(con, name)
        if found is not None:
            return found
        return con.execute("INSERT INTO artist (name, norm) VALUES (?, ?)", (name, norm(name))).lastrowid


# ---------------------------------------------------------------- songs (schema v3)
def resolve_song(con, artist_id, title) -> "int | None":
    """This act's song by this title: `song.norm` first, then `song_alias.norm`, both within the artist (another
    act's song of the same title is another song). None when unknown or blank."""
    n = norm(title)
    if not n:
        return None
    row = con.execute("SELECT id FROM song WHERE artist_id = ? AND norm = ?", (artist_id, n)).fetchone()
    if row is None:
        row = con.execute("SELECT song_id AS id FROM song_alias WHERE artist_id = ? AND norm = ?",
                          (artist_id, n)).fetchone()
    return row["id"] if row else None


def get_or_create_song(con, artist_id, title) -> int:
    """resolve_song, else a new song with this title (spaces collapsed) for the artist."""
    title = _name(title, "Song title")
    with transaction(con):
        found = resolve_song(con, artist_id, title)
        if found is not None:
            return found
        return con.execute("INSERT INTO song (artist_id, title, norm) VALUES (?, ?, ?)",
                           (artist_id, title, norm(title))).lastrowid


def drop_unused_songs(con, artist_ids) -> None:
    """Delete these artists' songs that no setlist plays, that are not in the artist's catalog and that have no alias:
    a typo fixed in a setlist leaves no song behind. Songs are never cascaded, so the writers that remove setlist
    entries (a setlist saved or deleted, an act taken off a lineup, a show deleted, an artist merge) call this."""
    con.executemany(
        "DELETE FROM song WHERE artist_id = ? AND in_catalog = 0 "
        "AND NOT EXISTS (SELECT 1 FROM setlist_entry e WHERE e.song_id = song.id) "
        "AND NOT EXISTS (SELECT 1 FROM song_alias x WHERE x.song_id = song.id)", [(a,) for a in set(artist_ids)])


def move_setlist_songs(con, lineup_id, old_artist_id, new_artist_id) -> None:
    """A lineup entry now names another act (a lineup edit renamed it): its stored setlist's songs become that act's
    songs, matched by title (made when the act has no such song), and the old act's songs nothing plays any more go.
    Call inside the caller's transaction."""
    rows = con.execute("SELECT e.id, s.title FROM setlist_entry e JOIN song s ON s.id = e.song_id "
                       "WHERE e.lineup_id = ?", (lineup_id,)).fetchall()
    for r in rows:
        con.execute("UPDATE setlist_entry SET song_id = ? WHERE id = ?",
                    (get_or_create_song(con, new_artist_id, r["title"]), r["id"]))
    drop_unused_songs(con, [old_artist_id])


# ---------------------------------------------------------------- venues and runs
def find_venue(con, name, city) -> "int | None":
    """The venue in this city that has ever gone by this name (`venue_norm` against every `venue_name`; the city
    is compared with `norm`). The lowest id wins when two venues would match."""
    n, c = venue_norm(name), norm(city)
    if not n:
        return None
    for row in con.execute("SELECT v.id, v.city FROM venue_name vn JOIN venue v ON v.id = vn.venue_id "
                           "WHERE vn.norm = ? ORDER BY v.id", (n,)):
        if norm(row["city"]) == c:
            return row["id"]
    return None


def get_or_create_venue(con, name, city, region, country) -> int:
    """The matching venue (see find_venue), else a new one with a `venue_name` row for `name`."""
    name, city = _name(name, "Venue name"), _name(city, "City")
    with transaction(con):
        found = find_venue(con, name, city)
        if found is not None:
            return found
        vid = con.execute("INSERT INTO venue (name, city, region, country) VALUES (?, ?, ?, ?)",
                          (name, city, region or None, country or None)).lastrowid
        con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)", (vid, name, venue_norm(name)))
        return vid


def get_or_create_run(con, name) -> int:
    name = _name(name, "Run name")
    with transaction(con):
        for row in con.execute("SELECT id, name FROM run ORDER BY id"):
            if norm(row["name"]) == norm(name):
                return row["id"]
        return con.execute("INSERT INTO run (name) VALUES (?)", (name,)).lastrowid


# ---------------------------------------------------------------- shows
def create_show(con, *, status, date, billing, venue_id, venue_label, run_id=None, tour=None, notes=None,
                confidence="confirmed", confidence_note=None) -> int:
    """Insert a show; `date_precision` and `sort_date` come from `parse_date`. Returns its id."""
    _choice(status, STATUSES, "status")
    _choice(confidence, CONFIDENCES, "confidence")
    try:
        d, precision, sort_date = parse_date(date)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    billing = _name(billing, "billing")
    try:
        return con.execute(
            "INSERT INTO show (status, date, date_precision, sort_date, billing, venue_id, venue_label, run_id, tour, "
            "notes, confidence, confidence_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (status, d, precision, sort_date, billing, venue_id, venue_label, run_id, tour, notes, confidence,
             confidence_note)).lastrowid
    except sqlite3.IntegrityError:
        raise ApiError(400, "Unknown venue or run.") from None


def add_lineup_entry(con, show_id, artist_id, role, position=None, confidence="confirmed",
                     confidence_note=None) -> int:
    """Put an artist on a show's bill. `position` defaults to last + 1. A repeat artist is 409."""
    _choice(role, ROLES, "role")
    _choice(confidence, CONFIDENCES, "confidence")
    with transaction(con):
        if con.execute("SELECT 1 FROM lineup WHERE show_id = ? AND artist_id = ?", (show_id, artist_id)).fetchone():
            raise ApiError(409, "Already on this show.")
        if position is None:
            position = con.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM lineup WHERE show_id = ?",
                                   (show_id,)).fetchone()[0]
        return con.execute(
            "INSERT INTO lineup (show_id, artist_id, role, position, confidence, confidence_note) "
            "VALUES (?, ?, ?, ?, ?, ?)", (show_id, artist_id, role, position, confidence, confidence_note)).lastrowid


def add_source(con, show_id, label, url=None, lineup_id=None) -> int:
    """Add to the evidence trail. `lineup_id` None means the source backs the show itself."""
    label = (label or "").strip() if isinstance(label, str) or label is None else ""
    if not label:
        raise ApiError(400, "A source needs a label.")
    return con.execute("INSERT INTO source (show_id, lineup_id, label, url) VALUES (?, ?, ?, ?)",
                       (show_id, lineup_id, label, url or None)).lastrowid


def delete_show(con, show_id) -> None:
    """Delete a show (its lineup, sources, companions, images, setlist links and stored setlists cascade; songs only
    those setlists played go too) and unlink its image files from IMAGE_DIR. Files go after the rows, and only by
    base name inside IMAGE_DIR; one already gone is fine. Inside a caller's larger transaction the files go before
    that transaction commits."""
    with transaction(con):
        files = [r["file"] for r in con.execute("SELECT file FROM image WHERE show_id = ?", (show_id,))]
        acts = [r["artist_id"] for r in con.execute("SELECT artist_id FROM lineup WHERE show_id = ?", (show_id,))]
        if con.execute("DELETE FROM show WHERE id = ?", (show_id,)).rowcount == 0:
            raise ApiError(404, "Show not found.")
        drop_unused_songs(con, acts)
    for name in files:
        try:
            os.unlink(os.path.join(settings.IMAGE_DIR, os.path.basename(name)))
        except OSError:
            pass
