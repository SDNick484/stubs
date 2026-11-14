"""The write-side core (spec sections 5 and 7.2): resolve or create artists, venues and runs, and create shows,
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
    """Delete a show (its lineup, sources, companions, images and setlist link cascade) and unlink its image
    files from IMAGE_DIR. Files go after the rows, and only by base name inside IMAGE_DIR; one already gone is
    fine. Inside a caller's larger transaction the files go before that transaction commits."""
    with transaction(con):
        files = [r["file"] for r in con.execute("SELECT file FROM image WHERE show_id = ?", (show_id,))]
        if con.execute("DELETE FROM show WHERE id = ?", (show_id,)).rowcount == 0:
            raise ApiError(404, "Show not found.")
    for name in files:
        try:
            os.unlink(os.path.join(settings.IMAGE_DIR, os.path.basename(name)))
        except OSError:
            pass
