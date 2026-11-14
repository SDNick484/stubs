"""setlist.fm (spec 7.11): search for the setlists of a show's acts (one act by name and date, or the whole show by
venue and date, matched to the lineup), fetch them live, and remember only the id and link the owner picked for each
lineup entry (`setlist_link`, schema v2). Song data is never written to disk or the database: fetch_setlist keeps it
in memory for an hour at most (setlist.fm's terms). Requests go one after another. No routes; app.py calls these."""
import json
import re
import socket
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import records
import settings
from common import venue_norm

API = "https://api.setlist.fm/rest/1.0"
TIMEOUT = 10
CACHE_SECONDS = 3600
SITE = "https://www.setlist.fm/"
_ID = re.compile(r"[A-Za-z0-9]{1,24}")

_cache = {}  # setlist id -> (expires_at, {"url", "sets"})


class Unavailable(Exception):
    """No API key, an HTTP error other than "not found", a timeout or an unreadable answer."""


def _now():
    return time.monotonic()


def valid_id(setlist_id):
    return isinstance(setlist_id, str) and bool(_ID.fullmatch(setlist_id))


def valid_url(url):
    """Only setlist.fm pages: the panel renders this as a link."""
    return isinstance(url, str) and url.startswith(SITE) and len(url) <= 500 and not re.search(r"[\s\"'<>]", url)


def _get(path, params=None):
    """The decoded JSON at API + path, or None when setlist.fm says 404. Unavailable on everything else."""
    if not settings.SETLISTFM_API_KEY:
        raise Unavailable("no API key")
    url = API + path + ("?" + urlencode(params) if params else "")
    req = Request(url, headers={"x-api-key": settings.SETLISTFM_API_KEY, "Accept": "application/json",
                                "User-Agent": settings.UA})
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404:
            return None
        raise Unavailable(f"HTTP {e.code}") from e
    except (URLError, socket.timeout, TimeoutError, OSError, ValueError) as e:
        raise Unavailable(str(e)) from e


def _name(obj):
    return (obj.get("name") or "") if isinstance(obj, dict) else ""


def _results(data):
    out = []
    for s in (data or {}).get("setlist") or []:
        if not isinstance(s, dict) or not valid_id(s.get("id")):
            continue
        venue = s.get("venue") if isinstance(s.get("venue"), dict) else {}
        out.append({"id": s["id"], "url": s.get("url") or "", "artist": _name(s.get("artist")),
                    "venue": venue.get("name") or "", "city": _name(venue.get("city")),
                    "date": s.get("eventDate") or ""})
    return out


def _day(date):
    y, m, d = date.split("-")
    return f"{d}-{m}-{y}"


def search(artist, date):
    """Setlists by this artist on this day (YYYY-MM-DD): [{"id", "url", "artist", "venue", "city", "date"}]. [] when
    there are none; Unavailable on no key, an HTTP error or a timeout."""
    return _results(_get("/search/setlists", {"artistName": artist, "date": _day(date)}))


def search_venue(venue, city, date):
    """Every act's setlist at this venue (by name, in this city) on this day; the same shape and failures as
    search()."""
    params = {"venueName": venue, "date": _day(date)}
    if city:
        params["cityName"] = city
    return _results(_get("/search/setlists", params))


def _sets(data):
    out = []
    raw = data.get("sets") if isinstance(data.get("sets"), dict) else {}
    for s in raw.get("set") or []:
        songs = [x["name"] for x in (s.get("song") or []) if isinstance(x, dict) and x.get("name")]
        if songs:
            out.append({"name": s.get("name") or "", "encore": bool(s.get("encore")), "songs": songs})
    return out


def fetch_setlist(setlist_id):
    """{"url", "sets": [{"name", "encore", "songs": [str]}]} for a setlist, or None (no key, a miss, an error, a
    timeout). Kept in memory for an hour; never stored."""
    if not valid_id(setlist_id):
        return None
    hit = _cache.get(setlist_id)
    if hit and hit[0] > _now():
        return hit[1]
    _cache.pop(setlist_id, None)
    try:
        data = _get("/setlist/" + quote(setlist_id, safe=""))
    except Unavailable:
        return None
    if not isinstance(data, dict) or not data.get("sets"):
        return None
    result = {"url": data.get("url") or "", "sets": _sets(data)}
    if len(_cache) >= 500:
        _cache.clear()
    _cache[setlist_id] = (_now() + CACHE_SECONDS, result)
    return result


# ---------------------------------------------------------------- links, one per lineup entry
def link(con: sqlite3.Connection, lineup_id, setlist_id, url):
    """Remember which setlist.fm page is this lineup entry's set: its id and link only."""
    con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (?, ?, ?) "
                "ON CONFLICT(lineup_id) DO UPDATE SET setlistfm_id = excluded.setlistfm_id, url = excluded.url",
                (lineup_id, setlist_id, url))


def unlink(con: sqlite3.Connection, lineup_id):
    con.execute("DELETE FROM setlist_link WHERE lineup_id = ?", (lineup_id,))


def linked(con: sqlite3.Connection, lineup_id):
    """(setlistfm_id, url) for the lineup entry, or None."""
    row = con.execute("SELECT setlistfm_id, url FROM setlist_link WHERE lineup_id = ?", (lineup_id,)).fetchone()
    return (row["setlistfm_id"], row["url"]) if row else None


def _acts(con, show_id):
    """The show's lineup in running order (position), each with its link if it has one."""
    return con.execute(
        "SELECT l.id, l.artist_id, l.role, a.name, sl.setlistfm_id, sl.url FROM lineup l "
        "JOIN artist a ON a.id = l.artist_id LEFT JOIN setlist_link sl ON sl.lineup_id = l.id "
        "WHERE l.show_id = ? ORDER BY l.position, l.id", (show_id,)).fetchall()


def panel(con: sqlite3.Connection, show_id, *, editor=False):
    """The show page's setlists: {"acts": [{"lineup_id", "name", "role", "available", "url", "sets"}]}, one per
    linked act in lineup order, each fetched live (one at a time, through the cache). An act whose fetch fails is
    {"available": false} with its stored link and never hides the others. Editors also get "unlinked": the acts with
    no link, [{"lineup_id", "name", "role"}]."""
    acts, unlinked = [], []
    for e in _acts(con, show_id):
        act = {"lineup_id": e["id"], "name": e["name"], "role": e["role"]}
        if e["setlistfm_id"] is None:
            unlinked.append(act)
            continue
        got = fetch_setlist(e["setlistfm_id"])
        if got:
            act.update(available=True, url=got["url"] if valid_url(got["url"]) else e["url"], sets=got["sets"])
        else:
            act.update(available=False, url=e["url"], sets=[])
        acts.append(act)
    out = {"acts": acts}
    if editor:
        out["unlinked"] = unlinked
    return out


def find_for_show(con: sqlite3.Connection, show_id):
    """Search setlist.fm by the show's venue and date (the name on the ticket, then the venue's current name when that
    is a different name, both with the venue's city) and match each result's artist to a lineup entry through
    records.resolve_artist, so aliases count. {"acts": [{"lineup_id", "name", "role", "linked": setlistfm_id|None,
    "results": [...]}] in lineup order, "unmatched": [results matching no one on the lineup]}. Links nothing.
    Unavailable on no key, an HTTP error or a timeout."""
    show = con.execute("SELECT s.date, s.venue_label, v.name AS venue, v.city FROM show s "
                       "JOIN venue v ON v.id = s.venue_id WHERE s.id = ?", (show_id,)).fetchone()
    names = [n for n in (show["venue_label"], show["venue"]) if n and n.strip()]
    if len(names) == 2 and venue_norm(names[0]) == venue_norm(names[1]):
        names.pop()
    results, seen = [], set()
    for name in names:
        for r in search_venue(name, show["city"], show["date"]):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(r)
    acts, by_artist = [], {}
    for e in _acts(con, show_id):
        act = {"lineup_id": e["id"], "name": e["name"], "role": e["role"], "linked": e["setlistfm_id"], "results": []}
        acts.append(act)
        by_artist[e["artist_id"]] = act
    unmatched = []
    for r in results:
        act = by_artist.get(records.resolve_artist(con, r["artist"]))
        (act["results"] if act else unmatched).append(r)
    return {"acts": acts, "unmatched": unmatched}
