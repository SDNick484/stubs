"""Post-v1 #44: song counts, an artist's song catalog, song pages and song merging, on #43's tables. Route-free: every
function takes a sqlite3.Connection; app.py serves the reads at /api/ and /admin/api/ and the writes under /admin/api/.

A play is one setlist_entry on an attended show (a song played twice in a night is 2 plays, 1 show); openers and
festival performers count like headliners. Every query filters on status = 'attended', though upcoming shows can't
have setlists. `in_catalog` means "a song this artist has", heard or not. A song is visible (listed, with a page)
when it has attended plays or is in the catalog; the public also needs the artist to have an attended show, so
nothing of an upcoming show leaks. Aliases are stored as norms (song_alias has no display column), so that is what
song_detail returns. Songs are never deleted while played: remove_from_catalog and merge_songs end with
records.drop_unused_songs, so an unplayed, unaliased song that leaves the catalog goes."""
import re

import records
import setlists
from browse import SHOW_SELECT, show_row
from common import ApiError, norm, text
from db import transaction

LIMIT = 20
MAX_TITLE = 200
MAX_TITLES = 1000
MAX_TEXT = 100_000
_ALBUM = re.compile(r"album\s*:", re.I)

_PLAYS = ("FROM setlist_entry e JOIN lineup l ON l.id = e.lineup_id JOIN show s ON s.id = l.show_id "
          "WHERE s.status = 'attended'")


# ---------------------------------------------------------------- reads
def artist_visible(con, artist_id, *, editor) -> bool:
    """The artist exists and, for the public, is on an attended show (the rule browse.artist_detail applies)."""
    if con.execute("SELECT 1 FROM artist WHERE id = ?", (artist_id,)).fetchone() is None:
        return False
    return editor or con.execute("SELECT 1 FROM lineup l JOIN show s ON s.id = l.show_id "
                                 "WHERE l.artist_id = ? AND s.status = 'attended'", (artist_id,)).fetchone() is not None


def _stats(con, where, args) -> dict:
    """song_id -> {"plays", "shows", "first", "last"} over attended plays, for the songs `where` picks."""
    out = {}
    rows = con.execute(f"SELECT e.song_id, s.id AS show_id, s.date, s.sort_date {_PLAYS} AND {where} "
                       "ORDER BY s.sort_date, s.id, e.id", args)
    for r in rows:
        d = out.setdefault(r["song_id"], {"plays": 0, "shows": set(), "first": r["date"], "last": r["date"]})
        d["plays"] += 1
        d["shows"].add(r["show_id"])
        d["last"] = r["date"]
    return {k: {**v, "shows": len(v["shows"])} for k, v in out.items()}


def artist_songs(con, artist_id) -> list[dict]:
    """[{"id", "title", "plays", "shows", "first", "last", "in_catalog"}] for the artist's visible songs (heard, or in
    the catalog), most played first, then by title. first and last are the dates of the oldest and newest night the
    song was played (None for a song never heard)."""
    played = _stats(con, "l.artist_id = ?", (artist_id,))
    out = []
    for g in con.execute("SELECT id, title, in_catalog FROM song WHERE artist_id = ?", (artist_id,)):
        p = played.get(g["id"], {"plays": 0, "shows": 0, "first": None, "last": None})
        if p["plays"] or g["in_catalog"]:
            out.append({"id": g["id"], "title": g["title"], **p, "in_catalog": bool(g["in_catalog"])})
    out.sort(key=lambda r: (-r["plays"], r["title"].casefold(), r["id"]))
    return out


def never_heard(con, artist_id) -> list[dict]:
    """The catalog songs with no play, in artist_songs' shape, by title."""
    return sorted((r for r in artist_songs(con, artist_id) if r["in_catalog"] and not r["plays"]),
                  key=lambda r: (r["title"].casefold(), r["id"]))


def song_detail(con, song_id, *, editor=False) -> "dict | None":
    """{"id", "title", "artist": {"id", "name"}, "plays", "first", "last", "in_catalog", "aliases", "shows"}; shows
    has one item per play, newest night first: the show row (browse.show_row) plus "set_name", "encore" and "note"
    (the setlist entry's own note, which is public since #43). None when there is no such song, it has no attended
    play and is not in the catalog, or (public) its artist has no attended show."""
    g = con.execute("SELECT g.id, g.title, g.in_catalog, g.artist_id, a.name FROM song g "
                    "JOIN artist a ON a.id = g.artist_id WHERE g.id = ?", (song_id,)).fetchone()
    if g is None or not artist_visible(con, g["artist_id"], editor=editor):
        return None
    plays = con.execute(f"SELECT s.id AS show_id, e.set_name, e.encore, e.note {_PLAYS} AND e.song_id = ? "
                        "ORDER BY s.sort_date DESC, s.id DESC, e.lineup_id, e.position", (song_id,)).fetchall()
    if not plays and not g["in_catalog"]:
        return None
    rows, shows = {}, []
    for p in plays:
        if p["show_id"] not in rows:
            rows[p["show_id"]] = show_row(con, con.execute(f"{SHOW_SELECT} WHERE s.id = ?", (p["show_id"],)).fetchone())
        shows.append({**rows[p["show_id"]], "set_name": p["set_name"], "encore": bool(p["encore"]), "note": p["note"]})
    aliases = [r["norm"] for r in con.execute("SELECT norm FROM song_alias WHERE song_id = ? ORDER BY norm", (song_id,))]
    return {"id": g["id"], "title": g["title"], "artist": {"id": g["artist_id"], "name": g["name"]},
            "plays": len(shows), "first": shows[-1]["date"] if shows else None,
            "last": shows[0]["date"] if shows else None, "in_catalog": bool(g["in_catalog"]),
            "aliases": aliases, "shows": shows}


def search_songs(con, artist_id, q) -> list[dict]:
    """Up to 20 of the artist's songs whose title or an alias contains q (by norm): those starting with it first,
    then most played, then by title. [{"id", "title", "plays"}]. Any song of the artist, for the merge picker."""
    n = norm(q)
    played = _stats(con, "l.artist_id = ?", (artist_id,))
    found = []
    for g in con.execute("SELECT id, title, norm FROM song WHERE artist_id = ?", (artist_id,)):
        names = [g["norm"]] + [r["norm"] for r in con.execute("SELECT norm FROM song_alias WHERE song_id = ?", (g["id"],))]
        if n and not any(n in x for x in names):
            continue
        found.append((not (g["norm"].startswith(n) if n else True), -played.get(g["id"], {"plays": 0})["plays"],
                      g["title"].casefold(), g["id"], g["title"]))
    found.sort()
    return [{"id": sid, "title": title, "plays": played.get(sid, {"plays": 0})["plays"]}
            for _, _, _, sid, title in found[:LIMIT]]


# ---------------------------------------------------------------- the catalog
def parse_catalog(raw) -> list[str]:
    """Pasted text -> titles in order, one per line: numbering and bullets go (as in a setlist paste), blank lines, an
    "Album: ..." line and heading lines (setlists._heading: "Set 2", "Side B:") are skipped, and quotes and a trailing
    (note) go, so a title resolves like the same line in a setlist. Repeats (by norm) collapse. A line over 200
    characters is 400 naming it; no titles at all is 400."""
    out, seen = [], set()
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or _ALBUM.match(line) or setlists._heading(line, 0) is not None:
            continue
        if len(line) > MAX_TITLE:
            raise ApiError(400, f"Line {number} is longer than {MAX_TITLE} characters.")
        title = setlists._song(setlists._strip_number(line, len(out), len(out)).strip())[0]
        if title and norm(title) not in seen:
            seen.add(norm(title))
            out.append(title)
    if not out:
        raise ApiError(400, "No songs found in that text.")
    if len(out) > MAX_TITLES:
        raise ApiError(400, f"A catalog paste can have at most {MAX_TITLES} songs.")
    return out


def catalog_text(data) -> list[str]:
    return parse_catalog(text(data, "text", MAX_TEXT, required=True))


def set_catalog(con, artist_id, titles) -> dict:
    """Each title resolves to the artist's song (by title or alias), which joins the catalog, or becomes a new song
    already in it. {"added": songs that joined or were created, "already": songs that were in it}. Pasting the same
    list again adds nothing. 404 for no such artist."""
    with transaction(con):
        if con.execute("SELECT 1 FROM artist WHERE id = ?", (artist_id,)).fetchone() is None:
            raise ApiError(404, "Not found")
        added = already = 0
        for title in titles:
            sid = records.get_or_create_song(con, artist_id, title)
            if con.execute("UPDATE song SET in_catalog = 1, updated_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND in_catalog = 0", (sid,)).rowcount:
                added += 1
            else:
                already += 1
    return {"added": added, "already": already}


def remove_from_catalog(con, song_id) -> dict:
    """Clear the flag. A song with plays stays (as does one with an alias); one nothing plays and nothing names goes
    (records.drop_unused_songs). {"id", "in_catalog": False, "deleted"}. 404 for no such song."""
    with transaction(con):
        row = con.execute("SELECT artist_id FROM song WHERE id = ?", (song_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Not found")
        con.execute("UPDATE song SET in_catalog = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (song_id,))
        records.drop_unused_songs(con, [row["artist_id"]])
        gone = con.execute("SELECT 1 FROM song WHERE id = ?", (song_id,)).fetchone() is None
    return {"id": song_id, "in_catalog": False, "deleted": gone}


# ---------------------------------------------------------------- merging
def merge_songs(con, keep_id, drop_id) -> dict:
    """Fold song drop_id into keep_id (the same artist, else 400): its setlist entries move, its aliases move, its title
    becomes an alias of the kept song (so a later setlist paste of it resolves there), the kept song joins the catalog
    if either was in it, and the dropped song goes. Two entries of one setlist stay two entries. Shows with a moved
    entry get a new version, so a setlist form opened before the merge is refused as stale. {"keep", "moved"}: the
    entries moved. 400 for the same song twice, 404 for an unknown one."""
    if keep_id == drop_id:
        raise ApiError(400, "Pick two different songs.")
    with transaction(con):
        keep = con.execute("SELECT id, artist_id, norm, in_catalog FROM song WHERE id = ?", (keep_id,)).fetchone()
        drop = con.execute("SELECT id, artist_id, norm, in_catalog FROM song WHERE id = ?", (drop_id,)).fetchone()
        if keep is None or drop is None:
            raise ApiError(404, "Song not found.")
        if keep["artist_id"] != drop["artist_id"]:
            raise ApiError(400, "Those songs belong to different acts.")
        con.execute("UPDATE show SET version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id IN "
                    "(SELECT l.show_id FROM setlist_entry e JOIN lineup l ON l.id = e.lineup_id WHERE e.song_id = ?)",
                    (drop_id,))
        moved = con.execute("UPDATE setlist_entry SET song_id = ? WHERE song_id = ?", (keep_id, drop_id)).rowcount
        con.execute("UPDATE song_alias SET song_id = ? WHERE song_id = ?", (keep_id, drop_id))
        con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(artist_id, norm) DO UPDATE SET song_id = excluded.song_id",
                    (keep["artist_id"], drop["norm"], keep_id))
        con.execute("DELETE FROM song_alias WHERE artist_id = ? AND norm = ?", (keep["artist_id"], keep["norm"]))
        con.execute("DELETE FROM song WHERE id = ?", (drop_id,))
        con.execute("UPDATE song SET in_catalog = MAX(in_catalog, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (drop["in_catalog"], keep_id))
    return {"keep": keep_id, "moved": moved}
