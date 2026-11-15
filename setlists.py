"""Stored setlists (post-v1 #43; spec sections 5 and 6, schema v3): the songs an act played on a night the owner was
there, pasted from a band or fan-club site or typed, kept per lineup entry. Route-free: every function that needs the
database takes a sqlite3.Connection; app.py serves them under /admin/api/lineup/<id>/setlist, and browse.show_detail
puts each act's setlist on the show.

parse_setlist turns pasted text into entries; preview resolves each title to the act's existing song (or None: new)
and stores nothing; save replaces an act's setlist in one transaction, creating songs as needed; delete removes it.
Saves and deletes carry the show's version like the edit form (409 STALE) and bump it. A song belongs to the act
that played it: titles resolve through records.resolve_song within that artist. Nothing here touches setlist.fm."""
import re

import editing
import records
from common import ApiError, text
from db import transaction

MAX_LINE = 200  # characters in one pasted line; longer is 400 with its line number
MAX_SONGS = 300
MAX_TEXT = 100_000  # a whole paste
MAX = {"title": 200, "set_name": 200, "note": 200, "source_label": 200, "source_url": 2000}
UPCOMING = "Add a setlist after the show."
ENTRY_FIELDS = frozenset(("title", "set_name", "encore", "note", "segue", "song_id"))
SAVE_FIELDS = frozenset(("entries", "source_label", "source_url", "version"))

# ---------------------------------------------------------------- the paste parser
_ROMAN = r"[ivxlc]+"
_WORDS = r"one|two|three|four|five|six"
_SET = re.compile(rf"set\s*(?:\d{{1,2}}|{_ROMAN}|{_WORDS})\s*:?", re.I)              # Set 1, Set II, Set two:
_NAMED_SET = re.compile(r"[^\W\d_]+(?:[ -][^\W\d_]+)?\s+set\s*:?", re.I)            # Acoustic Set, Main set:
_ENCORE = re.compile(r"(?:encore|enc\.)(?:\s*#?\s*(\d{1,2}))?\s*:?", re.I)        # Encore, Encore 2, Encore:
_E = re.compile(r"e(\d{0,2})\s*:", re.I)                                           # E:, E2:
_NUMBERED = re.compile(r"#?\d{1,3}\s*[.):]\s+|#\d{1,3}\s+|\d{1,3}\s+[-–]\s+")  # 1. 1) 1: #1 "1 - "
_BARE_NUMBER = re.compile(r"(\d{1,3})\s+(?=\S)")                                    # 01 Song, 7 Song
_BULLET = re.compile(r"[-*•·]\s+")
_TRAILING_SEGUE = re.compile(r"\s*(?:->|→|>)$")
_SEGUE = re.compile(r"\s+(?:->|>|→)\s+|\s*(?:->|→)\s*")
_NOTE = re.compile(r"\s*(?:\(([^()]*)\)|\[([^\[\]]*)\])$")
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("„", "“"), ("'", "'"), ("‘", "’"))


def _heading(line, encores):
    """(set_name, encore) when the line is only a heading, else None. `encores` is how many encores came before, so
    a second bare "Encore" becomes "Encore 2" and stays its own set."""
    m = _ENCORE.fullmatch(line) or _E.fullmatch(line)
    if m:
        if m.group(1):
            return f"Encore {int(m.group(1))}", True
        return (f"Encore {encores + 1}" if encores else None), True
    if _SET.fullmatch(line) or _NAMED_SET.fullmatch(line):
        return line.rstrip(":").strip(), False
    if line.endswith(":") and len(line) > 1 and not _NUMBERED.match(line):
        return line[:-1].strip(), False
    return None


def _unquote(title):
    for a, b in _QUOTE_PAIRS:
        if len(title) > 2 and title.startswith(a) and title.endswith(b):
            return title[1:-1].strip()
    return title


def _song(part):
    """(title, note) of one song as written: trailing (...) and [...] become the note (joined with "; ", in order),
    unless nothing would be left of the title; quotes around the title go."""
    title, notes = " ".join(part.split()), []
    while True:
        m = _NOTE.search(title)
        if not m or not title[:m.start()].strip():
            break
        note = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        if note:
            notes.insert(0, " ".join(note.split()))
        title = title[:m.start()].rstrip()
    return _unquote(title), ("; ".join(notes) or None)


def _strip_number(line, in_set, total):
    """The line without its leading numbering (1. 1) 1: #1 01) or bullet. A bare number with no punctuation ("7 Song")
    is numbering only when it has a leading zero or is the next song's number, so "99 Lanterns" keeps its 99."""
    m = _NUMBERED.match(line) or _BULLET.match(line)
    if m:
        return line[m.end():]
    m = _BARE_NUMBER.match(line)
    if m and (m.group(1).startswith("0") or int(m.group(1)) in (in_set + 1, total + 1)):
        return line[m.end():]
    return line


def parse_setlist(raw) -> list[dict]:
    """Pasted text -> [{"title", "set_name", "encore", "note", "segue"}], in order. One song per line; blank lines are
    ignored; leading numbering and bullets go. A line that is only a heading starts a new set: "Set 1", "Set II",
    "Acoustic Set" or anything ending in ":" names it; "Encore", "Encore 2", "E:", "Encore:" start an encore (every
    song after it has encore true until the next heading). "A > B" and "A -> B" are two songs, the first with segue
    true; a trailing ">" sets segue on its song. A trailing (...) or [...] is the note; quotes around a title go.
    A line over MAX_LINE characters, or a song past MAX_SONGS, is ApiError 400 naming the line."""
    out = []
    set_name, encore, encores, in_set = None, False, 0, 0
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_LINE:
            raise ApiError(400, f"Line {number} is longer than {MAX_LINE} characters.")
        heading = _heading(line, encores)
        if heading is not None:
            set_name, encore = heading
            encores += encore
            in_set = 0
            continue
        line = _strip_number(line, in_set, len(out)).strip()
        trailing = bool(_TRAILING_SEGUE.search(line))
        if trailing:
            line = _TRAILING_SEGUE.sub("", line)
        parts = [p for p in _SEGUE.split(line) if p.strip()]
        for i, part in enumerate(parts):
            title, note = _song(part)
            if not title:
                continue
            if len(out) == MAX_SONGS:
                raise ApiError(400, f"Line {number}: a setlist can have at most {MAX_SONGS} songs.")
            out.append({"title": title, "set_name": set_name, "encore": encore, "note": note,
                        "segue": i < len(parts) - 1 or trailing})
            in_set += 1
    return out


# ---------------------------------------------------------------- reads
def _group(rows, editor_source=None) -> dict:
    """Entries in position order -> {"sets": [{"name", "encore", "songs": [...]}]}: a new set starts wherever
    set_name or encore changes."""
    sets = []
    for r in rows:
        key = (r["set_name"], bool(r["encore"]))
        if not sets or (sets[-1]["name"], sets[-1]["encore"]) != key:
            sets.append({"name": key[0], "encore": key[1], "songs": []})
        sets[-1]["songs"].append({"song_id": r["song_id"], "title": r["title"], "note": r["note"],
                                  "segue": bool(r["segue"])})
    out = {"sets": sets}
    if editor_source is not None:
        out.update(source_label=editor_source["source_label"], source_url=editor_source["source_url"])
    return out


_ENTRIES = ("SELECT e.lineup_id, e.set_name, e.encore, e.song_id, s.title, e.note, e.segue FROM setlist_entry e "
            "JOIN song s ON s.id = e.song_id ")


def for_show(con, show_id, *, editor) -> dict:
    """{lineup_id: setlist} for every act on the show that has one. A setlist is {"sets": [{"name", "encore",
    "songs": [{"song_id", "title", "note", "segue"}]}]}; editors also get "source_label" and "source_url" (sources
    are editor-only, spec 6). Callers decide whether this reader may see the show at all."""
    heads = con.execute("SELECT t.lineup_id, t.source_label, t.source_url FROM setlist t "
                        "JOIN lineup l ON l.id = t.lineup_id WHERE l.show_id = ?", (show_id,)).fetchall()
    rows = {}
    for r in con.execute(_ENTRIES + "JOIN lineup l ON l.id = e.lineup_id WHERE l.show_id = ? "
                         "ORDER BY e.lineup_id, e.position", (show_id,)):
        rows.setdefault(r["lineup_id"], []).append(r)
    return {h["lineup_id"]: _group(rows.get(h["lineup_id"], []), h if editor else None) for h in heads}


def setlist(con, lineup_id, *, editor) -> "dict | None":
    head = con.execute("SELECT source_label, source_url FROM setlist WHERE lineup_id = ?", (lineup_id,)).fetchone()
    if head is None:
        return None
    rows = con.execute(_ENTRIES + "WHERE e.lineup_id = ? ORDER BY e.position", (lineup_id,)).fetchall()
    return _group(rows, head if editor else None)


# ---------------------------------------------------------------- writes
def _entry(con, lineup_id):
    """The lineup entry with its show's id and status; 404 when there is none."""
    row = con.execute("SELECT l.id, l.show_id, l.artist_id, s.status FROM lineup l JOIN show s ON s.id = l.show_id "
                      "WHERE l.id = ?", (lineup_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Not found")
    return row


def _not_upcoming(entry):
    if entry["status"] == "upcoming":
        raise ApiError(409, UPCOMING)


def preview(con, lineup_id, data) -> dict:
    """{"text"} -> {"entries": parse_setlist's entries, each with "song_id": the act's existing song, or None for a
    song new to it}. Stores nothing."""
    entry = _entry(con, lineup_id)
    _not_upcoming(entry)
    raw = text(data, "text", MAX_TEXT, required=True)
    entries = parse_setlist(raw)
    for e in entries:
        e["song_id"] = records.resolve_song(con, entry["artist_id"], e["title"])
    return {"entries": entries}


def _flag(value, what):
    if value in (None, False, 0):
        return 0
    if value in (True, 1) and not isinstance(value, float):
        return 1
    raise ApiError(400, f"{what} must be true or false.")


def _entries(con, artist_id, raw) -> list[tuple]:
    """The request's entries, checked: [(title, set_name, encore, note, segue, song_id or None)]."""
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "entries must be a list of songs, at least one (delete the setlist to remove it).")
    if len(raw) > MAX_SONGS:
        raise ApiError(400, f"A setlist can have at most {MAX_SONGS} songs.")
    out = []
    for e in raw:
        if not isinstance(e, dict):
            raise ApiError(400, "Each song must be an object.")
        editing._unknown(e, ENTRY_FIELDS)
        title = text(e, "title", MAX["title"], required=True)
        song_id = e.get("song_id")
        if song_id is not None:
            editing._id(song_id, "song_id")
            if not con.execute("SELECT 1 FROM song WHERE id = ? AND artist_id = ?", (song_id, artist_id)).fetchone():
                raise ApiError(400, "That song is not this act's song.")
        out.append((" ".join(title.split()), text(e, "set_name", MAX["set_name"]), _flag(e.get("encore"), "encore"),
                    text(e, "note", MAX["note"]), _flag(e.get("segue"), "segue"), song_id))
    return out


def save(con, lineup_id, data) -> dict:
    """{"entries": [{"title", "set_name"?, "encore"?, "note"?, "segue"?, "song_id"?}], "source_label", "source_url"?,
    "version"} -> {"version", "setlist"} (the editor's view). Replaces the act's setlist in one transaction: each
    title resolves to the act's song, or a new one is made (a song_id, when given, must be one of the act's songs
    and wins). Songs nothing plays any more go. 404 for no such lineup entry; stale -> 409 STALE; an upcoming show
    -> 409; bad input -> 400; nothing is written on any of them."""
    editing._unknown(data, SAVE_FIELDS)
    with transaction(con):
        entry = _entry(con, lineup_id)
        editing._check(con, entry["show_id"], data.get("version"))
        _not_upcoming(entry)
        label = text(data, "source_label", MAX["source_label"], required=True)
        url = text(data, "source_url", MAX["source_url"])
        if url and not editing._URL.fullmatch(url):
            raise ApiError(400, "source_url must start with http:// or https://.")
        entries = _entries(con, entry["artist_id"], data.get("entries"))
        con.execute("INSERT INTO setlist (lineup_id, source_label, source_url) VALUES (?, ?, ?) "
                    "ON CONFLICT(lineup_id) DO UPDATE SET source_label = excluded.source_label, "
                    "source_url = excluded.source_url, updated_at = CURRENT_TIMESTAMP", (lineup_id, label, url))
        con.execute("DELETE FROM setlist_entry WHERE lineup_id = ?", (lineup_id,))
        for position, (title, set_name, encore, note, segue, song_id) in enumerate(entries, start=1):
            if song_id is None:
                song_id = records.get_or_create_song(con, entry["artist_id"], title)
            con.execute("INSERT INTO setlist_entry (lineup_id, position, set_name, encore, song_id, note, segue) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (lineup_id, position, set_name, encore, song_id, note, segue))
        records.drop_unused_songs(con, [entry["artist_id"]])
        version = editing._bump(con, entry["show_id"])
    return {"version": version, "setlist": setlist(con, lineup_id, editor=True)}


def delete(con, lineup_id, data) -> dict:
    """{"version"} -> {"version"}: the act's setlist is gone, with the songs only it played. 404 when the entry has no
    setlist; stale -> 409 STALE."""
    with transaction(con):
        entry = _entry(con, lineup_id)
        editing._check(con, entry["show_id"], data.get("version"))
        if con.execute("DELETE FROM setlist WHERE lineup_id = ?", (lineup_id,)).rowcount == 0:
            raise ApiError(404, "Not found")
        records.drop_unused_songs(con, [entry["artist_id"]])
        return {"version": editing._bump(con, entry["show_id"])}

