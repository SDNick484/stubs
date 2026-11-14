"""Venue editing (Task 20): correct a venue's name and place, and manage the names it has had. Route-free: every
function takes a sqlite3.Connection and runs its change in one transaction.

`venue_name` holds every name a venue has had, the current one included; a show's `venue_label` is one of them. A
rename therefore adds the new name and keeps the old one, so past shows keep the name on their ticket and imports
still find the venue under either. Two venues in one city never share a name (by venue_norm): `records.find_venue`
would then match the lower id for both."""
import re

from common import ApiError, norm, venue_norm
from db import transaction

MAX = {"name": 200, "city": 100, "region": 40}
FIELDS = frozenset(("name", "city", "region", "country"))
_COUNTRY = re.compile(r"[A-Za-z]{2}")


def _clean(value, key, required):
    """Text with its whitespace collapsed, or None when blank (400 when required, not text, or over its limit)."""
    if value is not None and not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    value = " ".join((value or "").split())
    if not value:
        if required:
            raise ApiError(400, f"{key} is required.")
        return None
    if key == "country":
        if not _COUNTRY.fullmatch(value):
            raise ApiError(400, "country must be a two-letter code, such as US.")
        return value.upper()
    if len(value) > MAX[key]:
        raise ApiError(400, f"{key} must be at most {MAX[key]} characters.")
    return value


def _venue(con, venue_id):
    row = con.execute("SELECT * FROM venue WHERE id = ?", (venue_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Venue not found.")
    return row


def _name_value(value):
    name = _clean(value, "name", True)
    if not venue_norm(name):
        raise ApiError(400, "name must have letters or digits.")
    return name


def _clash(con, venue_id, city, name_norm):
    """The other venue in `city` that has this name, or None."""
    rows = con.execute("SELECT v.id, v.name, v.city FROM venue_name vn JOIN venue v ON v.id = vn.venue_id "
                       "WHERE vn.norm = ? AND v.id <> ? ORDER BY v.id", (name_norm, venue_id)).fetchall()
    for r in rows:
        if norm(r["city"]) == norm(city):
            return r
    return None


def _refuse_clash(con, venue_id, city, name_norm):
    other = _clash(con, venue_id, city, name_norm)
    if other is not None:
        raise ApiError(409, f"That name belongs to {other['name']}. Merge them instead.")


def _used(con, venue_id) -> dict:
    """venue_norm of a ticket name -> how many shows (any status) have it as their venue_label."""
    counts: dict = {}
    for r in con.execute("SELECT venue_label FROM show WHERE venue_id = ?", (venue_id,)):
        k = venue_norm(r["venue_label"])
        counts[k] = counts.get(k, 0) + 1
    return counts


def view(con, venue_id) -> dict:
    """{"id", "name", "city", "region", "country", "lat", "lon", "names": [{"name", "norm", "current", "shows"}]}:
    the current name first, then the others by name. `shows` counts the shows with that name on the ticket."""
    v = _venue(con, venue_id)
    used = _used(con, venue_id)
    cur = venue_norm(v["name"])
    names = [{"name": r["name"], "current": r["norm"] == cur, "shows": used.get(r["norm"], 0)}
             for r in con.execute("SELECT name, norm FROM venue_name WHERE venue_id = ? ORDER BY name, rowid",
                                  (venue_id,))]
    names.sort(key=lambda n: not n["current"])
    return {"id": v["id"], "name": v["name"], "city": v["city"], "region": v["region"], "country": v["country"],
            "lat": v["lat"], "lon": v["lon"], "names": names}


def update_venue(con, venue_id, data) -> dict:
    """Change `name`, `city`, `region` and/or `country` (only the keys sent). A new name is added to the venue's
    names and the old one kept. The coordinates are never touched; `coordinates_kept` is true when the place changed
    and the venue has coordinates, so the screen can offer "Locate again". Moving the venue to a city where another
    venue already has one of its names is 409 too."""
    extra = sorted(set(data) - FIELDS)
    if extra:
        raise ApiError(400, f"Unknown field: {extra[0]}.")
    with transaction(con):
        v = _venue(con, venue_id)
        new = {"name": v["name"], "city": v["city"], "region": v["region"], "country": v["country"]}
        if "name" in data:
            new["name"] = _name_value(data["name"])
        if "city" in data:
            new["city"] = _clean(data["city"], "city", True)
        for key in ("region", "country"):
            if key in data:
                new[key] = _clean(data[key], key, False)
        names = {r["norm"] for r in con.execute("SELECT norm FROM venue_name WHERE venue_id = ?", (venue_id,))}
        names.add(venue_norm(new["name"]))
        for n in sorted(names):
            _refuse_clash(con, venue_id, new["city"], n)
        con.execute("INSERT OR IGNORE INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)",
                    (venue_id, new["name"], venue_norm(new["name"])))
        con.execute("UPDATE venue SET name = ?, city = ?, region = ?, country = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?", (new["name"], new["city"], new["region"], new["country"], venue_id))
        moved = any(new[k] != v[k] for k in ("city", "region", "country"))
        out = view(con, venue_id)
        out["coordinates_kept"] = moved and v["lat"] is not None and v["lon"] is not None
        return out


def add_name(con, venue_id, name) -> "tuple[dict, bool]":
    """(view, added). A name the venue already has (by venue_norm) is a no-op; one another venue in the same city has
    is 409."""
    name = _name_value(name)
    with transaction(con):
        v = _venue(con, venue_id)
        n = venue_norm(name)
        if con.execute("SELECT 1 FROM venue_name WHERE venue_id = ? AND norm = ?", (venue_id, n)).fetchone():
            return view(con, venue_id), False
        _refuse_clash(con, venue_id, v["city"], n)
        con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)", (venue_id, name, n))
        return view(con, venue_id), True


def remove_name(con, venue_id, name) -> dict:
    """Delete a former name. 404 when the venue hasn't had it, 409 when it is the current name or a show's ticket
    uses it."""
    name = _name_value(name)
    with transaction(con):
        v = _venue(con, venue_id)
        n = venue_norm(name)
        if not con.execute("SELECT 1 FROM venue_name WHERE venue_id = ? AND norm = ?", (venue_id, n)).fetchone():
            raise ApiError(404, "The venue has not had that name.")
        if n == venue_norm(v["name"]):
            raise ApiError(409, "Rename the venue first.")
        count = _used(con, venue_id).get(n, 0)
        if count:
            raise ApiError(409, f"{count} {'show has' if count == 1 else 'shows have'} this name on the ticket.")
        con.execute("DELETE FROM venue_name WHERE venue_id = ? AND norm = ?", (venue_id, n))
        return view(con, venue_id)
