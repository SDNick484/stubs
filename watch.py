"""Concert watch (post-v1 #47): the artists the editor watches, where "near" is, and the upcoming concerts (sightings)
sources report for them. Route-free: every function takes a sqlite3.Connection and never imports app. Editor-only,
all of it: who he watches, artists he has never seen, the areas and the sightings.

Who: nothing is watched automatically. suggest_from_history and suggest_from_lastfm only suggest; the editor ticks.
An artist he has never seen is an `artist` row with no lineup, so it gets the same norm, alias and MusicBrainz
matching as every other artist; it never counts anywhere public (the public reads count attended shows only).

Checking: run() checks the `limit` watched, unmuted artists checked longest ago against each source that is on
(Ticketmaster, when TICKETMASTER_API_KEY is set), keeps an event only when it is within a watch area's radius or the
artist is marked `anywhere`, deletes sightings that are past (the day after their date) or that a source stopped
returning, and pushes the new ones (notify.py). The email "source" is POST /admin/api/watch/sightings (file_email):
Claude, reading the owner's inbox with its own connector, files what it finds; the app never reads mail.

A sighting that imports.match would call "have" against a show already in the database is "You have tickets": shown,
never pushed."""
import hashlib
import math
import time

import geocode
import imports
import lastfm
import notify
import records
import settings
import ticketmaster
from common import ApiError, norm, parse_date, text, venue_norm
from db import transaction

ORIGINS = ("history", "manual", "lastfm")
SOURCES = {"ticketmaster": ticketmaster}  # the sources run() asks, in order; each has enabled() and events()
LABELS = {"ticketmaster": "Ticketmaster", "bandsintown": "Bandsintown", "email": "Email"}
LIMIT = 40              # artists per run: an hourly run rechecks ~1,000 a day
BUDGET = 25.0           # seconds a run starts new lookups for; with one slow source call, one slow geocode and the
                        # push after it (10 s timeouts each) a run still ends inside gunicorn's 60 s timeout
LOCK_STALE = 600.0      # a run lock older than this was left by a worker that died
MAX_EVENTS = 25         # concerts in one POST /admin/api/watch/sightings
MAX_RADIUS = 20000.0    # km: half the earth's circumference
LEN = {"name": 200, "venue": 200, "city": 100, "region": 20, "url": 2000, "note": 500, "area": 100}
EARTH_KM = 6371.0088


class _OutOfTime(Exception):
    pass


_GEOCODER_DOWN = ("geocoder", "down")  # a key in locate()'s cache


def _bool(value, what):
    if not isinstance(value, bool):
        raise ApiError(400, f"{what} must be true or false.")
    return value


# ---------------------------------------------------------------- who is watched
_TIMES = ("(SELECT COUNT(DISTINCT s.id) FROM lineup l JOIN show s ON s.id = l.show_id "
          "WHERE l.artist_id = a.id AND s.status = 'attended')")
_LAST = ("(SELECT s.date FROM lineup l JOIN show s ON s.id = l.show_id WHERE l.artist_id = a.id "
         "AND s.status = 'attended' ORDER BY s.sort_date DESC, s.id DESC LIMIT 1)")


def suggest_from_history(con, min_times=1) -> list[dict]:
    """Artists on attended shows (any role) not watched yet, seen at least `min_times`: [{"artist_id", "name", "times",
    "last"}], most seen first, then the most recently seen. `last` is the latest attended show's date."""
    rows = con.execute(
        f"SELECT a.id AS artist_id, a.name, {_TIMES} AS times, {_LAST} AS last FROM artist a "
        "WHERE NOT EXISTS (SELECT 1 FROM watch w WHERE w.artist_id = a.id) AND times >= ? "
        "ORDER BY times DESC, (SELECT MAX(s.sort_date) FROM lineup l JOIN show s ON s.id = l.show_id "
        "WHERE l.artist_id = a.id AND s.status = 'attended') DESC, a.name COLLATE NOCASE, a.id",
        (max(1, int(min_times)),))
    return [dict(r) for r in rows]


def _seen(con, artist_id) -> bool:
    return con.execute("SELECT 1 FROM lineup l JOIN show s ON s.id = l.show_id WHERE l.artist_id = ? "
                       "AND s.status = 'attended' LIMIT 1", (artist_id,)).fetchone() is not None


def _watched(con, artist_id) -> bool:
    return con.execute("SELECT 1 FROM watch WHERE artist_id = ?", (artist_id,)).fetchone() is not None


def suggest_from_lastfm(con) -> list[dict]:
    """The Last.fm user's top artists not watched yet: [{"name", "playcount", "artist_id" (or None when the artist
    is not in Stubs), "seen", "url"}]. [] when LASTFM_API_KEY or LASTFM_USER is unset; 502 when Last.fm fails."""
    if not lastfm.enabled():
        return []
    out = []
    for a in lastfm.top_artists(settings.LASTFM_USER):
        aid = records.resolve_artist(con, a["name"], a["mbid"])
        if aid is not None and _watched(con, aid):
            continue
        out.append({"name": a["name"], "playcount": a["playcount"], "artist_id": aid,
                    "seen": aid is not None and _seen(con, aid), "url": a["url"]})
    return out


def add_watch(con, name, anywhere=False, origin="manual", mbid=None) -> int:
    """Watch the artist this name means (resolve_artist: the MusicBrainz id first, then the name and aliases), made
    when unknown (a band he has never seen). Already watched is fine: nothing changes. Returns the artist id."""
    if origin not in ORIGINS:
        raise ApiError(400, f"origin must be one of: {', '.join(ORIGINS)}.")
    with transaction(con):
        aid = records.resolve_artist(con, name, mbid)
        if aid is None:
            aid = records.get_or_create_artist(con, name)
        con.execute("INSERT INTO watch (artist_id, origin, anywhere) VALUES (?, ?, ?) ON CONFLICT(artist_id) DO NOTHING",
                    (aid, origin, int(bool(anywhere))))
    return aid


def watch_artist(con, artist_id, origin="history", anywhere=False) -> int:
    """Watch an artist already in Stubs (ticked from a suggestion or on the artist page). 404 for no such artist."""
    if origin not in ORIGINS:
        raise ApiError(400, f"origin must be one of: {', '.join(ORIGINS)}.")
    if con.execute("SELECT 1 FROM artist WHERE id = ?", (artist_id,)).fetchone() is None:
        raise ApiError(404, "Artist not found.")
    con.execute("INSERT INTO watch (artist_id, origin, anywhere) VALUES (?, ?, ?) ON CONFLICT(artist_id) DO NOTHING",
                (artist_id, origin, int(bool(anywhere))))
    return artist_id


def update_watch(con, artist_id, anywhere=None, muted=None) -> dict:
    """Change `anywhere` and/or `muted` (each true/false, or None to leave it). 404 when the artist isn't watched."""
    if not _watched(con, artist_id):
        raise ApiError(404, "Not watching that artist.")
    for col, value in (("anywhere", anywhere), ("muted", muted)):
        if value is not None:
            con.execute(f"UPDATE watch SET {col} = ? WHERE artist_id = ?", (int(_bool(value, col)), artist_id))
    return artist_watch(con, artist_id)


def remove_watch(con, artist_id) -> None:
    """Stop watching: the watch row and the artist's sightings go. An artist that was only watched (no show, song or
    alias refers to it) goes too, so a band added by name leaves nothing behind. 404 when not watched."""
    with transaction(con):
        if con.execute("DELETE FROM watch WHERE artist_id = ?", (artist_id,)).rowcount == 0:
            raise ApiError(404, "Not watching that artist.")
        con.execute("DELETE FROM sighting WHERE artist_id = ?", (artist_id,))
        con.execute("DELETE FROM artist WHERE id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM lineup WHERE artist_id = ?) "
                    "AND NOT EXISTS (SELECT 1 FROM song WHERE artist_id = ?) "
                    "AND NOT EXISTS (SELECT 1 FROM song_alias WHERE artist_id = ?) "
                    "AND NOT EXISTS (SELECT 1 FROM artist_alias WHERE artist_id = ?)", (artist_id,) * 5)


_SIGHTING_COLS = ("s.id, s.artist_id, a.name AS artist, a.mbid, s.source, s.date, s.venue, s.city, s.region, "
                  "s.country, s.url, s.on_sale, s.note, s.status")


def _sighting(r) -> dict:
    d = dict(r)
    d.pop("mbid", None)
    return d


def watched(con) -> list[dict]:
    """Every watched artist, by name: [{"artist_id", "name", "origin", "anywhere", "muted", "last_checked", "times",
    "last" (latest attended date, None when never seen), "sightings": [...]}] (sightings not dismissed, soonest
    first)."""
    rows = con.execute(f"SELECT w.artist_id, a.name, w.origin, w.anywhere, w.muted, w.last_checked, "
                       f"{_TIMES} AS times, {_LAST} AS last FROM watch w JOIN artist a ON a.id = w.artist_id "
                       "ORDER BY a.name COLLATE NOCASE, a.id").fetchall()
    by_artist = {}
    for s in con.execute(f"SELECT {_SIGHTING_COLS} FROM sighting s JOIN artist a ON a.id = s.artist_id "
                         "WHERE s.status <> 'dismissed' ORDER BY s.date, s.id"):
        by_artist.setdefault(s["artist_id"], []).append(_sighting(s))
    return [{**dict(r), "anywhere": bool(r["anywhere"]), "muted": bool(r["muted"]),
             "sightings": by_artist.get(r["artist_id"], [])} for r in rows]


def artist_watch(con, artist_id) -> dict:
    """For the artist page: {"artist_id", "seen" (an attended show), "watching", "anywhere", "muted", "origin",
    "last_checked", "sightings"} (sightings not dismissed, each with "have"). 404 for no such artist."""
    if con.execute("SELECT 1 FROM artist WHERE id = ?", (artist_id,)).fetchone() is None:
        raise ApiError(404, "Artist not found.")
    w = con.execute("SELECT * FROM watch WHERE artist_id = ?", (artist_id,)).fetchone()
    rows = con.execute(f"SELECT {_SIGHTING_COLS} FROM sighting s JOIN artist a ON a.id = s.artist_id "
                       "WHERE s.artist_id = ? AND s.status <> 'dismissed' ORDER BY s.date, s.id", (artist_id,))
    return {"artist_id": artist_id, "seen": _seen(con, artist_id), "watching": w is not None, "anywhere": bool(w and w["anywhere"]),
            "muted": bool(w and w["muted"]), "origin": w["origin"] if w else None,
            "last_checked": w["last_checked"] if w else None,
            "sightings": [{**_sighting(r), "have": have(con, r)} for r in rows.fetchall()]}


# ---------------------------------------------------------------- watch areas
def _number(value, what, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not lo <= value <= hi:
        raise ApiError(400, f"{what} must be a number from {lo:g} to {hi:g}.")
    return float(value)


def list_areas(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, name, lat, lon, radius_km FROM watch_area ORDER BY name, id")]


def add_area(con, data) -> dict:
    """{"name", "lat", "lon", "radius_km"} -> the new area. lat -90..90, lon -180..180, radius over 0 up to 20,000 km."""
    name = text(data, "name", LEN["area"], required=True)
    lat = _number(data.get("lat"), "lat", -90, 90)
    lon = _number(data.get("lon"), "lon", -180, 180)
    radius = _number(data.get("radius_km"), "radius_km", 0, MAX_RADIUS)
    if radius <= 0:
        raise ApiError(400, "radius_km must be more than 0.")
    aid = con.execute("INSERT INTO watch_area (name, lat, lon, radius_km) VALUES (?, ?, ?, ?)",
                      (name, lat, lon, radius)).lastrowid
    return dict(con.execute("SELECT id, name, lat, lon, radius_km FROM watch_area WHERE id = ?", (aid,)).fetchone())


def remove_area(con, area_id) -> None:
    if con.execute("DELETE FROM watch_area WHERE id = ?", (area_id,)).rowcount == 0:
        raise ApiError(404, "Watch area not found.")


def distance_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def near(areas, lat, lon) -> bool:
    """Within some area's radius."""
    return any(distance_km(a["lat"], a["lon"], lat, lon) <= a["radius_km"] for a in areas)


def locate(con, venue, city, region, country, cache=None, deadline=None):
    """(lat, lon) of a venue a source gave no coordinates for: a venue in Stubs with coordinates, else Nominatim for
    the venue, else for the city (geocode.lookup, one a second). None when nothing is found or the lookup fails; after
    a failure, Nominatim isn't asked again with this cache (one run)."""
    cache = {} if cache is None else cache
    key = (venue_norm(venue), norm(city), norm(region), norm(country))
    if key in cache:
        return cache[key]
    pos = None
    vid = records.find_venue(con, venue, city)
    if vid is not None:
        v = con.execute("SELECT lat, lon FROM venue WHERE id = ?", (vid,)).fetchone()
        if v["lat"] is not None and v["lon"] is not None:
            pos = (v["lat"], v["lon"])
    for name in ((venue, None) if pos is None and not cache.get(_GEOCODER_DOWN) else ()):
        if deadline is not None and time.monotonic() > deadline:
            raise _OutOfTime()
        try:
            pos = geocode.lookup(name, city, region, country)
        except ApiError:
            cache[_GEOCODER_DOWN] = True
            pos = None
            break
        if pos is not None:
            break
    cache[key] = pos
    return pos


# ---------------------------------------------------------------- sightings
def have(con, s) -> "int | None":
    """The show already in Stubs that this sighting is (imports.match's "have": same venue, same night, the act on
    its lineup), or None."""
    act = {"name": s["artist"]}
    if s["mbid"]:
        act["mbid"] = s["mbid"]
    try:
        p = imports.validate_proposal({"date": s["date"], "status": "upcoming", "billing": s["artist"],
                                       "acts": [act], "venue": s["venue"], "city": s["city"],
                                       "region": s["region"], "country": s["country"]})
    except ApiError:
        return None
    verdict, show_id, _ = imports.match(con, p)
    return show_id if verdict == "have" else None


def sightings(con) -> list[dict]:
    """New sightings (the admin card), soonest first, each with "have": the show id when you have tickets."""
    rows = con.execute(f"SELECT {_SIGHTING_COLS} FROM sighting s JOIN artist a ON a.id = s.artist_id "
                       "WHERE s.status = 'new' ORDER BY s.date, s.id").fetchall()
    return [{**_sighting(r), "have": have(con, r)} for r in rows]


def unnotified(con) -> list[dict]:
    """New sightings of unmuted watched artists not pushed yet, without the ones you have tickets for."""
    rows = con.execute(f"SELECT {_SIGHTING_COLS} FROM sighting s JOIN artist a ON a.id = s.artist_id "
                       "JOIN watch w ON w.artist_id = s.artist_id AND w.muted = 0 "
                       "WHERE s.status = 'new' AND s.notified_at IS NULL ORDER BY s.date, s.id").fetchall()
    return [_sighting(r) for r in rows if have(con, r) is None]


def _new_status(con, sighting_id):
    row = con.execute(f"SELECT {_SIGHTING_COLS} FROM sighting s JOIN artist a ON a.id = s.artist_id "
                      "WHERE s.id = ?", (sighting_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Sighting not found.")
    if row["status"] != "new":
        raise ApiError(409, "That concert was already added or dismissed.")
    return row


def add_sighting(con, sighting_id) -> int:
    """Add as upcoming: a new upcoming show with the act as headliner, the venue (found or made), and the sighting's
    link as a source labelled with the source's name. The sighting becomes "added" (and is never pushed). Returns the
    show id. 409 unless the sighting is new."""
    with transaction(con):
        s = _new_status(con, sighting_id)
        vid = records.get_or_create_venue(con, s["venue"], s["city"], s["region"], s["country"])
        show = records.create_show(con, status="upcoming", date=s["date"], billing=s["artist"], venue_id=vid,
                                   venue_label=s["venue"])
        records.add_lineup_entry(con, show, s["artist_id"], "headliner")
        records.add_source(con, show, LABELS[s["source"]], s["url"])
        con.execute("UPDATE sighting SET status = 'added', notified_at = COALESCE(notified_at, CURRENT_TIMESTAMP) "
                    "WHERE id = ?", (sighting_id,))
    return show


def dismiss(con, sighting_id) -> None:
    """Not interested: the sighting stays (so the source returning it again changes nothing) but is never shown on
    the card or pushed. 409 unless it is new."""
    _new_status(con, sighting_id)
    con.execute("UPDATE sighting SET status = 'dismissed' WHERE id = ?", (sighting_id,))


def _same_concert(con, artist_id, ev):
    """A sighting from any source of this artist on this date at this venue (venue_norm) in this city (norm)."""
    for r in con.execute("SELECT id, venue, city FROM sighting WHERE artist_id = ? AND date = ?",
                         (artist_id, ev["date"])):
        if venue_norm(r["venue"]) == venue_norm(ev["venue"]) and norm(r["city"]) == norm(ev["city"]):
            return r["id"]
    return None


def _upsert(con, artist_id, source, ev, pos, now) -> bool:
    """Store or refresh one event; True when it is new. A known event keeps its status and notified_at."""
    lat, lon = pos if pos else (None, None)
    fields = (ev["date"], ev["venue"], ev["city"], ev.get("region"), ev.get("country"), lat, lon, ev.get("url"),
              ev.get("on_sale"))
    row = con.execute("SELECT id FROM sighting WHERE source = ? AND source_event_id = ?",
                      (source, ev["source_event_id"])).fetchone()
    if row is not None:
        con.execute("UPDATE sighting SET date = ?, venue = ?, city = ?, region = ?, country = ?, lat = ?, lon = ?, "
                    "url = ?, on_sale = ?, last_seen = ? WHERE id = ?", (*fields, now, row["id"]))
        return False
    con.execute("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city, region, country, lat, "
                "lon, url, on_sale, note, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (artist_id, source, ev["source_event_id"], *fields, ev.get("note"), now, now))
    return True


def _where(con, ev, cache, deadline=None):
    if ev.get("lat") is not None and ev.get("lon") is not None:
        return ev["lat"], ev["lon"]
    known = con.execute("SELECT lat, lon FROM sighting WHERE source = ? AND source_event_id = ? AND lat IS NOT NULL",
                        (ev.get("source"), ev["source_event_id"])).fetchone()
    if known is not None:
        return known["lat"], known["lon"]
    return locate(con, ev["venue"], ev["city"], ev.get("region"), ev.get("country"), cache, deadline)


def _check_artist(con, artist, source, found, areas, now, cache, deadline) -> int:
    """Store what a source returned for one artist; delete this artist's sightings from it that it no longer returns
    (or that are no longer near). Returns how many were new."""
    kept, new = [], 0
    for ev in found:
        pos = _where(con, {**ev, "source": source}, cache, deadline)
        if not artist["anywhere"] and (pos is None or not near(areas, *pos)):
            continue
        kept.append(ev["source_event_id"])
        new += _upsert(con, artist["artist_id"], source, ev, pos, now)
    marks = ",".join("?" * len(kept))
    con.execute(f"DELETE FROM sighting WHERE artist_id = ? AND source = ? AND source_event_id NOT IN ({marks})",
                (artist["artist_id"], source, *kept))
    return new


def _event_id(artist_id, ev) -> str:
    key = "|".join((str(artist_id), ev["date"], venue_norm(ev["venue"]), norm(ev["city"])))
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _email_event(e, i, today):
    where = f"Event {i}: "
    if not isinstance(e, dict):
        raise ApiError(400, f"{where}each event must be a JSON object.")
    try:
        out = {"artist": text(e, "artist", LEN["name"], required=True),
               "date": text(e, "date", 10, required=True),
               "venue": text(e, "venue", LEN["venue"], required=True),
               "city": text(e, "city", LEN["city"], required=True),
               "region": text(e, "region", LEN["region"]), "country": text(e, "country", 2),
               "url": text(e, "url", LEN["url"]), "note": text(e, "note", LEN["note"])}
    except ApiError as err:
        raise ApiError(400, where + err.message) from None
    try:
        day, precision, _ = parse_date(out["date"])
    except ValueError:
        precision = None
    if precision != "day" or day < today:
        raise ApiError(400, f"{where}date must be a day (YYYY-MM-DD), today or later.")
    if out["country"] is not None:
        out["country"] = out["country"].upper()
        if not (len(out["country"]) == 2 and out["country"].isalpha() and out["country"].isascii()):
            raise ApiError(400, f"{where}country must be a two-letter code such as US or CA.")
    if out["url"] is not None and not out["url"].startswith(("https://", "http://")):
        raise ApiError(400, f"{where}url must start with https:// or http://.")
    return out


def file_email(con, data, today, now) -> dict:
    """{"source": "email", "events": [{"artist", "date", "venue", "city", "region"?, "country"?, "url"?, "note"?}]}
    -> {"new", "seen", "skipped": [{"index", "artist", "reason"}]}. Everything is checked first (400 naming the
    event); then each concert of a watched artist that is near a watch area (or whose artist is `anywhere`) is stored
    like a source's, deduplicated against every source by artist, date, venue and city."""
    if data.get("source") != "email":
        raise ApiError(400, "source must be \"email\".")
    events = data.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ApiError(400, f"events must be a list of 1 to {MAX_EVENTS} concerts.")
    checked = [_email_event(e, i, today) for i, e in enumerate(events, start=1)]
    areas, cache = list_areas(con), {}
    result = {"new": 0, "seen": 0, "skipped": []}
    for i, ev in enumerate(checked, start=1):
        aid = records.resolve_artist(con, ev["artist"])
        w = con.execute("SELECT anywhere FROM watch WHERE artist_id = ?", (aid,)).fetchone() if aid else None
        if w is None:
            result["skipped"].append({"index": i, "artist": ev["artist"],
                                      "reason": "Not watched: add the artist on the watch page first."})
            continue
        same = _same_concert(con, aid, ev)
        if same is not None:
            con.execute("UPDATE sighting SET last_seen = ? WHERE id = ?", (now, same))
            result["seen"] += 1
            continue
        pos = locate(con, ev["venue"], ev["city"], ev["region"], ev["country"], cache)
        if not w["anywhere"] and (pos is None or not near(areas, *pos)):
            result["skipped"].append({"index": i, "artist": ev["artist"], "reason": (
                "Couldn't find where that is." if pos is None else "Not near a watch area.")})
            continue
        ev["source_event_id"] = _event_id(aid, ev)
        result["new" if _upsert(con, aid, "email", ev, pos, now) else "seen"] += 1
    return result


# ---------------------------------------------------------------- checking
def sources() -> dict:
    """Which sources are on, for the watch page. The notification URL itself is never returned."""
    return {"ticketmaster": ticketmaster.enabled(), "lastfm": lastfm.enabled(), "email": True,
            "notify": settings.WATCH_NOTIFY_FORMAT if notify.enabled() else None}


def _lock(con):
    with transaction(con):
        row = con.execute("SELECT v FROM meta WHERE k = 'watch_run'").fetchone()
        if row is not None and time.time() - float(row["v"]) < LOCK_STALE:
            raise ApiError(409, "A check is already running.")
        con.execute("INSERT INTO meta (k, v) VALUES ('watch_run', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                    (repr(time.time()),))


def _unlock(con):
    con.execute("DELETE FROM meta WHERE k = 'watch_run'")


def expire(con, today) -> int:
    """Delete sightings whose day has passed (a sighting goes the day after its date). Returns how many."""
    return con.execute("DELETE FROM sighting WHERE date < ?", (today,)).rowcount


def run(con, now, limit=LIMIT) -> dict:
    """One check: past sightings go; the `limit` unmuted watched artists checked longest ago (never checked first)
    are asked of every source that is on, within BUDGET seconds; then the new sightings are pushed. `now` is
    "YYYY-MM-DD HH:MM:SS" (its day is today). A source that fails is skipped for the rest of the run, and an artist
    it failed on keeps its old last_checked, so it is asked again first next time. 409 while another run is going.
    -> {"checked", "new", "notified"}."""
    today = now[:10]
    _lock(con)
    try:
        expire(con, today)
        on = [(name, mod) for name, mod in SOURCES.items() if mod.enabled()]
        checked = new = 0
        if on:
            areas, cache = list_areas(con), {}
            deadline = time.monotonic() + BUDGET
            artists = con.execute(
                "SELECT w.artist_id, a.name, a.mbid, w.anywhere FROM watch w JOIN artist a ON a.id = w.artist_id "
                "WHERE w.muted = 0 ORDER BY w.last_checked IS NOT NULL, w.last_checked, w.artist_id LIMIT ?",
                (limit,)).fetchall()
            down = set()
            for artist in artists:
                if time.monotonic() > deadline or len(down) == len(on):
                    break
                asked = False
                try:
                    for name, mod in on:
                        if name in down:
                            continue
                        try:
                            found = mod.events(artist["name"], artist["mbid"], today)
                        except ApiError:
                            down.add(name)
                            continue
                        with transaction(con):
                            new += _check_artist(con, artist, name, found, areas, now, cache, deadline)
                        asked = True
                except _OutOfTime:
                    break
                if asked and not down:
                    con.execute("UPDATE watch SET last_checked = ? WHERE artist_id = ?", (now, artist["artist_id"]))
                    checked += 1
        notified = notify.notify_new(con, unnotified(con))
    finally:
        _unlock(con)
    return {"checked": checked, "new": new, "notified": notified}
