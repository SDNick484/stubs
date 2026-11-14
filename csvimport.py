"""Parse the owner's concerts CSV into Proposals (spec 7.3 step 1). Pure functions; no routes, no database."""
import csv
import io
import re

import common

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
CA_PROVINCES = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())

REQUIRED = ("date", "artist", "venue", "city", "status")
_EVENT = re.compile(r"(.+?)\s*\((.+,.+)\)")  # "Event (A, B, C)": the parentheses must hold a list
_URL = re.compile(r"https?://\S+", re.I)


def _decode(data: bytes) -> str:
    """UTF-8 (a BOM is dropped), else Windows-1252 as an older Excel "CSV" save writes it."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _act(name: str, role: str, confidence="confirmed", note=None, sources=None) -> dict:
    return {"name": name, "role": role, "confidence": confidence, "confidence_note": note,
            "sources": sources or []}


def split_billing(billing: str) -> tuple[str, list[dict], list[str]]:
    """(billing, acts, flags). "Event (A, B, C)" -> billing "Event" with performers; " + " -> coheadliners; otherwise
    one headliner. "&" never splits, but a billing containing " & " is flagged "check acts"."""
    billing = billing.strip()
    m = _EVENT.fullmatch(billing)
    if m:
        names = [n.strip() for n in m.group(2).split(",") if n.strip()]
        return m.group(1).strip(), [_act(n, "performer") for n in names], []
    flags = ["check acts"] if " & " in billing else []
    parts = [x.strip() for x in billing.split(" + ") if x.strip()]
    if len(parts) > 1:
        return billing, [_act(n, "coheadliner") for n in parts], flags
    return billing, [_act(billing, "headliner")], flags


def parse_city(s: str) -> tuple[str, str | None, str | None]:
    """"Oakland, CA" -> ("Oakland", "CA", "US"); "Vancouver, BC" -> ("Vancouver", "BC", "CA"). An unknown code is kept
    as the region with country None (the review asks); no comma gives (city, None, None)."""
    city, sep, code = (s or "").strip().rpartition(",")
    if not sep:
        return code.strip(), None, None
    city, code = city.strip(), code.strip().upper()
    if not code:
        return city, None, None
    if code in US_STATES:
        return city, code, "US"
    if code in CA_PROVINCES:
        return city, code, "CA"
    return city, code, None


def _sources(cell: str, seps: tuple) -> list[dict]:
    parts = [cell]
    for sep in seps:
        parts = [q for p in parts for q in p.split(sep)]
    return [_source(p) for p in (x.strip() for x in parts) if p]


def _source(label: str) -> dict:
    if _URL.fullmatch(label):
        return {"label": label.split("/")[2], "url": label}
    return {"label": label, "url": None}


def _proposal(row_no: int, row: dict) -> dict:
    cell = {k: (v or "").strip() for k, v in row.items() if k}
    skip = None
    flags = []

    date = None
    try:
        date = common.parse_date(cell["date"])[0]
    except ValueError:
        skip = f"bad date: {cell['date']!r}"

    status_word = cell["status"].lower()
    status = {"attended": "attended", "upcoming": "upcoming", "rescheduled": "upcoming"}.get(status_word)
    if status_word in ("missed", "canceled", "cancelled"):
        skip = skip or "not attended"
    elif status is None:
        skip = skip or f"unknown status: {cell['status']!r}"

    if not cell["artist"]:
        skip = skip or "no artist"
    if not cell["venue"]:
        skip = skip or "no venue"
    billing, acts, bflags = split_billing(cell["artist"])
    flags += bflags

    city, region, country = parse_city(cell["city"])
    if country is None:
        flags.append("country?")

    note = cell.get("openers_note") or None
    osrc = _sources(cell.get("openers_source", ""), ())
    for name in (n.strip() for n in cell.get("openers", "").split(";")):
        if name:
            acts.append(_act(name, "opener", "likely" if note else "confirmed", note, [dict(s) for s in osrc]))

    notes = cell.get("notes") or None
    approx = "date approximate" in (notes or "").lower()
    return {
        "row_no": row_no, "date": date, "status": status if not skip or skip.startswith("bad date") else None,
        "billing": billing, "acts": acts,
        "venue": cell["venue"], "city": city, "region": region, "country": country,
        "sources": _sources(cell.get("ticket_source", ""), (" + ", " / ")),
        "notes": notes, "tour": None, "run": None,
        "confidence": "likely" if approx else "confirmed", "confidence_note": "date approximate" if approx else None,
        "flags": flags, "skip_reason": skip,
    }


def parse_csv(data: bytes) -> list[dict]:
    """One Proposal per data row, skipped rows included (skip_reason set). Header names are matched case-insensitively
    and in any order; extra columns are ignored (the Proposal shape has no place for them: deviation from spec 7.3's
    "kept in raw"). A missing required column raises ValueError("missing column: <name>")."""
    reader = csv.reader(io.StringIO(_decode(data), newline=""))
    header = next(reader, None)
    if header is None:
        raise ValueError("empty file")
    names = [h.strip().lower() for h in header]
    for col in REQUIRED:
        if col not in names:
            raise ValueError(f"missing column: {col}")
    out = []
    for i, values in enumerate((r for r in reader if r), 1):
        if not any(v.strip() for v in values):
            continue  # a row of bare commas
        out.append(_proposal(i, dict(zip(names, values))))
    return out
