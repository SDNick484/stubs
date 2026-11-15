"""Stored setlists (post-v1 #43; spec sections 5 and 6, schema v3): the songs an act played on a night the owner was
there, pasted from a band or fan-club site or typed, kept per lineup entry.

parse_setlist turns pasted text into entries: one song per line, with its set, encore, note and segue."""
import re

from common import ApiError

MAX_LINE = 200  # characters in one pasted line; longer is 400 with its line number
MAX_SONGS = 300

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
