"""setlist.fm (spec 7.11): search for a show's setlist, fetch it live, and remember only the id and link the owner
picked (`setlist_link`). Song data is never written to disk or the database: fetch_setlist keeps it in memory for
an hour at most (setlist.fm's terms). No routes; app.py calls these."""
import json
import re
import socket
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import settings

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


def search(artist, date):
    """Setlists by this artist on this day (YYYY-MM-DD): [{"id", "url", "artist", "venue", "city", "date"}]. [] when
    there are none; Unavailable on no key, an HTTP error or a timeout."""
    y, m, d = date.split("-")
    data = _get("/search/setlists", {"artistName": artist, "date": f"{d}-{m}-{y}"})
    out = []
    for s in (data or {}).get("setlist") or []:
        if not isinstance(s, dict) or not valid_id(s.get("id")):
            continue
        venue = s.get("venue") if isinstance(s.get("venue"), dict) else {}
        out.append({"id": s["id"], "url": s.get("url") or "", "artist": _name(s.get("artist")),
                    "venue": venue.get("name") or "", "city": _name(venue.get("city")),
                    "date": s.get("eventDate") or ""})
    return out


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


def link(con: sqlite3.Connection, show_id, setlist_id, url):
    """Remember which setlist.fm page is this show's: its id and link only."""
    con.execute("INSERT INTO setlist_link (show_id, setlistfm_id, url) VALUES (?, ?, ?) "
                "ON CONFLICT(show_id) DO UPDATE SET setlistfm_id = excluded.setlistfm_id, url = excluded.url",
                (show_id, setlist_id, url))


def unlink(con: sqlite3.Connection, show_id):
    con.execute("DELETE FROM setlist_link WHERE show_id = ?", (show_id,))


def linked(con: sqlite3.Connection, show_id):
    """(setlistfm_id, url) for the show, or None."""
    row = con.execute("SELECT setlistfm_id, url FROM setlist_link WHERE show_id = ?", (show_id,)).fetchone()
    return (row["setlistfm_id"], row["url"]) if row else None


def panel(con: sqlite3.Connection, show_id):
    """The show page's setlist: {"available": bool, "url": str|None, "sets": [...]}."""
    ref = linked(con, show_id)
    if not ref:
        return {"available": False, "url": None, "sets": []}
    got = fetch_setlist(ref[0])
    if not got:
        return {"available": False, "url": ref[1], "sets": []}
    return {"available": True, "url": got["url"] if valid_url(got["url"]) else ref[1], "sets": got["sets"]}
