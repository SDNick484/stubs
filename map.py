"""The data behind the home map (spec 7.7): attended shows grouped by venue, venues with coordinates only. Route-free;
GET /api/map is in app.py. Upcoming shows count nowhere, so editors get the same answer as the public."""


def _where(year):
    """The WHERE clause and arguments for attended shows, in one year when given (year of sort_date)."""
    if year is None:
        return "s.status = 'attended'", []
    return "s.status = 'attended' AND substr(s.sort_date, 1, 4) = ?", [f"{int(year):04d}"]


def venue_points(con, *, year=None) -> list[dict]:
    """[{"venue_id", "name", "city", "lat", "lon", "count", "show_ids"}] for each venue that has coordinates and an
    attended show (in `year` when given), busiest first, then by name. show_ids run newest first."""
    where, args = _where(year)
    rows = con.execute(f"""SELECT v.id, v.name, v.city, v.lat, v.lon, s.id AS show_id
        FROM show s JOIN venue v ON v.id = s.venue_id
        WHERE {where} AND v.lat IS NOT NULL AND v.lon IS NOT NULL
        ORDER BY s.sort_date DESC, s.id DESC""", args).fetchall()
    points = {}
    for r in rows:
        p = points.setdefault(r["id"], {"venue_id": r["id"], "name": r["name"], "city": r["city"], "lat": r["lat"],
                                        "lon": r["lon"], "count": 0, "show_ids": []})
        p["count"] += 1
        p["show_ids"].append(r["show_id"])
    return sorted(points.values(), key=lambda p: (-p["count"], p["name"].lower(), p["venue_id"]))


def unmapped_count(con, *, year=None) -> int:
    """How many attended shows (in `year` when given) are at a venue without coordinates."""
    where, args = _where(year)
    return con.execute(f"""SELECT count(*) FROM show s JOIN venue v ON v.id = s.venue_id
        WHERE {where} AND (v.lat IS NULL OR v.lon IS NULL)""", args).fetchone()[0]
