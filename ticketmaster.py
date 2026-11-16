"""Ticketmaster Discovery API, for concert watch (post-v1 #47): a watched artist's upcoming music events. Takes no
Connection and never imports app; watch.py decides what to keep.

An event is the artist's only when one of its attractions is the artist: by MusicBrainz id when the artist has one and
the attraction lists one (so a same-name band with another id is not matched), else by the exact common.norm of the
attraction's name. Never a fuzzy match. Only what a sighting stores is returned (spec 7.14): the event id, date, venue,
city, region, country, coordinates, link and public on-sale time. The key goes in the query string (the API's only
way), so it is never logged: failures raise ApiError(502, "Lookup failed") with no detail."""
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import settings
from common import ApiError, norm

URL = "https://app.ticketmaster.com/discovery/v2/events.json"
TIMEOUT = 10
MIN_GAP = 0.25  # the Discovery API allows 5 requests a second (and 5,000 a day)
PAGE = 100
_lock = threading.Lock()
_last_call = None  # time.monotonic() of the last request, per process


def enabled() -> bool:
    return bool(settings.TICKETMASTER_API_KEY)


def _fail():
    return ApiError(502, "Lookup failed")


def _get(params):
    global _last_call
    query = urllib.parse.urlencode({**params, "apikey": settings.TICKETMASTER_API_KEY})
    req = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": settings.UA, "Accept": "application/json"})
    with _lock:  # one at a time per process, so the gap holds across threads
        if _last_call is not None:
            wait = MIN_GAP - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(4_000_000)
        except (OSError, urllib.error.URLError, ValueError):  # HTTPError and timeouts are OSError
            raise _fail() from None
        finally:
            _last_call = time.monotonic()
    try:
        return json.loads(body)
    except ValueError:
        raise _fail() from None


def _mbids(attraction):
    links = (attraction.get("externalLinks") or {}).get("musicbrainz") or []
    return {str(x.get("id", "")).strip().lower() for x in links if isinstance(x, dict) and x.get("id")}


def is_artist(attraction, name, mbid=None) -> bool:
    """This attraction is the artist: the MusicBrainz id decides when both sides have one, else the name by norm."""
    ids = _mbids(attraction)
    if mbid and ids:
        return mbid.lower() in ids
    return bool(norm(name)) and norm(attraction.get("name")) == norm(name)


def _coord(value, limit):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and -limit <= x <= limit else None


def _event(e):
    """The fields a sighting keeps, or None for an event that can't be one (no day, venue or city)."""
    venue = ((e.get("_embedded") or {}).get("venues") or [{}])[0]
    start = (e.get("dates") or {}).get("start") or {}
    out = {"source_event_id": str(e.get("id") or ""), "date": start.get("localDate") or "",
           "venue": (venue.get("name") or "").strip(), "city": ((venue.get("city") or {}).get("name") or "").strip(),
           "region": (venue.get("state") or {}).get("stateCode") or None,
           "country": (venue.get("country") or {}).get("countryCode") or None,
           "lat": _coord((venue.get("location") or {}).get("latitude"), 90),
           "lon": _coord((venue.get("location") or {}).get("longitude"), 180),
           "url": e.get("url") if str(e.get("url") or "").startswith("https://") else None,
           "on_sale": ((e.get("sales") or {}).get("public") or {}).get("startDateTime") or None}
    if out["lat"] is None or out["lon"] is None:
        out["lat"] = out["lon"] = None
    if not (out["source_event_id"] and len(out["date"]) == 10 and out["venue"] and out["city"]):
        return None
    return out


def events(name, mbid=None, today=None) -> list[dict]:
    """The artist's upcoming music events on Ticketmaster, soonest first (see the module note for the match); [] on
    a miss. Cancelled events and dates before `today` (YYYY-MM-DD) are left out. 502 "Lookup failed" on an HTTP
    error, a timeout or an unreadable answer."""
    params = {"keyword": name, "classificationName": "music", "size": PAGE, "sort": "date,asc"}
    if today:
        params["startDateTime"] = f"{today}T00:00:00Z"
    data = _get(params)
    try:
        found = (data.get("_embedded") or {}).get("events") or []
        out = []
        for e in found:
            if ((e.get("dates") or {}).get("status") or {}).get("code") in ("cancelled", "canceled"):
                continue
            if not any(is_artist(a, name, mbid) for a in (e.get("_embedded") or {}).get("attractions") or []):
                continue
            ev = _event(e)
            if ev is not None and (today is None or ev["date"] >= today):
                out.append(ev)
    except (AttributeError, TypeError, IndexError):
        raise _fail() from None
    return out
