"""Merging duplicate artists and venues (spec 7.8), and the name searches the merge screen and the show editor's venue
picker use. Route-free: every function takes a sqlite3.Connection.

A merge moves every reference from the dropped record to the kept one, records the dropped name as an alias (artists)
or a former name (venues), and deletes the dropped record, all in one transaction. Shows whose lineup or venue changed
get a new `version`, so an edit form opened before the merge is refused as stale instead of saving ids that are
gone."""
from common import ApiError, norm, venue_norm
from db import transaction

LIMIT = 20


def _ids(keep_id, drop_id, kind):
    if keep_id == drop_id:
        raise ApiError(400, f"Pick two different {kind}s.")


def _get(con, table, row_id, kind):
    row = con.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise ApiError(404, f"{kind.capitalize()} not found.")
    return row


# ---------------------------------------------------------------- artists
def merge_artists(con, keep_id, drop_id) -> None:
    """Fold artist drop_id into keep_id. Lineup entries move; where both are on one show the kept entry stays as it
    is (role, position, confidence; a missing rating is filled from the dropped entry), the dropped entry's sources
    move to it, and the dropped entry goes. Aliases move, the dropped name becomes an alias, the artist is deleted."""
    _ids(keep_id, drop_id, "artist")
    with transaction(con):
        keep = _get(con, "artist", keep_id, "artist")
        drop = _get(con, "artist", drop_id, "artist")
        con.execute("UPDATE show SET version = version + 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id IN (SELECT show_id FROM lineup WHERE artist_id = ?)", (drop_id,))
        both = con.execute(
            "SELECT d.id AS drop_entry, k.id AS keep_entry, d.rating FROM lineup d "
            "JOIN lineup k ON k.show_id = d.show_id AND k.artist_id = ? WHERE d.artist_id = ?",
            (keep_id, drop_id)).fetchall()
        for row in both:
            con.execute("UPDATE source SET lineup_id = ? WHERE lineup_id = ?", (row["keep_entry"], row["drop_entry"]))
            con.execute("UPDATE lineup SET rating = COALESCE(rating, ?) WHERE id = ?", (row["rating"], row["keep_entry"]))
            con.execute("DELETE FROM lineup WHERE id = ?", (row["drop_entry"],))
        con.execute("UPDATE lineup SET artist_id = ? WHERE artist_id = ?", (keep_id, drop_id))
        con.execute("UPDATE artist_alias SET artist_id = ? WHERE artist_id = ?", (keep_id, drop_id))
        con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES (?, ?) "
                    "ON CONFLICT(norm) DO UPDATE SET artist_id = excluded.artist_id", (drop["norm"], keep_id))
        con.execute("DELETE FROM artist_alias WHERE norm = ?", (keep["norm"],))
        con.execute("DELETE FROM artist WHERE id = ?", (drop_id,))
        con.execute("UPDATE artist SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (keep_id,))


def _artist_row(r):
    return {"id": r["id"], "name": r["name"], "shows": r["shows"]}


_ARTISTS = ("SELECT a.id, a.name, (SELECT COUNT(*) FROM lineup l WHERE l.artist_id = a.id) AS shows FROM artist a ")


def search_artists(con, q) -> list[dict]:
    """Up to 20 artists whose name or an alias contains q (both by norm): those starting with it first, then by name.
    [{"id", "name", "shows"}], shows counting every show the artist is on (any status, any role)."""
    n = norm(q)
    if not n:
        rows = con.execute(_ARTISTS + "ORDER BY a.name COLLATE NOCASE, a.id LIMIT ?", (LIMIT,))
    else:
        rows = con.execute(
            _ARTISTS + "WHERE instr(a.norm, :q) > 0 "
            "OR EXISTS (SELECT 1 FROM artist_alias x WHERE x.artist_id = a.id AND instr(x.norm, :q) > 0) "
            "ORDER BY instr(a.norm, :q) <> 1, a.name COLLATE NOCASE, a.id LIMIT :n", {"q": n, "n": LIMIT})
    return [_artist_row(r) for r in rows]


def artist_summary(con, artist_id) -> dict:
    return _artist_row(con.execute(_ARTISTS + "WHERE a.id = ?", (artist_id,)).fetchone())


# ---------------------------------------------------------------- venues
def merge_venues(con, keep_id, drop_id) -> None:
    """Fold venue drop_id into keep_id. Shows move and keep their venue_label (the name on that night's ticket); every
    venue_name moves (one the kept venue already has by venue_norm is not repeated), so the dropped names stay
    findable as former names. The kept venue's coordinates win, else the dropped one's (with its geocode_source);
    a missing region or country is filled the same way. The dropped venue is deleted."""
    _ids(keep_id, drop_id, "venue")
    with transaction(con):
        _get(con, "venue", keep_id, "venue")
        drop = _get(con, "venue", drop_id, "venue")
        con.execute("UPDATE show SET venue_id = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE venue_id = ?", (keep_id, drop_id))
        con.execute("INSERT OR IGNORE INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)",
                    (keep_id, drop["name"], venue_norm(drop["name"])))
        con.execute("INSERT OR IGNORE INTO venue_name (venue_id, name, norm) "
                    "SELECT ?, name, norm FROM venue_name WHERE venue_id = ?", (keep_id, drop_id))
        con.execute("DELETE FROM venue_name WHERE venue_id = ?", (drop_id,))
        con.execute(
            "UPDATE venue SET "
            "  geocode_source = CASE WHEN lat IS NULL OR lon IS NULL THEN :gs ELSE geocode_source END, "
            "  lat = CASE WHEN lat IS NULL OR lon IS NULL THEN :lat ELSE lat END, "
            "  lon = CASE WHEN lat IS NULL OR lon IS NULL THEN :lon ELSE lon END, "
            "  region = COALESCE(region, :region), country = COALESCE(country, :country), "
            "  updated_at = CURRENT_TIMESTAMP WHERE id = :id",
            {"gs": drop["geocode_source"], "lat": drop["lat"], "lon": drop["lon"], "region": drop["region"],
             "country": drop["country"], "id": keep_id})
        con.execute("DELETE FROM venue WHERE id = ?", (drop_id,))


def _venue_row(r):
    return {"id": r["id"], "name": r["name"], "city": r["city"], "region": r["region"], "country": r["country"],
            "shows": r["shows"]}


_VENUES = ("SELECT v.id, v.name, v.city, v.region, v.country, "
           "(SELECT COUNT(*) FROM show s WHERE s.venue_id = v.id) AS shows FROM venue v ")


def search_venues(con, q) -> list[dict]:
    """Up to 20 venues with a name, current or former, that contains q (by venue_norm, so a leading "The" doesn't
    matter): those whose current name starts with it first, then by name. [{"id", "name", "city", "region",
    "country", "shows"}], shows counting every show there (any status). Task 11's venue picker uses this too."""
    n = venue_norm(q)
    if not n:
        rows = con.execute(_VENUES + "ORDER BY v.name COLLATE NOCASE, v.id LIMIT ?", (LIMIT,))
    else:
        found = con.execute(
            _VENUES + "WHERE EXISTS (SELECT 1 FROM venue_name x WHERE x.venue_id = v.id AND instr(x.norm, ?) > 0)",
            (n,)).fetchall()
        # Sorted here: "starts with" is by venue_norm, which SQL can't compute. A collection has few venues.
        found.sort(key=lambda r: (not venue_norm(r["name"]).startswith(n), r["name"].casefold(), r["id"]))
        rows = found[:LIMIT]
    return [_venue_row(r) for r in rows]


def venue_summary(con, venue_id) -> dict:
    return _venue_row(con.execute(_VENUES + "WHERE v.id = ?", (venue_id,)).fetchone())
