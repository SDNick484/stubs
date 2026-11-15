"""The error type routes and modules raise, request-body helpers, names (spec 7.2), dates and the clock. No routes."""
import calendar
import re
import unicodedata
from datetime import date

from flask import request


class ApiError(Exception):
    """Raised anywhere while handling a request; app.py renders it as {"error": message} with this status."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra  # more keys for the JSON body (a 409 that names the record in the way)


# ---------------------------------------------------------------- request bodies
def json_body() -> dict:
    """The request's JSON object. Anything else (no body, bad JSON, a list, a string) is 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    return data


def text(data: dict, key: str, max_len: int, required: bool = False) -> str | None:
    """data[key] stripped. Missing, null or empty gives None, or 400 when required. Not a string, or
    longer than max_len, is 400: text is never cut."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    value = (value or "").strip()
    if not value:
        if required:
            raise ApiError(400, f"{key} is required.")
        return None
    if len(value) > max_len:
        raise ApiError(400, f"{key} must be at most {max_len} characters.")
    return value


# ---------------------------------------------------------------- names
# Curly and other typographic quotes become straight ones, so "Jack’s" and "Jack's" match.
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "´": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'})
_SPACE = re.compile(r"\s+")


def norm(s) -> str:
    """The form names are matched by: Unicode NFKC, typographic quotes to straight, case-folded, whitespace collapsed,
    trimmed. `&` and `and` are kept as written ("Juniper & The Owls" is not "Juniper and The Owls")."""
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES).casefold()
    return _SPACE.sub(" ", s).strip()


def venue_norm(s) -> str:
    """norm() without a leading "the " word: "The Bellwether" and "Bellwether" match (callers also compare the city)."""
    n = norm(s)
    return n[4:] if n.startswith("the ") and len(n) > 4 else n


# ---------------------------------------------------------------- dates
_DATE = re.compile(r"([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?")
PRECISIONS = ("year", "month", "day")


def parse_date(s) -> tuple[str, str, str]:
    """"2025-07-19" -> ("2025-07-19", "day", "2025-07-19"); "2025-07" -> ("2025-07", "month", "2025-07-01");
    "2027" -> ("2027", "year", "2027-01-01"). sort_date is the first day of the period. Surrounding spaces are
    dropped; anything else that is not a real date in one of those shapes raises ValueError."""
    if not isinstance(s, str):
        raise ValueError(f"not a date: {s!r}")
    m = _DATE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"not a date: {s!r} (use YYYY-MM-DD, YYYY-MM or YYYY)")
    year, month, day = m.groups()
    first = date(int(year), int(month or 1), int(day or 1))  # ValueError for month 13, Feb 30, year 0
    precision = "day" if day else "month" if month else "year"
    return m.group(0), precision, first.isoformat()


def period_end(d: str, precision: str) -> str:
    """The last ISO day of the period the date names: ("2027", "year") -> "2027-12-31",
    ("2024-02", "month") -> "2024-02-29", a day is itself. The precision must agree with the date (ValueError)."""
    value, actual, first = parse_date(d)
    if actual != precision:
        raise ValueError(f"{d!r} is a {actual}, not a {precision}")
    y, m, _ = (int(x) for x in first.split("-"))
    if precision == "year":
        return f"{y:04d}-12-31"
    if precision == "month":
        return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
    return value


# ---------------------------------------------------------------- the clock
_frozen_today = None  # tests set this through the `clock` fixture


def today() -> str:
    """Today as YYYY-MM-DD. Every module asks here, so tests can freeze the date."""
    return _frozen_today or date.today().isoformat()
