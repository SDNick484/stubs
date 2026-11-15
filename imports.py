"""Import batches (spec 7.3 steps 2-4): validate proposals, match them against the shows we have, store them as a
pending batch for review, let the editor tick and edit rows, then apply the ticked rows in one transaction.

CSV uploads (csvimport.parse_csv) and API batches (POST /admin/api/imports) share this one pipeline. Every function
takes a sqlite3.Connection and imports no route code. Bad input raises common.ApiError.

Matching, for a proposal P against an existing show (or an earlier row of the same batch) C:
- `have`:  same venue, the same night (both full dates at most one day apart, or the very same date string such as
           "2027" and "2027"), and the billing or an artist in common.
- `maybe`: an artist (or the billing) in common, dates within a day of each other's periods, and the same venue (a
           year or month date that only overlaps) or the same city at a different or unknown venue.
- `new`:   nothing like it; `skip`: the proposal says why (skip_reason) and is never applied.
Existing shows win over earlier rows, `have` over `maybe`, then the nearest date. Apply re-checks every ticked row
against the database inside its transaction, so a second batch of the same file adds nothing twice."""
import json
import re
from datetime import date as Date, timedelta

import musicbrainz
import records
from common import ApiError, norm, parse_date, period_end, text, venue_norm
from db import transaction

VERDICTS = ("new", "have", "maybe", "skip")
ORIGINS = ("csv", "api")
MAX_CSV_BYTES = 5 * 1024 * 1024   # an upload over this is 413
MAX_ROWS = 5000                    # shows in one batch
MAX_LIST = 200                     # acts, sources or flags on one proposal
LEN = {"billing": 300, "name": 200, "venue": 200, "city": 100, "region": 20, "notes": 5000, "tour": 200, "run": 200,
       "label": 200, "url": 2000, "note": 500, "flag": 100, "skip_reason": 300}
DIFF_FIELDS = ("date", "status", "billing", "venue", "acts", "run", "tour", "notes", "confidence", "sources")
_COUNTRY = re.compile(r"[A-Z]{2}")
_URL = re.compile(r"https?://\S+", re.I)
_DAY = timedelta(days=1)


# ---------------------------------------------------------------- proposals
def _list(p, key):
    value = p.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(400, f"{key} must be a list.")
    if len(value) > MAX_LIST:
        raise ApiError(400, f"{key} can hold at most {MAX_LIST} entries.")
    return value


def _choice(p, key, allowed, default):
    value = p.get(key)
    if value is None:
        return default
    if value not in allowed:
        raise ApiError(400, f"{key} must be one of: {', '.join(allowed)}.")
    return value


def _sources(items, where):
    out = []
    for s in items:
        if isinstance(s, str):
            s = {"label": s}
        if not isinstance(s, dict):
            raise ApiError(400, f"{where}sources must be labels or {{label, url}} objects.")
        label = text(s, "label", LEN["label"], required=True)
        url = text(s, "url", LEN["url"])
        if url is not None and not _URL.fullmatch(url):
            raise ApiError(400, f"{where}source url must be an http or https link.")
        out.append({"label": label, "url": url})
    return out


def _acts(items):
    out, seen = [], set()
    for a in items:
        if not isinstance(a, dict):
            raise ApiError(400, "acts must be {name, role} objects.")
        name = text(a, "name", LEN["name"], required=True)
        if norm(name) in seen:
            raise ApiError(400, f"{name} is listed twice in acts.")
        seen.add(norm(name))
        raw_mbid = a.get("mbid")
        mbid = None
        if raw_mbid not in (None, ""):
            mbid = musicbrainz.valid_mbid(raw_mbid)
            if mbid is None:
                raise ApiError(400, f"{name}: mbid must be a MusicBrainz ID (a UUID).")
        act = {"name": name, "role": _choice(a, "role", records.ROLES, "headliner"),
               "confidence": _choice(a, "confidence", records.CONFIDENCES, "confirmed"),
               "confidence_note": text(a, "confidence_note", LEN["note"]),
               "sources": _sources(_list(a, "sources"), f"{name}: ")}
        if mbid:  # optional: the key is there only when the act carries one
            act["mbid"] = mbid
        out.append(act)
    return out


def validate_proposal(p) -> dict:
    """The Proposal (see the plan's Contracts) with every key present and defaults filled in: no acts means the
    billing as headliner; an act's role defaults to headliner and its confidence to confirmed; a source may be plain
    text (its label). Text is trimmed and never cut (over its limit is 400). A proposal with skip_reason only needs
    well-typed fields: it is never applied. Unknown keys are dropped (the batch keeps them in import_row.raw)."""
    if not isinstance(p, dict):
        raise ApiError(400, "A show must be a JSON object.")
    skip = text(p, "skip_reason", LEN["skip_reason"])
    need = skip is None

    row_no = p.get("row_no")
    if row_no is not None and (type(row_no) is not int or row_no < 1):
        raise ApiError(400, "row_no must be a positive whole number.")

    raw_date, d = p.get("date"), None
    if raw_date not in (None, ""):
        try:
            d = parse_date(raw_date)[0]
        except ValueError:
            if need:
                raise ApiError(400, f"date {raw_date!r} is not YYYY-MM-DD, YYYY-MM or YYYY.") from None
    elif need:
        raise ApiError(400, "date is required.")
    status = p.get("status")
    if (need or status is not None) and status not in records.STATUSES:
        raise ApiError(400, f"status must be one of: {', '.join(records.STATUSES)}.")

    billing = text(p, "billing", LEN["billing"], required=need) or ""
    acts = _acts(_list(p, "acts"))
    if not acts and need:
        acts = [{"name": billing, "role": "headliner", "confidence": "confirmed", "confidence_note": None,
                 "sources": []}]
    country = text(p, "country", 2)
    if country is not None:
        country = country.upper()
        if not _COUNTRY.fullmatch(country):
            raise ApiError(400, "country must be a two-letter code such as US or CA.")
    flags = _list(p, "flags")
    if not all(isinstance(f, str) and 0 < len(f) <= LEN["flag"] for f in flags):
        raise ApiError(400, "flags must be short text.")

    return {
        "row_no": row_no, "date": d, "status": status, "billing": billing, "acts": acts,
        "venue": text(p, "venue", LEN["venue"], required=need) or "",
        "city": text(p, "city", LEN["city"], required=need) or "",
        "region": text(p, "region", LEN["region"]), "country": country,
        "sources": _sources(_list(p, "sources"), ""),
        "notes": text(p, "notes", LEN["notes"]), "tour": text(p, "tour", LEN["tour"]),
        "run": text(p, "run", LEN["run"]),
        "confidence": _choice(p, "confidence", records.CONFIDENCES, "confirmed"),
        "confidence_note": text(p, "confidence_note", LEN["note"]),
        "flags": list(flags), "skip_reason": skip,
    }


def _validate_row(p, row_no):
    try:
        return validate_proposal(p)
    except ApiError as e:
        raise ApiError(e.status, f"Row {row_no}: {e.message}") from None


# ---------------------------------------------------------------- matching
def _artist_key(con, name, mbid=None):
    aid = records.resolve_artist(con, name, mbid)
    return ("id", aid) if aid is not None else ("name", norm(name))


def _span(d):
    value, precision, first = parse_date(d)
    return precision, Date.fromisoformat(first), Date.fromisoformat(period_end(value, precision))


def _side(date_s, venue_id, venue, city, region, billing, artists, show_id=None, row_no=None):
    """What matching compares, for a proposal, an existing show or an earlier row."""
    precision, start, end = _span(date_s)
    return {"show_id": show_id, "row_no": row_no, "date": date_s, "precision": precision, "start": start, "end": end,
            "venue_id": venue_id, "venue": venue, "city": norm(city), "region": norm(region),
            "billing": norm(billing), "artists": artists}


def _proposal_side(con, p, row_no=None):
    names = {}
    for a in p["acts"]:
        names.setdefault(_artist_key(con, a["name"], a.get("mbid")), a["name"])
    side = _side(p["date"], records.find_venue(con, p["venue"], p["city"]), venue_norm(p["venue"]), p["city"],
                 p["region"], p["billing"], set(names), row_no=row_no)
    side["names"] = names
    return side


def _shows_near(con, side):
    """Existing shows whose period could come within a day of the proposal's."""
    lo, hi = (side["start"] - timedelta(days=367)).isoformat(), (side["end"] + _DAY).isoformat()
    rows = con.execute("SELECT s.id, s.date, s.billing, s.venue_id, v.city, v.region FROM show s "
                       "JOIN venue v ON v.id = s.venue_id WHERE s.sort_date BETWEEN ? AND ? ORDER BY s.id", (lo, hi))
    for r in rows.fetchall():
        artists = {("id", a[0]) for a in con.execute("SELECT artist_id FROM lineup WHERE show_id = ?", (r["id"],))}
        yield _side(r["date"], r["venue_id"], None, r["city"], r["region"], r["billing"], artists, show_id=r["id"])


def _compare(p, c):
    """("have" | "maybe" | None, reason) for proposal side p against candidate c."""
    shared = p["artists"] & c["artists"]
    if not shared and p["billing"] != c["billing"]:
        return None, ""
    if p["start"] - _DAY > c["end"] or c["start"] - _DAY > p["end"]:
        return None, ""
    if p["venue_id"] is not None and c["venue_id"] is not None:
        same_venue = p["venue_id"] == c["venue_id"]
    else:
        same_venue = p["venue"] is not None and p["venue"] == c["venue"] and p["city"] == c["city"]
    same_city = p["city"] == c["city"] and (not p["region"] or not c["region"] or p["region"] == c["region"])
    same_night = p["date"] == c["date"] or (p["precision"] == c["precision"] == "day")
    who = (f"row {c['row_no']} of this import" if c["show_id"] is None else f"show #{c['show_id']}")
    what = p["names"][min(shared, key=str)] + " in common" if shared else "the same billing"
    when = ("the same date" if p["date"] == c["date"] else "a day apart" if same_night
            else f"date not exact ({p['date']} and {c['date']})")
    if same_venue and same_night:
        return "have", f"Same venue, {when}, {what}: {who}."
    if same_venue:
        return "maybe", f"Same venue, {when}, {what}: {who}."
    if same_city:
        return "maybe", f"Same city, {when}, {what}, at another venue: {who}."
    return None, ""


def _best(p, candidates):
    best = None
    for c in candidates:
        verdict, reason = _compare(p, c)
        if verdict is None:
            continue
        rank = (verdict != "have", c["show_id"] is None, abs((p["start"] - c["start"]).days),
                c["show_id"] or 0, c["row_no"] or 0)
        if best is None or rank < best[0]:
            best = (rank, verdict, c["show_id"], reason)
    return best[1:] if best else ("new", None, "Not in your shows yet.")


def _match(con, p, earlier=()):
    if p["skip_reason"]:
        return "skip", None, p["skip_reason"]
    side = _proposal_side(con, p)
    found = _best(side, _shows_near(con, side))
    if found[0] == "have":
        return found
    in_batch = _best(side, earlier)
    if in_batch[0] == "have" or found[0] == "new":
        return in_batch
    return found


def match(con, p: dict) -> "tuple[str, int | None, str]":
    """(verdict, show_id, reason) for a validated proposal against the shows we have (spec 7.3 step 2). show_id is
    the existing show for have/maybe, else None."""
    return _match(con, p)


# ---------------------------------------------------------------- batches
def _batch(con, batch_id):
    b = con.execute("SELECT * FROM import_batch WHERE id = ?", (batch_id,)).fetchone()
    if b is None:
        raise ApiError(404, "Import not found.")
    return b


def _need_pending(b):
    if b["status"] == "applied":
        raise ApiError(409, "This import was already applied.")
    if b["status"] == "discarded":
        raise ApiError(409, "This import was discarded.")


def _earlier(con, batch_id, before_row_no, before_id):
    """Match sides for the batch's rows before this one that are not skipped."""
    rows = con.execute("SELECT row_no, parsed FROM import_row WHERE batch_id = ? AND verdict <> 'skip' "
                       "AND (row_no < ? OR (row_no = ? AND id < ?)) ORDER BY row_no, id",
                       (batch_id, before_row_no, before_row_no, before_id)).fetchall()
    return [_proposal_side(con, json.loads(r["parsed"]), row_no=r["row_no"]) for r in rows]


def create_batch(con, origin, label, proposals) -> int:
    """Validate every proposal (400 "Row N: ..." on the first bad one, and no batch), match each against the shows
    we have and the rows before it, and store a pending batch. CSV rows keep the parser's row_no; API batches number
    their list from 1. `include` starts true for new rows only."""
    if origin not in ORIGINS:
        raise ApiError(400, f"origin must be one of: {', '.join(ORIGINS)}.")
    label = text({"label": label}, "label", LEN["label"], required=True)
    if not isinstance(proposals, list) or not proposals:
        raise ApiError(400, "No shows to import.")
    if len(proposals) > MAX_ROWS:
        raise ApiError(400, f"At most {MAX_ROWS} shows in one import.")
    rows = []
    for i, p in enumerate(proposals, 1):
        given = p.get("row_no") if isinstance(p, dict) else None
        row_no = given if origin == "csv" and type(given) is int and given > 0 else i
        v = _validate_row(p, row_no)
        v["row_no"] = row_no
        rows.append((p, v))
    with transaction(con):
        batch_id = con.execute("INSERT INTO import_batch (origin, label) VALUES (?, ?)", (origin, label)).lastrowid
        earlier = []
        for raw, v in rows:
            verdict, show_id, reason = _match(con, v, earlier)
            con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict, match_show_id, reason, include)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (batch_id, v["row_no"], json.dumps(raw, ensure_ascii=False), json.dumps(v, ensure_ascii=False),
                         verdict, show_id, reason, int(verdict == "new")))
            if verdict != "skip":
                earlier.append(_proposal_side(con, v, row_no=v["row_no"]))
    return batch_id


def _counts(con, batch_id):
    counts = dict.fromkeys(VERDICTS, 0)
    for r in con.execute("SELECT verdict, COUNT(*) FROM import_row WHERE batch_id = ? GROUP BY verdict", (batch_id,)):
        counts[r[0]] = r[1]
    return counts


def _differs(con, p, show_id):
    """The fields where the proposal says something other than the show we have (spec 7.3 step 3)."""
    s = con.execute("SELECT s.*, r.name AS run_name FROM show s LEFT JOIN run r ON r.id = s.run_id WHERE s.id = ?",
                    (show_id,)).fetchone()
    lineup = [(("id", r["artist_id"]), r["role"], r["confidence"]) for r in con.execute(
        "SELECT artist_id, role, confidence FROM lineup WHERE show_id = ? ORDER BY position, id", (show_id,))]
    have_sources = {norm(r[0]) for r in con.execute(
        "SELECT label FROM source WHERE show_id = ? AND lineup_id IS NULL", (show_id,))}
    same = {
        "date": p["date"] == s["date"],
        "status": p["status"] == s["status"],
        "billing": norm(p["billing"]) == norm(s["billing"]),
        "venue": norm(p["venue"]) == norm(s["venue_label"]),
        "acts": [(_artist_key(con, a["name"], a.get("mbid")), a["role"], a["confidence"]) for a in p["acts"]] == lineup,
        "run": norm(p["run"]) == norm(s["run_name"]),
        "tour": (p["tour"] or "") == (s["tour"] or "").strip(),
        "notes": (p["notes"] or "") == (s["notes"] or "").strip(),
        "confidence": p["confidence"] == s["confidence"],
        "sources": all(norm(x["label"]) in have_sources for x in p["sources"]),
    }
    return [f for f in DIFF_FIELDS if not same[f]]


def _row_view(con, r):
    parsed = json.loads(r["parsed"])
    m = None
    if r["match_show_id"] is not None:
        s = con.execute("SELECT id, date, billing, venue_label FROM show WHERE id = ?", (r["match_show_id"],)).fetchone()
        m = dict(s) if s else None
    return {"id": r["id"], "row_no": r["row_no"], "verdict": r["verdict"], "reason": r["reason"],
            "include": bool(r["include"]), "parsed": parsed, "match": m,
            "differs": _differs(con, parsed, m["id"]) if m and r["verdict"] in ("have", "maybe") else []}


def batch_view(con, batch_id) -> dict:
    """{"id", "origin", "label", "status", "counts": {verdict: n} (all four), "rows": [{"id", "row_no", "verdict",
    "reason", "include", "parsed", "match": {"id", "date", "billing", "venue_label"} | None, "differs": [field]}]}.
    `match` is the existing show a have/maybe row was matched to (None for new and skip rows, and for a row that
    repeats an earlier row of the same batch: its reason names that row). `differs` lists, from DIFF_FIELDS, where
    the row disagrees with that show, worked out now."""
    b = _batch(con, batch_id)
    rows = con.execute("SELECT * FROM import_row WHERE batch_id = ? ORDER BY row_no, id", (batch_id,)).fetchall()
    return {"id": b["id"], "origin": b["origin"], "label": b["label"], "status": b["status"],
            "counts": _counts(con, batch_id), "rows": [_row_view(con, r) for r in rows]}


def update_row(con, row_id, parsed=None, include=None) -> dict:
    """Edit one row of a pending batch (409 once applied or discarded) and return it as batch_view shows it. New
    `parsed` is validated and matched again (against the shows we have and the rows before it); `include` then
    resets to the new verdict's default unless given. have and skip rows can't be included (400)."""
    if include is not None and not isinstance(include, bool):
        raise ApiError(400, "include must be true or false.")
    with transaction(con):
        r = con.execute("SELECT * FROM import_row WHERE id = ?", (row_id,)).fetchone()
        if r is None:
            raise ApiError(404, "Import row not found.")
        _need_pending(_batch(con, r["batch_id"]))
        verdict = r["verdict"]
        if parsed is not None:
            v = _validate_row(parsed, r["row_no"])
            v["row_no"] = r["row_no"]
            verdict, show_id, reason = _match(con, v, _earlier(con, r["batch_id"], r["row_no"], r["id"]))
            con.execute("UPDATE import_row SET parsed = ?, verdict = ?, match_show_id = ?, reason = ?, include = ? "
                        "WHERE id = ?", (json.dumps(v, ensure_ascii=False), verdict, show_id, reason,
                                         int(verdict == "new"), row_id))
        if include is not None:
            if include and verdict == "have":
                raise ApiError(400, "You already have this show; it can't be imported again.")
            if include and verdict == "skip":
                raise ApiError(400, "This row is skipped, so it can't be imported.")
            con.execute("UPDATE import_row SET include = ? WHERE id = ?", (int(include), row_id))
        return _row_view(con, con.execute("SELECT * FROM import_row WHERE id = ?", (row_id,)).fetchone())


def _create(con, p, label):
    """One proposal -> venue, run, show, lineup (acts in order) and sources, plus the batch label as a source."""
    venue_id = records.get_or_create_venue(con, p["venue"], p["city"], p["region"], p["country"])
    run_id = records.get_or_create_run(con, p["run"]) if p["run"] else None
    show_id = records.create_show(con, status=p["status"], date=p["date"], billing=p["billing"], venue_id=venue_id,
                                  venue_label=p["venue"], run_id=run_id, tour=p["tour"], notes=p["notes"],
                                  confidence=p["confidence"], confidence_note=p["confidence_note"])
    for a in p["acts"]:
        artist_id = records.get_or_create_artist(con, a["name"], a.get("mbid"))
        row = con.execute("SELECT id FROM lineup WHERE show_id = ? AND artist_id = ?", (show_id, artist_id)).fetchone()
        # two spellings of one artist (an alias) on one bill: one entry, carrying both entries' sources
        lineup_id = row["id"] if row else records.add_lineup_entry(
            con, show_id, artist_id, a["role"], confidence=a["confidence"], confidence_note=a["confidence_note"])
        for s in a["sources"]:
            records.add_source(con, show_id, s["label"], s["url"], lineup_id=lineup_id)
    seen = set()
    for s in [*p["sources"], {"label": label, "url": None}]:
        key = (norm(s["label"]), s["url"])
        if key not in seen:
            seen.add(key)
            records.add_source(con, show_id, s["label"], s["url"])
    return show_id


def apply_batch(con, batch_id) -> dict:
    """Create a show for every ticked new or maybe row, in one transaction: {"created": n}. 409 unless the batch is
    pending. The transaction takes SQLite's write lock before it reads the batch's status, so a second apply (a double
    click, two tabs) waits for the first, then finds it applied: 409, nothing created. A ticked row that now matches a
    show we have (one made since review, or by another batch of the same file) becomes `have` and is not created."""
    created = 0
    with transaction(con):
        b = _batch(con, batch_id)
        _need_pending(b)
        rows = con.execute("SELECT id, parsed FROM import_row WHERE batch_id = ? AND include = 1 "
                           "AND verdict IN ('new', 'maybe') ORDER BY row_no, id", (batch_id,)).fetchall()
        for r in rows:
            p = json.loads(r["parsed"])
            verdict, show_id, reason = _match(con, p)
            if verdict == "have":
                con.execute("UPDATE import_row SET verdict = 'have', match_show_id = ?, reason = ?, include = 0 "
                            "WHERE id = ?", (show_id, reason, r["id"]))
                continue
            _create(con, p, b["label"])
            created += 1
        con.execute("UPDATE import_batch SET status = 'applied', applied_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (batch_id,))
    return {"created": created}


def discard_batch(con, batch_id) -> None:
    """Drop a pending batch from review (409 once applied or discarded). Its rows stay, for the record."""
    with transaction(con):
        _need_pending(_batch(con, batch_id))
        con.execute("UPDATE import_batch SET status = 'discarded' WHERE id = ?", (batch_id,))


def pending_batches(con) -> list:
    """[{"id", "origin", "label", "status", "created_at", "counts"}] for batches waiting for review, newest first."""
    rows = con.execute("SELECT id, origin, label, status, created_at FROM import_batch WHERE status = 'pending' "
                       "ORDER BY created_at DESC, id DESC").fetchall()
    return [{**dict(r), "counts": _counts(con, r["id"])} for r in rows]
