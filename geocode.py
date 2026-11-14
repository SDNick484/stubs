"""Venue coordinates (spec 7.6): Nominatim lookups and manual entry. Takes a sqlite3.Connection, never imports app."""
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import settings
from common import ApiError

TIMEOUT = 10
MIN_GAP = 1.0  # Nominatim's usage policy: at most one request per second
_lock = threading.Lock()
_last_call = None  # time.monotonic() of the last request, per process

_COLS = "id, name, city, region, country, lat, lon, geocode_source"


def _fail():
    return ApiError(502, "Lookup failed")


def lookup(name, city, region, country):
    """(lat, lon) of the best match for the venue, None on a miss. Waits so calls are at least a second apart.
    HTTP errors, timeouts and unreadable answers are ApiError(502, "Lookup failed")."""
    global _last_call
    q = ", ".join(p for p in (name, city, region, country) if p)
    url = settings.GEOCODER_URL + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": settings.UA, "Accept": "application/json"})
    with _lock:  # one at a time per process, so the gap holds across threads
        if _last_call is not None:
            wait = MIN_GAP - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(1_000_000)
        except (OSError, urllib.error.URLError, ValueError):  # HTTPError, timeouts and refusals are all OSError
            raise _fail() from None
        finally:
            _last_call = time.monotonic()
    try:
        results = json.loads(body)
        if not results:
            return None
        lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
    except (ValueError, TypeError, KeyError, IndexError):
        raise _fail() from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise _fail()
    return lat, lon


def _venue(con, venue_id):
    row = con.execute(f"SELECT {_COLS} FROM venue WHERE id = ?", (venue_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Venue not found.")
    return dict(row)


def locate_venue(con, venue_id):
    """Look the venue up and store what is found (geocode_source "nominatim"). A miss stores nothing. Returns the
    venue, so a miss shows as lat None."""
    v = _venue(con, venue_id)
    found = lookup(v["name"], v["city"], v["region"], v["country"])
    if found is not None:
        con.execute("UPDATE venue SET lat = ?, lon = ?, geocode_source = 'nominatim', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?", (found[0], found[1], venue_id))
    return _venue(con, venue_id)


def _coord(value, name, limit):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not -limit <= value <= limit:
        raise ApiError(400, f"{name} must be a number from -{limit} to {limit}.")
    return float(value)


def set_coords(con, venue_id, lat, lon):
    """Store coordinates typed by hand (geocode_source "manual"). Out of range is 400. Returns the venue."""
    lat, lon = _coord(lat, "lat", 90), _coord(lon, "lon", 180)
    _venue(con, venue_id)
    con.execute("UPDATE venue SET lat = ?, lon = ?, geocode_source = 'manual', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?", (lat, lon, venue_id))
    return _venue(con, venue_id)


def missing_coords(con):
    """Venues without coordinates, the busiest first: [{"id", "name", "city", "shows"}]."""
    rows = con.execute("SELECT v.id, v.name, v.city, (SELECT COUNT(*) FROM show s WHERE s.venue_id = v.id) AS shows "
                       "FROM venue v WHERE v.lat IS NULL OR v.lon IS NULL "
                       "ORDER BY shows DESC, v.name COLLATE NOCASE, v.id").fetchall()
    return [dict(r) for r in rows]
