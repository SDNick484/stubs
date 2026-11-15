"""Show editing (spec 7.8 and 7.10): create, edit and delete shows, set a show's lineup, add and remove sources, and
list runs. Route-free: every function takes a sqlite3.Connection; records.py does the creating and resolving.

Saves carry the show's `version`. update_show and set_lineup check it first, inside the same transaction as their
writes, and bump it; a version that no longer matches (another tab saved, or a merge moved the show) is 409 STALE and
writes nothing. Sources are not part of a form save: adding or removing one leaves the version alone. Bad input is
ApiError 400 and, since everything runs in one transaction, leaves no half-made artist, venue, run or name behind."""
import re

import records
from common import ApiError, parse_date, text, venue_norm
from db import transaction
from records import CONFIDENCES, ROLES, STATUSES

STALE = "Someone else just changed this show. Reload to see it."

# Longest text accepted per field; longer is 400, never cut.
MAX = {"billing": 200, "venue_label": 200, "venue": 200, "city": 100, "region": 40, "run": 200, "tour": 200,
       "notes": 5000, "confidence_note": 500, "name": 200, "label": 200, "url": 2000}
SHOW_FIELDS = frozenset(("status", "date", "billing", "venue_id", "venue", "city", "region", "country",
                         "venue_label", "run", "tour", "notes", "rating", "confidence", "confidence_note"))
_COUNTRY = re.compile(r"[A-Za-z]{2}")
_URL = re.compile(r"https?://[^\s/?#]+\S*", re.I)


# ---------------------------------------------------------------- checks
def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _id(value, what):
    if not _whole(value) or not 0 < value < 2 ** 63:
        raise ApiError(400, f"{what} must be an id.")
    return value


def _choice(value, allowed, what):
    if value not in allowed:
        raise ApiError(400, f"{what} must be one of: {', '.join(allowed)}.")
    return value


def _rating(value):
    if value is None:
        return None
    if not _whole(value) or not 1 <= value <= 5:
        raise ApiError(400, "rating must be 1 to 5, or empty.")
    return value


def _date(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, "date is required (YYYY-MM-DD, YYYY-MM or YYYY).")
    try:
        return parse_date(value)
    except ValueError:
        raise ApiError(400, "date must be YYYY-MM-DD, YYYY-MM or YYYY.") from None


def _unknown(data, allowed):
    extra = sorted(set(data) - allowed)
    if extra:
        raise ApiError(400, f"Unknown field: {extra[0]}.")


def _venue(con, data):
    """(venue_id, the name typed for a new venue) from `venue_id`, or from `venue` + `city` (+ `region`,
    `country`) found or created with records.get_or_create_venue. (None, None) when neither is given."""
    if data.get("venue_id") is not None:
        vid = _id(data["venue_id"], "venue_id")
        if not con.execute("SELECT 1 FROM venue WHERE id = ?", (vid,)).fetchone():
            raise ApiError(400, "Unknown venue.")
        return vid, None
    if not any(data.get(k) for k in ("venue", "city")):
        return None, None
    name = text(data, "venue", MAX["venue"])
    city = text(data, "city", MAX["city"])
    if not name or not city:
        raise ApiError(400, "A new venue needs a name and a city.")
    region = text(data, "region", MAX["region"])
    country = text(data, "country", 2)
    if country and not _COUNTRY.fullmatch(country):
        raise ApiError(400, "country must be a two-letter code, such as US.")
    return records.get_or_create_venue(con, name, city, region, country.upper() if country else None), name


def _label(con, venue_id, label):
    """The name on the ticket: `label`, or the venue's current name when there is none. A label the venue has never
    gone by (compared by venue_norm) is added to its names, as a former name: the current name stays."""
    if not label:
        return con.execute("SELECT name FROM venue WHERE id = ?", (venue_id,)).fetchone()["name"]
    con.execute("INSERT OR IGNORE INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)",
                (venue_id, label, venue_norm(label)))
    return label


def _run(con, data):
    name = text(data, "run", MAX["run"])
    return records.get_or_create_run(con, name) if name else None


def _check(con, show_id, version):
    """The show's row, after the stale check. 404 for no such show, 409 for an old version."""
    if not _whole(version):
        raise ApiError(400, "version is required: send the version the form was loaded with.")
    row = con.execute("SELECT id, venue_id, version FROM show WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Show not found.")
    if row["version"] != version:
        raise ApiError(409, STALE)
    return row


def _bump(con, show_id) -> int:
    con.execute("UPDATE show SET version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (show_id,))
    return con.execute("SELECT version FROM show WHERE id = ?", (show_id,)).fetchone()["version"]


# ---------------------------------------------------------------- shows
def create(con, data) -> int:
    """A new show from the editor's form or a script: billing, date, status (default attended), a venue by
    `venue_id` or by `venue` + `city` (+ `region`, `country`), and optionally venue_label, run, tour, notes, rating,
    confidence, confidence_note and lineup (entries as set_lineup takes them, without ids). With no `lineup` key the
    billing is the headliner; `"lineup": []` leaves the bill empty. Returns the new show's id."""
    _unknown(data, SHOW_FIELDS | {"lineup"})
    with transaction(con):
        billing = text(data, "billing", MAX["billing"], required=True)
        date = _date(data.get("date"))[0]
        vid, typed = _venue(con, data)
        if vid is None:
            raise ApiError(400, "Pick a venue, or give a new venue's name and city.")
        sid = records.create_show(
            con, status=_choice(data.get("status") or "attended", STATUSES, "status"), date=date, billing=billing,
            venue_id=vid, venue_label=_label(con, vid, text(data, "venue_label", MAX["venue_label"]) or typed),
            run_id=_run(con, data), tour=text(data, "tour", MAX["tour"]), notes=text(data, "notes", MAX["notes"]),
            confidence=_choice(data.get("confidence") or "confirmed", CONFIDENCES, "confidence"),
            confidence_note=text(data, "confidence_note", MAX["confidence_note"]))
        rating = _rating(data.get("rating"))
        if rating is not None:
            con.execute("UPDATE show SET rating = ? WHERE id = ?", (rating, sid))
        _write_lineup(con, sid, data["lineup"] if "lineup" in data else [{"name": billing, "role": "headliner"}])
        return sid


def update_show(con, show_id, fields: dict, version: int) -> int:
    """Change any of SHOW_FIELDS on a show and return its new version. `run` is a run's name (found or created) or
    null; `venue_id`, or `venue` + `city` for a venue to find or create, moves the show; `venue_label` must be one of
    the venue's names, and a new one is added to them. Moving without a label puts the venue's own name (or the name
    typed for it) on the ticket. Stale -> 409 STALE, nothing written."""
    _unknown(fields, SHOW_FIELDS)
    with transaction(con):
        row = _check(con, show_id, version)
        sets = {}
        if "status" in fields:
            sets["status"] = _choice(fields["status"], STATUSES, "status")
        if "date" in fields:
            sets["date"], sets["date_precision"], sets["sort_date"] = _date(fields["date"])
        if "billing" in fields:
            sets["billing"] = text(fields, "billing", MAX["billing"], required=True)
        for key in ("tour", "notes", "confidence_note"):
            if key in fields:
                sets[key] = text(fields, key, MAX[key])
        if "rating" in fields:
            sets["rating"] = _rating(fields["rating"])
        if "confidence" in fields:
            sets["confidence"] = _choice(fields["confidence"], CONFIDENCES, "confidence")
        if "run" in fields:
            sets["run_id"] = _run(con, fields)
        vid, typed = _venue(con, fields)
        if (vid is not None and vid != row["venue_id"]) or "venue_label" in fields or typed:
            vid = vid if vid is not None else row["venue_id"]
            sets["venue_id"] = vid
            sets["venue_label"] = _label(con, vid, text(fields, "venue_label", MAX["venue_label"]) or typed)
        if sets:
            con.execute(f"UPDATE show SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        (*sets.values(), show_id))
        return _bump(con, show_id)


def delete(con, show_id) -> None:
    """records.delete_show: the lineup, sources, companions, images (and their files), setlist links and stored
    setlists go too."""
    records.delete_show(con, show_id)


# ---------------------------------------------------------------- lineups
def _write_lineup(con, show_id, entries):
    """Make the show's lineup exactly `entries`, in order (position 1, 2, ...). Call inside a transaction."""
    if not isinstance(entries, list):
        raise ApiError(400, "lineup must be a list of acts.")
    existing = {r["id"]: r["artist_id"] for r in con.execute("SELECT id, artist_id FROM lineup WHERE show_id = ?",
                                                             (show_id,))}
    plan, kept, artists = [], set(), set()
    for position, e in enumerate(entries, start=1):
        if not isinstance(e, dict):
            raise ApiError(400, "Each act in the lineup must be an object.")
        eid = e.get("id")
        if eid is not None and (not _whole(eid) or eid not in existing or eid in kept):
            raise ApiError(400, "That lineup entry is not on this show. Reload and try again.")
        kept.add(eid)
        name = text(e, "name", MAX["name"])
        if not name:
            raise ApiError(400, "Each act needs a name.")
        role = _choice(e.get("role"), ROLES, "role")
        confidence = _choice(e.get("confidence") or "confirmed", CONFIDENCES, "confidence")
        note, rating = text(e, "confidence_note", MAX["confidence_note"]), _rating(e.get("rating"))
        artist = records.get_or_create_artist(con, name)
        if artist in artists:
            raise ApiError(400, f"{name} is on the lineup twice.")
        artists.add(artist)
        plan.append((eid, artist, role, position, rating, confidence, note))
    gone = [i for i in existing if i not in kept]
    con.executemany("DELETE FROM lineup WHERE id = ?", [(i,) for i in gone])  # their sources and setlists cascade
    records.drop_unused_songs(con, [existing[i] for i in gone])
    # An entry renamed to another artist keeps its id and sources. Park those entries on a placeholder artist first
    # (checked only at commit), so two entries that swap names don't collide on UNIQUE(show_id, artist_id).
    moving = [p[0] for p in plan if p[0] is not None and existing[p[0]] != p[1]]
    if moving:
        con.execute("PRAGMA defer_foreign_keys = ON")
        con.executemany("UPDATE lineup SET artist_id = -id WHERE id = ?", [(i,) for i in moving])
    for eid, artist, role, position, rating, confidence, note in plan:
        if eid is None:
            con.execute("INSERT INTO lineup (show_id, artist_id, role, position, rating, confidence, confidence_note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (show_id, artist, role, position, rating, confidence, note))
        else:
            con.execute("UPDATE lineup SET artist_id = ?, role = ?, position = ?, rating = ?, confidence = ?, "
                        "confidence_note = ? WHERE id = ?", (artist, role, position, rating, confidence, note, eid))
    for eid in moving:  # a stored setlist's songs follow the entry to its new act
        records.move_setlist_songs(con, eid, existing[eid], next(p[1] for p in plan if p[0] == eid))


def set_lineup(con, show_id, entries: list, version: int) -> int:
    """Replace a show's lineup with `entries` ({"id"?, "name", "role", "confidence", "confidence_note", "rating"}) in
    order. Entries with an id are updated (a new name moves the entry to that artist, keeping its sources and stored
    setlist, whose songs become the new artist's); entries left out are removed, with their sources and setlists;
    names without an id are resolved or created with records.get_or_create_artist. One artist twice is 400. Returns
    the show's new version; stale -> 409 STALE."""
    with transaction(con):
        _check(con, show_id, version)
        _write_lineup(con, show_id, entries)
        return _bump(con, show_id)


# ---------------------------------------------------------------- sources and runs
def add_show_source(con, show_id, data) -> dict:
    """{"label", "url"?, "lineup_id"?} -> the new source {"id", "label", "url", "lineup_id"}. The url must be http(s);
    lineup_id must be an entry of this show (None: the source backs the show itself)."""
    if not con.execute("SELECT 1 FROM show WHERE id = ?", (show_id,)).fetchone():
        raise ApiError(404, "Show not found.")
    label = text(data, "label", MAX["label"])
    if not label:
        raise ApiError(400, "A source needs a label.")
    url = text(data, "url", MAX["url"])
    if url and not _URL.fullmatch(url):
        raise ApiError(400, "url must start with http:// or https://.")
    lineup_id = data.get("lineup_id")
    if lineup_id is not None:
        _id(lineup_id, "lineup_id")
        if not con.execute("SELECT 1 FROM lineup WHERE id = ? AND show_id = ?", (lineup_id, show_id)).fetchone():
            raise ApiError(400, "That lineup entry is not on this show.")
    sid = records.add_source(con, show_id, label, url, lineup_id)
    return {"id": sid, "label": label, "url": url, "lineup_id": lineup_id}


def remove_source(con, source_id) -> None:
    if con.execute("DELETE FROM source WHERE id = ?", (source_id,)).rowcount == 0:
        raise ApiError(404, "Source not found.")


def list_runs(con) -> list[dict]:
    """Every run, by name: [{"id", "name", "shows"}], shows counting the shows in it (any status)."""
    rows = con.execute("SELECT r.id, r.name, (SELECT COUNT(*) FROM show s WHERE s.run_id = r.id) AS shows FROM run r "
                       "ORDER BY r.name COLLATE NOCASE, r.id")
    return [{"id": r["id"], "name": r["name"], "shows": r["shows"]} for r in rows]
