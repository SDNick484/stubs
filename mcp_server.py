#!/usr/bin/env python3
"""Stubs MCP server (post-v1 #46): tools that answer from the concert history ("every time I've seen X", "what did I
see in 2009", "how many times have I heard X") and, at write level `import`, send shows found elsewhere to the import
review screen.

It talks to a running Stubs over HTTP, through the same /admin/api routes the app's own pages use, with one key:
STUBS_READ_KEY at write level none (the default) and STUBS_API_KEY at write level import. STUBS_URL is where Stubs
listens (empty: http://127.0.0.1:<PORT>, PORT 8773). It never opens the database and never imports app or any of the
app's modules: the MCP venv has no Flask, and the stubs-mcp user can't read /var/lib/stubs.

Reads use the editor's view (upcoming shows, notes, sources and confidence included) but never companions: no tool
calls a /people route, so who went with the owner stays in the app. There is no apply, edit or delete tool: a batch
from import_shows waits in the review screen until the owner applies it there (spec 2's anti-goal: no import silently
changes anything).

    python mcp_server.py      stdio, for Claude Code or Desktop (env: STUBS_URL, STUBS_READ_KEY or STUBS_API_KEY,
                              STUBS_MCP_WRITES)
    python mcp_http.py        Streamable HTTP behind Cloudflare Access: the stubs-mcp service (docs/mcp-setup.md)
"""
import inspect
import json
import logging
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # the app's own venv and CI jobs without requirements-mcp.txt
    FastMCP = None

try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    ToolError = Exception

log = logging.getLogger("stubs.mcp")

DEFAULT_PORT = "8773"
UA = "stubs-mcp/1.0"
TIMEOUT = 15
LEVELS = ("none", "import")
# Which write tools each level registers. Both tools at `import` only send a batch to review or list the waiting ones.
WRITE_LEVELS: Dict[str, Tuple[str, ...]] = {"none": (), "import": ("import_shows", "list_imports")}
WRITE_TOOLS = WRITE_LEVELS["import"]
MAX_SHOWS = 1000  # search_shows' largest limit


# ---------------------------------------------------------------- settings (from an environment mapping)
def _resolve(env: Mapping[str, str]) -> Tuple[str, List[str]]:
    raw = (env.get("STUBS_MCP_WRITES") or "").strip()
    want = raw.lower()
    if not want:
        return "none", []
    if want in LEVELS:
        return want, []
    return "none", [f"unknown STUBS_MCP_WRITES {raw!r}; using none"]


def write_level(env: Mapping[str, str]) -> str:
    """"none" (the default, and any unknown value) or "import"."""
    return _resolve(env)[0]


def write_level_warnings(env: Mapping[str, str]) -> List[str]:
    """Lines to log at startup when STUBS_MCP_WRITES holds an unknown value."""
    return _resolve(env)[1]


def service_key(env: Mapping[str, str]) -> Tuple[str, str]:
    """(name, value) of the one key this service holds: STUBS_READ_KEY at none, STUBS_API_KEY at import."""
    name = "STUBS_API_KEY" if write_level(env) == "import" else "STUBS_READ_KEY"
    return name, (env.get(name) or "").strip()


def stubs_url(env: Mapping[str, str]) -> str:
    """Where the Flask app listens: STUBS_URL, else loopback on its PORT."""
    return ((env.get("STUBS_URL") or "").strip()
            or f"http://127.0.0.1:{(env.get('PORT') or '').strip() or DEFAULT_PORT}").rstrip("/")


def startup_problem(env: Mapping[str, str]) -> Optional[str]:
    """Why the server must not start with these settings, or None."""
    level = write_level(env)
    name, key = service_key(env)
    if not key:
        return f"{name} is empty; at write level {level} the service reads Stubs through /admin/api with it."
    if level == "none" and (env.get("STUBS_API_KEY") or "").strip():
        return ("STUBS_API_KEY must not be in this service's environment at write level none (it can write); it "
                "uses STUBS_READ_KEY. Re-run the installer to refresh /etc/init.d/stubs-mcp.")
    return None


def client_for(env: Mapping[str, str]) -> "StubsClient":
    """The client the tools use with these settings: the level's key, STUBS_URL."""
    name, key = service_key(env)
    return StubsClient(api_url=stubs_url(env), api_key=key, key_name=name)


# ---------------------------------------------------------------- names (copies of common.norm and venue_norm)
# Kept local so this module never imports common (which imports Flask); tests/test_mcp_server.py keeps them equal.
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "´": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'})
_SPACE = re.compile(r"\s+")


def _norm(s) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES).casefold()
    return _SPACE.sub(" ", s).strip()


def _venue_norm(s) -> str:
    n = _norm(s)
    return n[4:] if n.startswith("the ") and len(n) > 4 else n


# ---------------------------------------------------------------- the client
class StubsError(RuntimeError, ToolError):
    """Every failure a tool reports. As a ToolError its text reaches the model."""

    def __init__(self, message: str, status: Optional[int] = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class StubsClient:
    """JSON over HTTP to Stubs, with the key as `Authorization: Bearer`. The key is never put in a message or a log."""

    def __init__(self, api_url: str, api_key: str, key_name: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.key_name = key_name

    def _open(self, req, timeout):
        return urllib.request.urlopen(req, timeout=timeout)

    def request(self, method: str, path: str, data: Any = None, **params) -> Any:
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{self.api_url}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        headers = {"Accept": "application/json", "User-Agent": UA, "Authorization": f"Bearer {self.api_key}"}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            log.warning("Stubs %s %s -> HTTP %s", method, path, e.code)
            if e.code in (401, 403):
                raise StubsError(f"Stubs rejected the service's key (HTTP {e.code}). Check {self.key_name} on the "
                                 "Stubs host.", status=e.code) from None
            detail = ""
            try:
                err = json.loads(e.read().decode("utf-8", errors="replace")[:2000])
                if isinstance(err, dict):
                    detail = str(err.get("error") or "")[:300]
            except ValueError:
                pass
            raise StubsError(f"Stubs API error ({e.code})" + (f": {detail}" if detail else ""), status=e.code,
                             detail=detail) from None
        except Exception as e:  # noqa: BLE001 - refused, timed out, reset: all mean "not answering"
            log.warning("Stubs %s %s failed: %s", method, path, type(e).__name__)
            raise StubsError("Stubs is unreachable right now. Try again in a minute.") from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise StubsError("Stubs sent something that isn't JSON.") from None

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, **params)

    def post(self, path: str, data: Any) -> Any:
        return self.request("POST", path, data)


_default_client: Optional[StubsClient] = None


def _client() -> StubsClient:
    global _default_client
    if _default_client is None:
        _default_client = client_for(os.environ)
    return _default_client


def _get_or_none(path: str, **params) -> Any:
    """GET, with a 404 as None."""
    try:
        return _client().get(path, **params)
    except StubsError as e:
        if e.status == 404:
            return None
        raise


# ---------------------------------------------------------------- resolving names
def _artist(artist: Optional[str], artist_id: Optional[int]) -> Dict[str, Any]:
    """{"id", "name"} of the artist a tool was asked about: by id, else by name (current name or an alias, matched as
    the app's search does). An exact name wins; otherwise a single match; several are an error that lists them."""
    if artist_id is not None:
        d = _get_or_none(f"/admin/api/artists/{int(artist_id)}")
        if d is None:
            raise StubsError(f"No artist with id {artist_id}.", status=404)
        return {"id": d["id"], "name": d["name"]}
    name = (artist or "").strip()
    if not name:
        raise StubsError("Pass artist or artist_id.")
    found = _client().get("/admin/api/artists", q=name)
    exact = [a for a in found if _norm(a["name"]) == _norm(name)]
    if len(exact) == 1:
        found = exact
    if not found:
        raise StubsError(f"No artist in Stubs matches {name!r}.")
    if len(found) > 1:
        listed = ", ".join(f"{a['name']} (id {a['id']})" for a in found)
        raise StubsError(f"Several artists match {name!r}: {listed}. Ask which one, then call again with artist_id.")
    return {"id": found[0]["id"], "name": found[0]["name"]}


def _venue(venue: str) -> int:
    name = venue.strip()
    found = _client().get("/admin/api/venues", q=name)
    exact = [v for v in found if _venue_norm(v["name"]) == _venue_norm(name)]
    if len(exact) == 1:
        found = exact
    if not found:
        raise StubsError(f"No venue in Stubs matches {name!r}.")
    if len(found) > 1:
        listed = ", ".join(f"{v['name']}, {v['city']} (id {v['id']})" for v in found)
        raise StubsError(f"Several venues match {name!r}: {listed}. Ask which one, then call again with venue_id.")
    return found[0]["id"]


def _place(show: Dict[str, Any]) -> Dict[str, Any]:
    """A show row (browse.show_row) as the tools give it: id, date, billing, and where."""
    v = show["venue"]
    return {"show_id": show["id"], "date": show["date"], "billing": show["billing"], "venue": v["label"],
            "city": v["city"], "region": v["region"], "country": v["country"]}


def _review_link(batch_id: int) -> str:
    return f"/admin#/admin/import/{batch_id}"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


# ---------------------------------------------------------------- read tools
def find_artist(name: str) -> Dict[str, Any]:
    """Find artists in the concert history by name. Matches current names and aliases (former or alternate names),
    any part of the name, case and accents as Stubs matches them. Each artist comes with its id and counts: times
    (attended shows, in any role: headliner, co-headliner, opener or festival performer), first and last (dates),
    by_role, and upcoming (shows not yet happened, never counted). Use the id as artist_id in the other tools when a
    name is ambiguous."""
    if not (name or "").strip():
        raise StubsError("Pass a name.")
    out = []
    for a in _client().get("/admin/api/artists", q=name.strip()):
        d = _client().get(f"/admin/api/artists/{a['id']}")
        out.append({"id": d["id"], "name": d["name"], "times": d["times"], "first": d["first"], "last": d["last"],
                    "by_role": d["by_role"], "upcoming": len(d.get("upcoming") or [])})
    return {"query": name, "artists": out}


def times_seen(artist: str = "", artist_id: Optional[int] = None) -> Dict[str, Any]:
    """Every time the owner has seen an artist: each attended show, newest first, with its date, venue, city and the
    artist's role that night (headliner, coheadliner, opener or performer at a festival). `times` counts attended
    shows only; `upcoming` lists booked shows separately and they never count. Pass the artist's name (an alias works)
    or artist_id from find_artist."""
    ref = _artist(artist, artist_id)
    d = _client().get(f"/admin/api/artists/{ref['id']}")
    return {"artist": ref, "times": d["times"], "first": d["first"], "last": d["last"], "by_role": d["by_role"],
            "shows": [{**_place(s), "role": s["role"]} for s in d["shows"]],
            "upcoming": [{**_place(s), "role": s["role"]} for s in d.get("upcoming") or []]}


def search_shows(year: Optional[int] = None, artist: Optional[str] = None, venue: Optional[str] = None,
                 city: Optional[str] = None, artist_id: Optional[int] = None, venue_id: Optional[int] = None,
                 limit: int = 100) -> Dict[str, Any]:
    """Shows in the history, filtered by any of: year (e.g. 2009), artist (name or alias, on the bill in any role;
    or artist_id), venue (current or former name; or venue_id) and city (the whole city name, e.g. "Oakland"). Upcoming
    shows come first, then the rest newest first; each has status "attended" or "upcoming". `count` is the number
    found; at most `limit` (default 100) are listed. With no filter it lists every show. get_show(id) has the
    details."""
    if artist_id is None and artist:
        artist_id = _artist(artist, None)["id"]
    if venue_id is None and venue and venue.strip():
        venue_id = _venue(venue)
    rows = _client().get("/admin/api/shows", year=year, artist_id=artist_id, venue_id=venue_id,
                         city=(city or "").strip() or None)
    limit = max(1, min(int(limit), MAX_SHOWS))
    shows = [{"id": s["id"], "date": s["date"], "status": s["status"], "billing": s["billing"],
              "venue": s["venue"]["label"], "city": s["venue"]["city"], "region": s["venue"]["region"],
              "country": s["venue"]["country"], "headliners": s["headliners"], "run": s["run"]} for s in rows]
    return {"count": len(shows), "shows": shows[:limit], "truncated": len(shows) > limit}


def get_show(show_id: int) -> Dict[str, Any]:
    """One show in full: date (and precision), status, billing, venue, tour and run; the lineup in bill order, each
    act with its role, confidence, sources and stored setlist (sets of songs, with the setlist's source); and the
    show's notes, rating, confidence (confirmed or likely, with a note saying why) and sources (the evidence
    trail). prev_id and next_id step through the history in date order."""
    d = _get_or_none(f"/admin/api/shows/{int(show_id)}")
    if d is None:
        raise StubsError(f"No show with id {show_id}.", status=404)
    return d


def get_stats() -> Dict[str, Any]:
    """Totals (attended shows, artists, venues, cities), shows per year, and the top artists, venues, cities and
    songs. Attended shows only."""
    return _client().get("/api/stats")


def upcoming_shows() -> Dict[str, Any]:
    """Shows the owner has tickets for, soonest first. passed=true means the date has gone by and the owner hasn't
    yet said whether they went (Stubs asks "Did you go?" on its admin page); until then it counts nowhere."""
    due = {s["id"] for s in _client().get("/admin/api/did-you-go")["shows"]}
    return {"shows": [{**s, "passed": s["id"] in due} for s in _client().get("/admin/api/upcoming")["shows"]]}


def to_confirm() -> Dict[str, Any]:
    """Facts still marked "likely" (a show, or one act on a show), oldest first, each with the note saying why and its
    sources so far. Help the owner confirm them; confirming happens in the app."""
    return _client().get("/admin/api/to-confirm")


def _songs(ref: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _client().get(f"/admin/api/artists/{ref['id']}/songs")


def songs_heard(artist: str = "", artist_id: Optional[int] = None) -> Dict[str, Any]:
    """Every song the owner has heard an artist play live, most played first: plays (a song played twice in one night
    is two plays), shows (nights), first and last (dates). Counts come from the setlists stored in Stubs for attended
    shows, so a night without a stored setlist adds nothing; say so when it matters."""
    ref = _artist(artist, artist_id)
    heard = [{k: s[k] for k in ("id", "title", "plays", "shows", "first", "last")} for s in _songs(ref) if s["plays"]]
    out = {"artist": ref, "songs_heard": len(heard), "total_plays": sum(s["plays"] for s in heard), "songs": heard}
    if not heard:
        out["message"] = (f"No setlists with {ref['name']} are stored in Stubs yet, so no song counts; a play is a "
                          "song on a stored setlist of an attended show.")
    return out


def song_count(artist: str = "", song: str = "", artist_id: Optional[int] = None) -> Dict[str, Any]:
    """How many times the owner has heard an artist play one song: plays, shows (nights), first and last, and each
    night (date, venue, set and note). The title matches the artist's songs and their alternate titles, any part of
    it. Counts come from the setlists stored in Stubs; found=false (or plays 0) means it is on none of them."""
    ref = _artist(artist, artist_id)
    title = (song or "").strip()
    if not title:
        raise StubsError("Pass a song title.")
    found = _client().get(f"/admin/api/artists/{ref['id']}/songs", q=title)
    exact = [s for s in found if _norm(s["title"]) == _norm(title)]
    if len(exact) == 1:
        found = exact
    if not found:
        return {"artist": ref, "song": None, "query": title, "found": False, "plays": 0,
                "message": f"No song like {title!r} is on any of {_possessive(ref['name'])} stored setlists or in "
                           "their catalog in Stubs."}
    if len(found) > 1:
        listed = ", ".join(f"{s['title']} (id {s['id']})" for s in found)
        raise StubsError(f"Several of {_possessive(ref['name'])} songs match {title!r}: {listed}. Use the full title.")
    pick = found[0]
    d = _get_or_none(f"/admin/api/songs/{pick['id']}")
    if d is None:  # a title Stubs keeps (an alias points at it) with no play and not in the catalog
        return {"artist": ref, "song": {"id": pick["id"], "title": pick["title"]}, "found": True, "plays": 0,
                "shows": 0, "first": None, "last": None, "in_catalog": False, "nights": []}
    nights = [{**_place(s), "set_name": s["set_name"], "encore": s["encore"], "note": s["note"]} for s in d["shows"]]
    return {"artist": ref, "song": {"id": d["id"], "title": d["title"]}, "found": True, "plays": d["plays"],
            "shows": len({n["show_id"] for n in nights}), "first": d["first"], "last": d["last"],
            "in_catalog": d["in_catalog"], "nights": nights}


def never_heard(artist: str = "", artist_id: Optional[int] = None) -> Dict[str, Any]:
    """The songs in an artist's catalog (the songs the owner pasted in as theirs) that the owner has never heard live,
    by title. Needs a catalog: without one the list is empty and the message says so."""
    ref = _artist(artist, artist_id)
    catalog = [s for s in _songs(ref) if s["in_catalog"]]
    never = sorted(({"id": s["id"], "title": s["title"]} for s in catalog if not s["plays"]),
                   key=lambda s: (s["title"].casefold(), s["id"]))
    out = {"artist": ref, "catalog_size": len(catalog), "never_heard": never}
    if not catalog:
        out["message"] = (f"No song catalog for {ref['name']} in Stubs yet: the owner pastes one on the artist's page "
                          "in the app.")
    return out


# ---------------------------------------------------------------- write tools (level import)
IMPORT_DOC = """Send shows found elsewhere (ticket emails, receipts, photos, calendars, messages) to Stubs' import
review screen. Nothing goes into the history: Stubs stores them as a pending batch, matches each against the shows it
already has (new, have, maybe), and the owner reviews, edits and applies the batch in the app. This tool
never applies, edits or deletes anything. Before sending, check with times_seen or search_shows what Stubs has.

label: where these came from, e.g. "Ticket emails 2009-2012" (up to 200 characters).
shows: a list, one show per night (up to 5000):
- A run of nights is one show per night (give them the same "run"). A festival is one show per day, and each day
  lists only the acts the owner actually watched, as "performer": never the whole poster.
- date (required): "YYYY-MM-DD"; "YYYY-MM" or "YYYY" when the source gives no more.
- status (required): "attended", or "upcoming" for a show not yet happened.
- billing (required): the name on the ticket. venue and city (required): as on the ticket; region is the state or
  province code ("CA"), country a two-letter code ("US").
- acts: in bill order, {"name", "role"} with role "headliner", "coheadliner", "opener" or "performer"; without
  acts the billing is the headliner. An act may carry its own confidence, confidence_note and sources.
- sources (every fact needs one: where you found it): {"label", "url"} objects, or plain labels.
- confidence: "confirmed" (the default) or "likely". When unsure (a guessed day, an opener nobody names, not sure
  the owner went) use "likely" and say why in confidence_note; the owner confirms it later.
- notes, tour, run (text); flags: short hints for the review ("check acts"); skip_reason: something you found but
  think should not be added ("refunded: canceled"); the row is shown and never applied.

Example:
[
  {
    "date": "2009-05-04", "status": "attended", "billing": "Cold Harbor with The Paper Lanterns",
    "acts": [
      {"name": "Cold Harbor", "role": "headliner"},
      {"name": "The Paper Lanterns", "role": "opener", "confidence": "likely",
       "confidence_note": "The ticket names only Cold Harbor; the opener is from the venue's calendar"}
    ],
    "venue": "Granite Hall", "city": "Oakland", "region": "CA", "country": "US",
    "sources": [{"label": "Ticket email, 2009-04-02", "url": "https://tickets.example.com/orders/1001"}]
  },
  {
    "date": "2011-07-16", "status": "attended", "billing": "Lantern Fest 2011",
    "acts": [{"name": "Marigold Static", "role": "performer"}, {"name": "The Paper Lanterns", "role": "performer"}],
    "venue": "Riverside Park", "city": "Portland", "region": "OR", "country": "US", "run": "Lantern Fest 2011",
    "confidence": "likely", "confidence_note": "The wristband photo shows Saturday; which acts is from a message",
    "sources": ["Photo of the Saturday wristband"]
  }
]

Returns the batch id, its counts (new, have, maybe, skip) and review_link, the path of the review screen on the
owner's Stubs site (e.g. https://concerts.example.com/admin#/admin/import/12); give the owner the link. A bad show is
refused with the reason ("Row 2: date is required.") and nothing is stored: fix it and send the batch again."""


def import_shows(label: str, shows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        made = _client().post("/admin/api/imports", {"label": label, "shows": shows})
    except StubsError as e:
        if e.status == 400:
            raise StubsError(f"Stubs refused the import, and stored nothing: {e.detail}", status=400,
                             detail=e.detail) from None
        raise
    link = _review_link(made["id"])
    return {"batch_id": made["id"], "label": label, "counts": made["counts"], "review_link": link,
            "note": f"Nothing is in the history yet: the owner reviews this batch on their Stubs site at {link} (or "
                    "the Imports waiting card on the admin page) and applies it there. 'have' rows match a show "
                    "Stubs already has; 'maybe' rows need the owner's eye."}


import_shows.__doc__ = IMPORT_DOC


def list_imports() -> Dict[str, Any]:
    """Import batches still waiting for the owner's review, newest first: id, label, origin (csv or api), created_at,
    counts (new, have, maybe, skip) and review_link (the path of the review screen on the owner's Stubs site)."""
    batches = _client().get("/admin/api/imports")["batches"]
    return {"batches": [{**b, "review_link": _review_link(b["id"])} for b in batches]}


# ---------------------------------------------------------------- the server
READ_TOOLS = (find_artist, times_seen, search_shows, get_show, get_stats, upcoming_shows, to_confirm, songs_heard,
              song_count, never_heard)
WRITE_FUNCS = (import_shows, list_imports)

# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
TOOL_HINTS: Dict[str, Tuple[bool, bool, bool, bool]] = {fn.__name__: (True, False, True, False) for fn in READ_TOOLS}
TOOL_HINTS["list_imports"] = (True, False, True, False)
TOOL_HINTS["import_shows"] = (False, False, False, False)  # each call adds a pending batch; no show changes

SERVER_INSTRUCTIONS = """\
Stubs is the owner's concert history: every show they went to, who was on the bill, where, and what proves it.
- "Every time I've seen X", "how many times have I seen X": times_seen(artist="X"). It counts attended shows in any role \
(headliner, opener, festival performer); upcoming shows are listed apart and never counted.
- "What did I see in 2009", at a venue, in a city, with an artist: search_shows (year, venue, city, artist). \
get_show(show_id) has the lineup, notes, sources, confidence and stored setlists.
- Songs: "how many times have I heard X play Y": song_count(artist, song); "which songs have I heard": \
songs_heard(artist); "which of their songs have I never heard": never_heard(artist). Song counts come only from the \
setlists stored in Stubs; say so when a count is zero.
- A name that matches several artists or venues answers with the list and ids: ask which, then pass artist_id or \
venue_id. find_artist shows the candidates with their counts.
- Totals and top lists: get_stats. Tickets for shows to come: upcoming_shows. Facts still marked likely: to_confirm.
- Who went with the owner (companions) is not available here; it stays in the app.
"""
_IMPORT_NOTE = ("- Shows found elsewhere (emails, receipts, photos): send them with import_shows. They wait in the "
                "review screen and the owner applies them there; give the owner the review link. list_imports lists "
                "the batches still waiting.\n")
_READ_ONLY_NOTE = ("- This connection is read-only: it cannot send shows to Stubs. To add a show, the owner uses the "
                   "Stubs app.\n")


def _level(writes) -> str:
    if isinstance(writes, str) and writes in WRITE_LEVELS:
        return writes
    raise ValueError(f"writes must be one of {sorted(WRITE_LEVELS)}, not {writes!r}")


def instructions_for(writes) -> str:
    """Server instructions that name only the tools registered at this level."""
    return SERVER_INSTRUCTIONS + (_IMPORT_NOTE if _level(writes) == "import" else _READ_ONLY_NOTE)


def _annotation_kwargs(server_cls, name: str) -> Dict[str, Any]:
    """Tool annotations for `name`, only when this mcp release supports them on tool()."""
    if server_cls is None or name not in TOOL_HINTS:
        return {}
    try:
        from mcp.types import ToolAnnotations
        if "annotations" not in inspect.signature(server_cls.tool).parameters:
            return {}
    except (ImportError, TypeError, ValueError):
        return {}
    read_only, destructive, idempotent, open_world = TOOL_HINTS[name]
    return {"annotations": ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                                           idempotentHint=idempotent, openWorldHint=open_world)}


def build_server(*, writes="none"):
    """A new server with the ten read tools, plus import_shows and list_imports at writes="import". None without
    the mcp package. Tool functions read _default_client at call time."""
    level = _level(writes)
    if FastMCP is None:
        return None
    server = FastMCP("stubs", instructions=instructions_for(level))
    for fn in READ_TOOLS + tuple(f for f in WRITE_FUNCS if f.__name__ in WRITE_LEVELS[level]):
        server.tool(**_annotation_kwargs(FastMCP, fn.__name__))(fn)
    return server


def _run_stdio(server) -> None:  # pragma: no cover - blocks on stdin
    server.run()


def main(env: Optional[Mapping[str, str]] = None) -> int:
    """stdio. Returns 2 with a one-line message when the settings are unsafe or the SDK is missing."""
    global _default_client
    env = dict(os.environ if env is None else env)
    problem = startup_problem(env)
    if problem:
        print(f"stubs-mcp: {problem}", file=sys.stderr)
        return 2
    if FastMCP is None:
        print("stubs-mcp: the MCP SDK is not installed: pip install -r requirements-mcp.txt", file=sys.stderr)
        return 2
    for note in write_level_warnings(env):
        print(f"stubs-mcp: {note}", file=sys.stderr)
    _default_client = client_for(env)
    _run_stdio(build_server(writes=write_level(env)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
