"""MusicBrainz links on artists (post-v1 #45): a lookup against the MusicBrainz web service and the artist.mbid column.
Takes a sqlite3.Connection, never imports app. MusicBrainz's core data is CC0, so the id and its "the UK band" note
are stored; nothing links itself, the editor picks from the candidates."""
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import settings
from common import ApiError, norm

TIMEOUT = 10
MIN_GAP = 1.0  # MusicBrainz's usage policy: at most one request per second
LIMIT = 5
MAX_NOTE = 500
_lock = threading.Lock()
_last_call = None  # time.monotonic() of the last request, per process

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _fail():
    return ApiError(502, "Lookup failed")


def valid_mbid(value) -> "str | None":
    """The lowercase UUID, or None when value is not a string holding one."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _UUID.fullmatch(value) else None


def _quote(name):
    """name as a Lucene phrase body: backslashes and quotes escaped."""
    return name.replace("\\", "\\\\").replace('"', '\\"')


def search(name) -> list[dict]:
    """Up to five candidates for an artist name, best first: [{"mbid", "name", "disambiguation", "country",
    "score"}] ([] on a miss; disambiguation and country None when MusicBrainz has none). Waits so calls are at least
    a second apart. HTTP errors, timeouts and unreadable answers are ApiError(502, "Lookup failed")."""
    global _last_call
    query = urllib.parse.urlencode({"query": f'artist:"{_quote(name)}"', "fmt": "json", "limit": LIMIT})
    req = urllib.request.Request(f"{settings.MUSICBRAINZ_URL}/artist?{query}",
                                 headers={"User-Agent": settings.UA, "Accept": "application/json"})
    with _lock:  # one at a time per process, so the gap holds across threads
        if _last_call is not None:
            wait = MIN_GAP - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(1_000_000)
        except (OSError, urllib.error.URLError, ValueError):  # HTTPError and timeouts are OSError
            raise _fail() from None
        finally:
            _last_call = time.monotonic()
    try:
        found = json.loads(body)["artists"]
        out = []
        for a in found[:LIMIT]:
            mbid = valid_mbid(a["id"])
            if mbid is None:
                raise ValueError("id")
            out.append({"mbid": mbid, "name": str(a["name"]), "disambiguation": a.get("disambiguation") or None,
                        "country": a.get("country") or None, "score": int(a.get("score", 0))})
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _fail() from None
    return out


def exact(name, candidates) -> "str | None":
    """The mbid "Link all exact" may link: the top candidate when its score is 100, its name equals `name` by
    common.norm and no other candidate scores 100 too. Else None."""
    if not candidates or candidates[0]["score"] != 100 or norm(candidates[0]["name"]) != norm(name):
        return None
    if any(c["score"] == 100 for c in candidates[1:]):
        return None
    return candidates[0]["mbid"]


def _artist(con, artist_id):
    row = con.execute("SELECT id, name, mbid, mb_disambiguation FROM artist WHERE id = ?", (artist_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Artist not found.")
    return dict(row)


def suggest(con, artist_id) -> dict:
    """Search MusicBrainz for the artist's name: {"candidates": search(...), "exact": mbid or None}."""
    a = _artist(con, artist_id)
    found = search(a["name"])
    return {"candidates": found, "exact": exact(a["name"], found)}


def link(con, artist_id, mbid, disambiguation=None) -> dict:
    """Store the MusicBrainz id (and its note) on the artist; relinking replaces both. A bad UUID is 400; an id
    another artist has is 409 "Another artist has this MusicBrainz ID: merge them." with that artist's id as
    ApiError.extra["artist_id"]. Returns {"id", "name", "mbid", "mb_disambiguation"}."""
    clean = valid_mbid(mbid)
    if clean is None:
        raise ApiError(400, "mbid must be a MusicBrainz ID (a UUID).")
    if disambiguation is not None and (not isinstance(disambiguation, str) or len(disambiguation) > MAX_NOTE):
        raise ApiError(400, f"disambiguation must be text of at most {MAX_NOTE} characters.")
    note = (disambiguation or "").strip() or None
    _artist(con, artist_id)
    other = con.execute("SELECT id FROM artist WHERE mbid = ? AND id <> ?", (clean, artist_id)).fetchone()
    if other is not None:
        raise ApiError(409, "Another artist has this MusicBrainz ID: merge them.", artist_id=other["id"])
    try:
        con.execute("UPDATE artist SET mbid = ?, mb_disambiguation = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (clean, note, artist_id))
    except sqlite3.IntegrityError:  # another request took it between the check and the write
        raise ApiError(409, "Another artist has this MusicBrainz ID: merge them.") from None
    return _artist(con, artist_id)


def unlink(con, artist_id) -> dict:
    """Clear the artist's MusicBrainz id and note (nothing to do when there is none). Returns the artist as link does."""
    _artist(con, artist_id)
    con.execute("UPDATE artist SET mbid = NULL, mb_disambiguation = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND mbid IS NOT NULL", (artist_id,))
    return _artist(con, artist_id)


def unlinked(con) -> list[dict]:
    """Artists without a MusicBrainz id, the busiest first: [{"id", "name", "shows"}], shows counting every show
    the artist is on (any status, any role)."""
    rows = con.execute("SELECT a.id, a.name, (SELECT COUNT(*) FROM lineup l WHERE l.artist_id = a.id) AS shows "
                       "FROM artist a WHERE a.mbid IS NULL ORDER BY shows DESC, a.name COLLATE NOCASE, a.id")
    return [dict(r) for r in rows]
