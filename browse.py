"""The reads behind the browse pages (spec 7.7): show lists, a show, an artist, a venue, the years and a city. Route-free:
every function takes a sqlite3.Connection, and app.py serves each one twice, at /api/ with editor=False and at
/admin/api/ with editor=True.

What the public gets (spec 6): attended shows only, and nothing editor-only. An upcoming show is None (a 404, the same
as an id that doesn't exist), and so is an artist, venue or city that only upcoming shows would reveal; a venue name
used only on an upcoming show's ticket is left out too. Editors also get upcoming shows (in an `upcoming` list beside
the attended `shows` on artist, venue and city pages; first in show lists) and, on a show, notes, rating, confidence
and sources. Counts (`times`, `by_role`, years) use attended shows only, for everyone (spec 5, 7.5).

Lists are newest first: by sort_date, then id, descending. A city is (country, region, city); "-" (or "") for country
or region matches a venue that has none, so the page for Lyon, France is /api/cities/FR/-/Lyon."""
from collections import Counter

from common import norm, venue_norm

ROLES = ("headliner", "coheadliner", "opener", "performer")

_COLUMNS = """s.id, s.status, s.date, s.date_precision, s.sort_date, s.billing, s.venue_id, s.venue_label, s.tour,
  s.notes, s.rating, s.confidence, s.confidence_note, s.version, v.city, v.region, v.country, r.name AS run_name"""
_FROM = "FROM show s JOIN venue v ON v.id = s.venue_id LEFT JOIN run r ON r.id = s.run_id"
SHOW_SELECT = f"SELECT {_COLUMNS} {_FROM}"  # the rows show_row() reads
_NEWEST = "s.sort_date DESC, s.id DESC"


# ---------------------------------------------------------------- shows
def show_row(con, row) -> dict:
    """One show for a list (the Show row contract). `row` is a row of SHOW_SELECT. Public-safe: no editor-only field.
    `headliners` are the headliners and co-headliners in lineup order (empty for a festival day of performers)."""
    heads = con.execute("SELECT a.name FROM lineup l JOIN artist a ON a.id = l.artist_id "
                        "WHERE l.show_id = ? AND l.role IN ('headliner', 'coheadliner') ORDER BY l.position, l.id",
                        (row["id"],))
    return {"id": row["id"], "date": row["date"], "precision": row["date_precision"], "sort_date": row["sort_date"],
            "billing": row["billing"], "status": row["status"],
            "venue": {"id": row["venue_id"], "label": row["venue_label"], "city": row["city"], "region": row["region"],
                      "country": row["country"]},
            "run": row["run_name"], "headliners": [h["name"] for h in heads]}


def list_shows(con, *, editor, year=None, artist_id=None, venue_id=None, city=None) -> list[dict]:
    """Show rows, newest first. The public gets attended shows only; editors get upcoming shows too, ahead of the
    rest. year matches the year of sort_date ("2027" lists under 2027); city matches the venue's city by norm()."""
    where, args = [], []
    if not editor:
        where.append("s.status = 'attended'")
    if year is not None:
        where.append("substr(s.sort_date, 1, 4) = ?")
        args.append(f"{int(year):04d}")
    if artist_id is not None:
        where.append("EXISTS (SELECT 1 FROM lineup l WHERE l.show_id = s.id AND l.artist_id = ?)")
        args.append(artist_id)
    if venue_id is not None:
        where.append("s.venue_id = ?")
        args.append(venue_id)
    sql = SHOW_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    rows = con.execute(f"{sql} ORDER BY s.status = 'upcoming' DESC, {_NEWEST}", args).fetchall()
    if city is not None:
        rows = [r for r in rows if norm(r["city"]) == norm(city)]
    return [show_row(con, r) for r in rows]


def _sources(con, show_id) -> dict:
    """lineup_id (None for the show itself) -> [{"id", "label", "url"}], oldest first."""
    out = {}
    for s in con.execute("SELECT id, lineup_id, label, url FROM source WHERE show_id = ? ORDER BY id", (show_id,)):
        out.setdefault(s["lineup_id"], []).append({"id": s["id"], "label": s["label"], "url": s["url"]})
    return out


def show_detail(con, show_id, *, editor) -> dict | None:
    """The show row plus tour, lineup, prev_id and next_id; for editors also notes, rating, confidence,
    confidence_note, version, sources, and per lineup entry rating, confidence, confidence_note and sources. None when
    there is no such show, or it is upcoming and this is the public. Previous/next step by sort_date, then id, over
    the shows this reader may see (attended only for the public)."""
    row = con.execute(f"{SHOW_SELECT} WHERE s.id = ?", (show_id,)).fetchone()
    if row is None or (not editor and row["status"] != "attended"):
        return None
    d = show_row(con, row)
    d["tour"] = row["tour"]
    sources = _sources(con, show_id) if editor else {}
    d["lineup"] = []
    for e in con.execute("SELECT l.id, l.artist_id, a.name, l.role, l.position, l.rating, l.confidence, l.confidence_note "
                         "FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? ORDER BY l.position, l.id",
                         (show_id,)):
        entry = {"id": e["id"], "artist_id": e["artist_id"], "name": e["name"], "role": e["role"], "position": e["position"]}
        if editor:
            entry.update(rating=e["rating"], confidence=e["confidence"], confidence_note=e["confidence_note"],
                         sources=sources.get(e["id"], []))
        d["lineup"].append(entry)
    seen = "" if editor else " AND status = 'attended'"
    step = "SELECT id FROM show WHERE (sort_date, id) {} (?, ?)" + seen + " ORDER BY sort_date {}, id {} LIMIT 1"
    key = (row["sort_date"], row["id"])
    prev = con.execute(step.format("<", "DESC", "DESC"), key).fetchone()
    nxt = con.execute(step.format(">", "ASC", "ASC"), key).fetchone()
    d["prev_id"], d["next_id"] = (prev["id"] if prev else None), (nxt["id"] if nxt else None)
    if editor:
        d.update(notes=row["notes"], rating=row["rating"], confidence=row["confidence"],
                 confidence_note=row["confidence_note"], version=row["version"], sources=sources.get(None, []))
    return d


def _split(con, rows, editor, extra=()):
    """(attended show rows, upcoming show rows or None for the public). extra names columns copied onto each row."""
    attended, upcoming = [], []
    for r in rows:
        item = {**show_row(con, r), **{k: r[k] for k in extra}}
        (attended if r["status"] == "attended" else upcoming).append(item)
    return attended, (upcoming if editor else None)


def _with_upcoming(d, upcoming):
    if upcoming is not None:
        d["upcoming"] = upcoming
    return d


# ---------------------------------------------------------------- artists, venues, years, cities
def artist_detail(con, artist_id, *, editor) -> dict | None:
    """{"id", "name", "times", "first", "last", "by_role", "shows", "upcoming" (editors only)}. times counts the
    attended shows the artist is on in any role; by_role has only the roles with a count; first and last are the
    oldest and newest attended dates (None with no attended show). shows are show rows with the artist's "role"."""
    a = con.execute("SELECT id, name FROM artist WHERE id = ?", (artist_id,)).fetchone()
    if a is None:
        return None
    rows = con.execute(f"SELECT {_COLUMNS}, l.role AS role {_FROM} JOIN lineup l ON l.show_id = s.id "
                       f"WHERE l.artist_id = ? ORDER BY {_NEWEST}", (artist_id,)).fetchall()
    shows, upcoming = _split(con, rows, editor, extra=("role",))
    if not shows and not editor:
        return None
    counts = Counter(r["role"] for r in shows)
    d = {"id": a["id"], "name": a["name"], "times": len(shows),
         "first": shows[-1]["date"] if shows else None, "last": shows[0]["date"] if shows else None,
         "by_role": {role: counts[role] for role in ROLES if counts[role]}, "shows": shows}
    return _with_upcoming(d, upcoming)


def venue_detail(con, venue_id, *, editor) -> dict | None:
    """{"id", "name", "names", "city", "region", "country", "lat", "lon", "times", "shows", "upcoming" (editors
    only)}. names is every name the venue has had, the current one first. The public doesn't get a name that only an
    upcoming show's ticket uses."""
    v = con.execute("SELECT id, name, city, region, country, lat, lon FROM venue WHERE id = ?", (venue_id,)).fetchone()
    if v is None:
        return None
    rows = con.execute(f"{SHOW_SELECT} WHERE s.venue_id = ? ORDER BY {_NEWEST}", (venue_id,)).fetchall()
    shows, upcoming = _split(con, rows, editor)
    if not shows and not editor:
        return None
    hidden = set()
    if not editor:
        labels = {status: {venue_norm(r["venue_label"]) for r in rows if r["status"] == status}
                  for status in ("attended", "upcoming")}
        hidden = labels["upcoming"] - labels["attended"]
    current = venue_norm(v["name"])
    names = [v["name"]] + [n["name"] for n in con.execute(
        "SELECT name, norm FROM venue_name WHERE venue_id = ? ORDER BY name, rowid", (venue_id,))
        if n["norm"] != current and n["norm"] not in hidden]
    d = {"id": v["id"], "name": v["name"], "names": names, "city": v["city"], "region": v["region"],
         "country": v["country"], "lat": v["lat"], "lon": v["lon"], "times": len(shows), "shows": shows}
    return _with_upcoming(d, upcoming)


def years(con, *, editor) -> list[dict]:
    """[{"year": int, "count": int}] for each year with an attended show, oldest first. Upcoming shows count nowhere,
    so editors get the same answer (the argument keeps the read functions alike)."""
    del editor
    rows = con.execute("SELECT substr(sort_date, 1, 4) AS y, count(*) AS n FROM show WHERE status = 'attended' "
                       "GROUP BY y ORDER BY y")
    return [{"year": int(r["y"]), "count": r["n"]} for r in rows]


def _place(value) -> str:
    """A country or region as a path segment means: "-" or "" is none; anything else matches by norm()."""
    value = (value or "").strip()
    return "" if value == "-" else norm(value)


def city_detail(con, country, region, city, *, editor) -> dict | None:
    """{"city", "region", "country", "times", "shows", "upcoming" (editors only)} for the venues in this city
    (matched by norm(), so case doesn't matter; "-" for a country or region matches a venue without one). The names
    come as stored on the first matching venue with a show the reader sees. None when no venue is there, or for the
    public, no attended show."""
    want = (_place(country), _place(region), norm(city))
    venues = [v for v in con.execute("SELECT id, city, region, country FROM venue ORDER BY id")
              if (norm(v["country"]), norm(v["region"]), norm(v["city"])) == want]
    if not venues or not want[2]:
        return None
    marks = ",".join("?" * len(venues))
    rows = con.execute(f"{SHOW_SELECT} WHERE s.venue_id IN ({marks}) ORDER BY {_NEWEST}", [v["id"] for v in venues])
    shows, upcoming = _split(con, rows.fetchall(), editor)
    if not shows and not editor:
        return None
    shown = {r["venue"]["id"] for r in shows}
    first = next((v for v in venues if v["id"] in shown), venues[0])  # the public's spelling comes from a shown venue
    d = {"city": first["city"], "region": first["region"], "country": first["country"], "times": len(shows),
         "shows": shows}
    return _with_upcoming(d, upcoming)
