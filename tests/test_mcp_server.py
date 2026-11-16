"""The MCP server's tools (mcp_server.py, post-v1 #46) against the real app. Each StubsClient here has its opener
swapped for one that runs the request through app.test_client(), so the tools read and write a fresh test database
through the very routes the stubs-mcp service calls, and nothing leaves the process. The urllib path itself is tested
with urlopen patched (a hit, an HTTP error, a refused connection and a timeout).

Skips when the mcp package is missing (pip install -r requirements-mcp.txt), unless STUBS_MCP_REQUIRED=1 (the CI MCP
job), where that is a failure."""
import asyncio
import io
import json
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

REQUIRED = os.environ.get("STUBS_MCP_REQUIRED") == "1"
try:
    import mcp.server.mcpserver  # noqa: F401
except ImportError:  # pragma: no cover - depends on the machine
    if REQUIRED:
        raise
    pytest.skip("the mcp package is not installed (pip install -r requirements-mcp.txt)", allow_module_level=True)

import app as stubs  # noqa: E402
import common  # noqa: E402
import mcp_server  # noqa: E402
import records  # noqa: E402
from conftest import READ_KEY, WRITE_KEY, mk_artist, mk_show, mk_venue  # noqa: E402

READ_TOOLS = {"find_artist", "times_seen", "search_shows", "get_show", "get_stats", "upcoming_shows", "to_confirm",
              "songs_heard", "song_count", "never_heard"}
IMPORT_TOOLS = {"import_shows", "list_imports"}
COMPANION = "Riley"


# ---------------------------------------------------------------- harness
class Wire:
    """Stands in for urllib.request.urlopen: runs each request through the Flask test client and records it."""

    def __init__(self):
        self.requests = []  # (method, path with query, Authorization header, JSON body or None)

    def __call__(self, req, timeout=None):
        assert timeout, "every request has a timeout"
        url = urlsplit(req.full_url)
        assert f"{url.scheme}://{url.netloc}" == "http://127.0.0.1:8773"
        path = url.path + (f"?{url.query}" if url.query else "")
        headers = dict(req.header_items())
        self.requests.append((req.get_method(), path, headers.get("Authorization"),
                              json.loads(req.data) if req.data else None))
        r = stubs.app.test_client().open(path, method=req.get_method(), headers=headers, data=req.data)
        if r.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, r.status_code, r.status, {}, io.BytesIO(r.data))
        return io.BytesIO(r.data)

    def paths(self, method=None):
        return [p for m, p, _, _ in self.requests if method is None or m == method]


@pytest.fixture
def wire():
    return Wire()


def connect(wire, level="none"):
    """Point the tools at the test app with the key the service holds at this write level."""
    env = {"STUBS_READ_KEY": READ_KEY} if level == "none" else {"STUBS_API_KEY": WRITE_KEY}
    client = mcp_server.client_for({**env, "STUBS_MCP_WRITES": level})
    client._open = wire
    mcp_server._default_client = client
    return client


@pytest.fixture
def reader(wire, monkeypatch):
    monkeypatch.setattr(mcp_server, "_default_client", None)
    return connect(wire, "none")


@pytest.fixture
def importer(wire, monkeypatch):
    monkeypatch.setattr(mcp_server, "_default_client", None)
    return connect(wire, "import")


def call(tool, level="none", /, **args):
    """Call a tool the way a client does (through the server at this level); returns its structured result."""
    result = asyncio.run(mcp_server.build_server(writes=level).call_tool(tool, args))
    assert result.is_error is False, result
    return result.structured_content["result"]


def call_error(tool, level="none", /, **args):
    """The text a client gets when the tool fails."""
    with pytest.raises(mcp_server.ToolError) as e:
        asyncio.run(mcp_server.build_server(writes=level).call_tool(tool, args))
    return str(e.value)


def play(con, show_id, artist, titles):
    """Store a setlist for `artist` on the show (direct SQL, as tests/test_songs.py does)."""
    aid = mk_artist(con, artist)
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ? AND artist_id = ?", (show_id, aid)).fetchone()["id"]
    con.execute("INSERT OR IGNORE INTO setlist (lineup_id, source_label) VALUES (?, ?)", (lid, "fan club site"))
    for i, title in enumerate(titles, start=1):
        con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id) VALUES (?, ?, ?)",
                    (lid, i, records.get_or_create_song(con, aid, title)))
    return lid


@pytest.fixture
def history(con, clock):
    """Three Paper Lanterns nights (one as an opener for Cold Harbor), an upcoming show, a likely show, a song catalog
    and a companion on every show."""
    clock.set("2026-11-14")
    hall = mk_venue(con, "Granite Hall")
    dome = mk_venue(con, "The Tin Dome", city="Portland", region="OR")
    a = mk_show(con, "The Paper Lanterns", "2009-05-04", hall, notes="Front row, lost a shoe")
    b = mk_show(con, "Cold Harbor", "2009-09-12", dome, lineup=(("Cold Harbor", "headliner"),
                                                                ("The Paper Lanterns", "opener")))
    c = mk_show(con, "The Paper Lanterns", "2021-06-05", hall, confidence="likely",
                confidence_note="Ticket stub says June; the day is a guess")
    up = mk_show(con, "The Paper Lanterns", "2027-03-01", dome, status="upcoming")
    play(con, a, "The Paper Lanterns", ["Lantern Light", "Paper Boats", "Night Ferry"])
    play(con, b, "The Paper Lanterns", ["Lantern Light"])
    play(con, c, "The Paper Lanterns", ["Lantern Light", "Paper Boats"])
    lanterns = mk_artist(con, "The Paper Lanterns")
    for title in ("Lantern Light", "Candlewick", "Ember Waltz"):
        con.execute("UPDATE song SET in_catalog = 1 WHERE id = ?", (records.get_or_create_song(con, lanterns, title),))
    con.execute("INSERT INTO artist_alias (artist_id, norm) VALUES (?, ?)", (lanterns, common.norm("Lanterns of Paper")))
    pid = con.execute("INSERT INTO person (name) VALUES (?)", (COMPANION,)).lastrowid
    for show in (a, b, c, up):
        con.execute("INSERT INTO show_person (show_id, person_id) VALUES (?, ?)", (show, pid))
    return {"a": a, "b": b, "c": c, "up": up, "lanterns": lanterns, "hall": hall, "dome": dome,
            "harbor": mk_artist(con, "Cold Harbor")}


# ---------------------------------------------------------------- registration and levels
def names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_none_registers_only_the_read_tools():
    assert names(mcp_server.build_server(writes="none")) == READ_TOOLS


def test_import_registers_import_shows_and_list_imports_only():
    assert names(mcp_server.build_server(writes="import")) == READ_TOOLS | IMPORT_TOOLS
    assert mcp_server.WRITE_LEVELS == {"none": (), "import": ("import_shows", "list_imports")}


def test_there_is_no_apply_edit_or_delete_tool():
    every = names(mcp_server.build_server(writes="import"))
    assert not [n for n in every if re.search(r"apply|edit|delete|update|merge|discard|confirm_|resolve|set_", n)]


@pytest.mark.parametrize("bad", ["all", "IMPORT", "", None, True, False, 1])
def test_build_server_rejects_unknown_levels(bad):
    with pytest.raises(ValueError, match="writes must be"):
        mcp_server.build_server(writes=bad)


@pytest.mark.parametrize("raw,level,warnings", [
    (None, "none", []), ("", "none", []), ("none", "none", []), (" None ", "none", []),
    ("import", "import", []), (" IMPORT ", "import", []),
    ("all", "none", ["unknown STUBS_MCP_WRITES 'all'; using none"]),
    ("apply", "none", ["unknown STUBS_MCP_WRITES 'apply'; using none"]),
])
def test_write_level(raw, level, warnings):
    env = {} if raw is None else {"STUBS_MCP_WRITES": raw}
    assert mcp_server.write_level(env) == level
    assert mcp_server.write_level_warnings(env) == warnings


@pytest.mark.parametrize("writes,name,value", [
    (None, "STUBS_READ_KEY", "r"), ("none", "STUBS_READ_KEY", "r"), ("bogus", "STUBS_READ_KEY", "r"),
    ("import", "STUBS_API_KEY", "w"),
])
def test_service_key(writes, name, value):
    env = {"STUBS_READ_KEY": " r ", "STUBS_API_KEY": "w", **({"STUBS_MCP_WRITES": writes} if writes else {})}
    assert mcp_server.service_key(env) == (name, value)


@pytest.mark.parametrize("env,url", [
    ({}, "http://127.0.0.1:8773"), ({"STUBS_URL": ""}, "http://127.0.0.1:8773"), ({"PORT": "9000"}, "http://127.0.0.1:9000"),
    ({"STUBS_URL": "http://127.0.0.1:8800/", "PORT": "9000"}, "http://127.0.0.1:8800"),
])
def test_stubs_url(env, url):
    assert mcp_server.stubs_url(env) == url


def test_startup_refuses_the_write_key_at_none():
    for env in ({"STUBS_READ_KEY": READ_KEY, "STUBS_API_KEY": WRITE_KEY},
                {"STUBS_READ_KEY": READ_KEY, "STUBS_API_KEY": WRITE_KEY, "STUBS_MCP_WRITES": "none"},
                {"STUBS_READ_KEY": READ_KEY, "STUBS_API_KEY": WRITE_KEY, "STUBS_MCP_WRITES": "bogus"}):
        problem = mcp_server.startup_problem(env)
        assert "STUBS_API_KEY must not be in" in problem and WRITE_KEY not in problem


def test_startup_needs_the_key_for_its_level():
    assert "STUBS_READ_KEY is empty" in mcp_server.startup_problem({"STUBS_READ_KEY": "  "})
    assert "STUBS_API_KEY is empty" in mcp_server.startup_problem({"STUBS_MCP_WRITES": "import",
                                                                   "STUBS_READ_KEY": READ_KEY})
    assert mcp_server.startup_problem({"STUBS_READ_KEY": READ_KEY}) is None
    assert mcp_server.startup_problem({"STUBS_MCP_WRITES": "import", "STUBS_API_KEY": WRITE_KEY,
                                       "STUBS_READ_KEY": READ_KEY}) is None


def test_stdio_main_refuses_the_write_key_at_none(capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(mcp_server, "_run_stdio", lambda server: ran.append(server))
    monkeypatch.setattr(mcp_server, "_default_client", None)
    assert mcp_server.main({"STUBS_READ_KEY": READ_KEY, "STUBS_API_KEY": WRITE_KEY}) == 2
    assert "STUBS_API_KEY must not be in" in capsys.readouterr().err and ran == []
    assert mcp_server.main({"STUBS_READ_KEY": READ_KEY}) == 0
    assert names(ran[0]) == READ_TOOLS and mcp_server._default_client.key_name == "STUBS_READ_KEY"
    assert mcp_server.main({"STUBS_MCP_WRITES": "import", "STUBS_API_KEY": WRITE_KEY}) == 0
    assert names(ran[1]) == READ_TOOLS | IMPORT_TOOLS and mcp_server._default_client.api_key == WRITE_KEY


EXPECTED_HINTS = {name: (True, False, True, False) for name in READ_TOOLS | {"list_imports"}}
EXPECTED_HINTS["import_shows"] = (False, False, False, False)  # each call adds a batch; it changes no show


def test_every_tool_carries_its_annotations():
    tools = {t.name: t for t in asyncio.run(mcp_server.build_server(writes="import").list_tools())}
    assert set(tools) == set(EXPECTED_HINTS) == set(mcp_server.TOOL_HINTS)
    for name, expected in EXPECTED_HINTS.items():
        wire = tools[name].annotations.model_dump(by_alias=True)
        assert (wire["readOnlyHint"], wire["destructiveHint"], wire["idempotentHint"], wire["openWorldHint"]) == \
            expected, name


@pytest.mark.parametrize("level", ["none", "import"])
def test_instructions_name_only_registered_tools(level):
    server = mcp_server.build_server(writes=level)
    registered = names(server)
    for tool in READ_TOOLS | IMPORT_TOOLS:
        if re.search(rf"\b{tool}\b", server.instructions):
            assert tool in registered, f"level {level}: the instructions name {tool}"
    assert "times_seen" in server.instructions and "song_count" in server.instructions
    assert ("import_shows" in server.instructions) is (level == "import")
    if level == "none":
        assert "read-only" in server.instructions


def test_import_shows_teaches_the_proposal_shape():
    tools = {t.name: t for t in asyncio.run(mcp_server.build_server(writes="import").list_tools())}
    doc = tools["import_shows"].description
    for needle in ("one show per night", "festival", "actually watched", '"confidence": "likely"', "confidence_note",
                   "sources", "acts", '"role": "opener"', "YYYY-MM-DD", "country", "review", "never applies",
                   "The Paper Lanterns"):
        assert needle in doc, needle
    example = json.loads(doc[doc.index("[\n"):doc.index("\n]") + 2])
    for show in example:  # the example itself is a valid batch
        assert set(show) <= {"date", "status", "billing", "acts", "venue", "city", "region", "country", "sources",
                             "notes", "tour", "run", "confidence", "confidence_note", "flags", "skip_reason"}


def test_the_example_in_the_description_is_accepted_by_the_api(importer, wire, con):
    tools = {t.name: t for t in asyncio.run(mcp_server.build_server(writes="import").list_tools())}
    doc = tools["import_shows"].description
    example = json.loads(doc[doc.index("[\n"):doc.index("\n]") + 2])
    out = call("import_shows", "import", label="Example from the description", shows=example)
    assert sum(out["counts"].values()) == len(example)


# ---------------------------------------------------------------- read tools
def test_reads_use_the_read_key_and_never_write(reader, wire, history):
    call("times_seen", artist="The Paper Lanterns")
    call("get_show", show_id=history["a"])
    call("search_shows", year=2009)
    assert wire.requests and all(auth == f"Bearer {READ_KEY}" for _, _, auth, _ in wire.requests)
    assert {m for m, _, _, _ in wire.requests} == {"GET"}


def test_find_artist_matches_names_and_aliases_with_counts(reader, history):
    out = call("find_artist", name="paper lanterns")
    assert [(a["id"], a["name"], a["times"], a["upcoming"]) for a in out["artists"]] == [
        (history["lanterns"], "The Paper Lanterns", 3, 1)]
    assert out["artists"][0]["by_role"] == {"headliner": 2, "opener": 1}
    assert (out["artists"][0]["first"], out["artists"][0]["last"]) == ("2009-05-04", "2021-06-05")
    assert [a["id"] for a in call("find_artist", name="Lanterns of Paper")["artists"]] == [history["lanterns"]]
    assert call("find_artist", name="Marigold Static")["artists"] == []


def test_times_seen_lists_every_attended_show_with_role_and_place(reader, history):
    out = call("times_seen", artist="The Paper Lanterns")
    assert out["artist"] == {"id": history["lanterns"], "name": "The Paper Lanterns"}
    assert out["times"] == 3
    assert [(s["show_id"], s["date"], s["venue"], s["city"], s["role"]) for s in out["shows"]] == [
        (history["c"], "2021-06-05", "Granite Hall", "Oakland", "headliner"),
        (history["b"], "2009-09-12", "The Tin Dome", "Portland", "opener"),
        (history["a"], "2009-05-04", "Granite Hall", "Oakland", "headliner")]
    assert [s["show_id"] for s in out["upcoming"]] == [history["up"]]


def test_times_seen_by_alias_and_by_id(reader, history):
    assert call("times_seen", artist="Lanterns of Paper")["times"] == 3
    assert call("times_seen", artist_id=history["harbor"])["times"] == 1


def test_an_ambiguous_name_lists_the_candidates(reader, history, con):
    other = mk_artist(con, "Paper Moon")
    text = call_error("times_seen", artist="paper")
    assert "Several artists match 'paper'" in text
    assert f"The Paper Lanterns (id {history['lanterns']})" in text and f"Paper Moon (id {other})" in text
    assert "artist_id" in text


def test_an_exact_name_wins_over_partial_matches(reader, history, con):
    mk_show(con, "Cold Harbor Revival", "2015-01-01", history["hall"])
    assert call("times_seen", artist="cold harbor")["artist"]["id"] == history["harbor"]


def test_an_unknown_artist_says_so(reader, history):
    assert call_error("times_seen", artist="Marigold Static") == \
        "Error executing tool times_seen: No artist in Stubs matches 'Marigold Static'."
    assert "No artist with id 999" in call_error("times_seen", artist_id=999)
    assert "Pass artist or artist_id" in call_error("times_seen")


def test_search_shows_by_year_artist_venue_and_city(reader, history, wire):
    by_year = call("search_shows", year=2009)
    assert [s["id"] for s in by_year["shows"]] == [history["b"], history["a"]] and by_year["count"] == 2
    assert by_year["shows"][0] == {"id": history["b"], "date": "2009-09-12", "status": "attended",
                                   "billing": "Cold Harbor", "venue": "The Tin Dome", "city": "Portland",
                                   "region": "OR", "country": "US", "headliners": ["Cold Harbor"], "run": None}
    assert [s["id"] for s in call("search_shows", venue="tin dome")["shows"]] == [history["up"], history["b"]]
    assert [s["id"] for s in call("search_shows", city="oakland", artist="The Paper Lanterns")["shows"]] == [
        history["c"], history["a"]]
    assert [s["id"] for s in call("search_shows", artist="Cold Harbor", year=2009)["shows"]] == [history["b"]]
    assert "/admin/api/shows?year=2009" in wire.paths()
    assert call("search_shows", year=1990) == {"count": 0, "shows": [], "truncated": False}


def test_search_shows_limit(reader, history):
    out = call("search_shows", limit=2)
    assert (out["count"], len(out["shows"]), out["truncated"]) == (4, 2, True)


def test_search_shows_ambiguous_and_unknown_venue(reader, history, con):
    mk_venue(con, "Granite Hall Annex")
    assert "Granite Hall" in call("search_shows", venue="Granite Hall")["shows"][0]["venue"]
    text = call_error("search_shows", venue="granite")
    assert "Several venues match 'granite'" in text and "venue_id" in text
    assert call_error("search_shows", venue="Velvet Basement").endswith("No venue in Stubs matches 'Velvet Basement'.")


def test_get_show_has_lineup_notes_sources_confidence_and_setlists(reader, history):
    show = call("get_show", show_id=history["a"])
    assert show["notes"] == "Front row, lost a shoe" and show["confidence"] == "confirmed"
    assert show["sources"] == []
    [act] = show["lineup"]
    assert act["name"] == "The Paper Lanterns" and act["role"] == "headliner"
    assert [g["title"] for g in act["setlist"]["sets"][0]["songs"]] == ["Lantern Light", "Paper Boats", "Night Ferry"]
    assert act["setlist"]["source_label"] == "fan club site"
    likely = call("get_show", show_id=history["c"])
    assert (likely["confidence"], likely["confidence_note"]) == ("likely", "Ticket stub says June; the day is a guess")
    assert call("get_show", show_id=history["up"])["status"] == "upcoming"


def test_get_show_unknown_id(reader, history):
    assert call_error("get_show", show_id=999) == "Error executing tool get_show: No show with id 999."


def test_get_stats(reader, history):
    stats = call("get_stats")
    assert stats["totals"]["shows"] == 3
    assert stats["top_artists"][0]["name"] == "The Paper Lanterns"


def test_upcoming_shows_and_did_you_go(reader, history, con, clock):
    passed = mk_show(con, "Marigold Static", "2026-10-01", history["hall"], status="upcoming")
    out = call("upcoming_shows")
    assert [(s["id"], s["passed"]) for s in out["shows"]] == [(passed, True), (history["up"], False)]


def test_to_confirm(reader, history):
    items = call("to_confirm")["items"]
    assert [(i["kind"], i["id"]) for i in items] == [("show", history["c"])]


def test_songs_heard(reader, history):
    out = call("songs_heard", artist="The Paper Lanterns")
    assert [(s["title"], s["plays"], s["shows"]) for s in out["songs"]] == [
        ("Lantern Light", 3, 3), ("Paper Boats", 2, 2), ("Night Ferry", 1, 1)]
    assert (out["songs_heard"], out["total_plays"]) == (3, 6)
    empty = call("songs_heard", artist="Cold Harbor")
    assert empty["songs"] == [] and "No setlists" in empty["message"]


def test_song_count(reader, history):
    out = call("song_count", artist="The Paper Lanterns", song="lantern light")
    assert out["found"] is True and out["song"]["title"] == "Lantern Light"
    assert (out["plays"], out["shows"], out["first"], out["last"]) == (3, 3, "2009-05-04", "2021-06-05")
    assert [n["date"] for n in out["nights"]] == ["2021-06-05", "2009-09-12", "2009-05-04"]
    assert out["nights"][1]["venue"] == "The Tin Dome"
    assert call("song_count", artist="The Paper Lanterns", song="Boats")["song"]["title"] == "Paper Boats"
    assert call("song_count", artist_id=history["lanterns"], song="Night Ferry")["plays"] == 1
    assert call_error("song_count", artist="The Paper Lanterns").endswith("Pass a song title.")


def test_song_count_never_heard_and_unknown(reader, history):
    catalog = call("song_count", artist="The Paper Lanterns", song="Candlewick")
    assert (catalog["found"], catalog["plays"], catalog["in_catalog"]) == (True, 0, True)
    missing = call("song_count", artist="The Paper Lanterns", song="Glass Harbor")
    assert (missing["found"], missing["plays"]) == (False, 0) and "stored setlists" in missing["message"]


def test_song_count_ambiguous(reader, history, con):
    text = call_error("song_count", artist="The Paper Lanterns", song="a")
    assert "Several of The Paper Lanterns' songs match 'a'" in text


def test_never_heard(reader, history):
    out = call("never_heard", artist="The Paper Lanterns")
    assert [s["title"] for s in out["never_heard"]] == ["Candlewick", "Ember Waltz"] and out["catalog_size"] == 3
    no_catalog = call("never_heard", artist="Cold Harbor")
    assert no_catalog["never_heard"] == [] and "No song catalog" in no_catalog["message"]


def test_no_tool_output_contains_a_companion(importer, history, wire):
    outputs = [
        call("find_artist", name="Lanterns"), call("times_seen", artist="The Paper Lanterns"),
        call("search_shows"), call("search_shows", year=2009), call("get_stats"), call("upcoming_shows"),
        call("to_confirm"), call("songs_heard", artist="The Paper Lanterns"),
        call("song_count", artist="The Paper Lanterns", song="Lantern Light"),
        call("never_heard", artist="The Paper Lanterns"),
        call("import_shows", "import", label="Ticket emails", shows=PROPOSALS[1:]),
        call("list_imports", "import"),
    ] + [call("get_show", show_id=history[k]) for k in ("a", "b", "c", "up")]
    assert COMPANION not in json.dumps(outputs)
    assert not [p for p in wire.paths() if "people" in p]


# ---------------------------------------------------------------- import tools
PROPOSALS = [
    {"date": "2012-08-18", "status": "attended", "billing": "Marigold Static", "venue": "Granite Hall", "city": "Oakland",
     "region": "CA", "country": "US", "sources": [{"label": "Ticket email", "url": "https://tickets.example.com/o/1"}]},
    {"date": "2009-05-04", "status": "attended", "billing": "The Paper Lanterns", "venue": "Granite Hall",
     "city": "Oakland"},
]


def test_import_shows_creates_a_pending_batch_and_applies_nothing(importer, history, wire, con):
    before = con.execute("SELECT COUNT(*) FROM show").fetchone()[0]
    out = call("import_shows", "import", label="Ticket emails 2009-2012", shows=PROPOSALS)
    assert out["counts"] == {"new": 1, "have": 1, "maybe": 0, "skip": 0}
    assert out["review_link"] == f"/admin#/admin/import/{out['batch_id']}"
    assert "nothing" in out["note"].lower() and "review" in out["note"].lower()
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == before
    batch = con.execute("SELECT origin, label, status FROM import_batch WHERE id = ?", (out["batch_id"],)).fetchone()
    assert tuple(batch) == ("api", "Ticket emails 2009-2012", "pending")
    posts = [(p, auth, body) for m, p, auth, body in wire.requests if m != "GET"]
    assert posts == [("/admin/api/imports", f"Bearer {WRITE_KEY}", {"label": "Ticket emails 2009-2012",
                                                                     "shows": PROPOSALS})]


def test_list_imports(importer, history):
    first = call("import_shows", "import", label="Ticket emails", shows=PROPOSALS[:1])
    batches = call("list_imports", "import")["batches"]
    assert [(b["id"], b["label"], b["status"], b["review_link"]) for b in batches] == [
        (first["batch_id"], "Ticket emails", "pending", f"/admin#/admin/import/{first['batch_id']}")]


def test_a_bad_proposal_returns_the_apis_400_message(importer, history, con):
    text = call_error("import_shows", "import", label="Ticket emails",
                      shows=[PROPOSALS[0], {"billing": "Cold Harbor", "venue": "Granite Hall", "city": "Oakland"}])
    assert text == ("Error executing tool import_shows: Stubs refused the import, and stored nothing: "
                    "Row 2: date is required.")
    assert con.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0


def test_an_empty_batch_is_refused_by_the_api(importer, history):
    assert call_error("import_shows", "import", label="Nothing", shows=[]).endswith("No shows to import.")


def test_the_read_key_cannot_import_even_if_the_tool_were_called(reader, history, wire, con):
    with pytest.raises(mcp_server.StubsError) as e:
        mcp_server.import_shows("Sneaky", PROPOSALS)
    assert str(e.value) == "Stubs rejected the service's key (HTTP 403). Check STUBS_READ_KEY on the Stubs host."
    assert con.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0


# ---------------------------------------------------------------- the client's own HTTP path
UNREACHABLE = "Stubs is unreachable right now. Try again in a minute."


def http_error(code, body):
    return urllib.error.HTTPError("http://127.0.0.1:8773/admin/api/upcoming", code, "x", {}, io.BytesIO(body.encode()))


def raising(exc):
    def fake(req, timeout=None):
        raise exc
    return fake


ERROR_CASES = {
    "refused": (urllib.error.URLError(ConnectionRefusedError(111, "refused")), UNREACHABLE),
    "timeout": (socket.timeout("timed out"), UNREACHABLE),
    "401": (http_error(401, '{"error": "Sign in to edit"}'),
            "Stubs rejected the service's key (HTTP 401). Check STUBS_READ_KEY on the Stubs host."),
    "500html": (http_error(500, "<!doctype html><html>Traceback</html>"), "Stubs API error (500)"),
    "502json": (http_error(502, '{"error": "Lookup failed"}'), "Stubs API error (502): Lookup failed"),
}


@pytest.mark.parametrize("case", ERROR_CASES)
def test_http_errors_become_short_messages(monkeypatch, case):
    exc, expected = ERROR_CASES[case]
    monkeypatch.setattr(urllib.request, "urlopen", raising(exc))
    client = mcp_server.client_for({"STUBS_READ_KEY": READ_KEY})
    with pytest.raises(mcp_server.StubsError) as e:
        client.get("/admin/api/upcoming")
    assert str(e.value) == expected
    assert READ_KEY not in str(e.value) and "<" not in str(e.value) and "127.0.0.1" not in str(e.value)


def test_a_hit_sends_the_key_ua_and_timeout(monkeypatch):
    seen = []

    def fake(req, timeout=None):
        seen.append((req.full_url, req.get_method(), req.get_header("Authorization"), req.get_header("User-agent"),
                     timeout))
        return io.BytesIO(b'{"shows": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    client = mcp_server.client_for({"STUBS_READ_KEY": READ_KEY, "STUBS_URL": "http://127.0.0.1:8800/"})
    assert client.get("/admin/api/upcoming", year=2009, city=None) == {"shows": []}
    assert seen == [("http://127.0.0.1:8800/admin/api/upcoming?year=2009", "GET", f"Bearer {READ_KEY}",
                     "stubs-mcp/1.0", 15)]


def test_query_values_are_encoded(wire, reader, history):
    call("search_shows", city="Saint-Étienne & Co")
    query = urlsplit(wire.paths()[-1]).query
    assert parse_qs(query) == {"city": ["Saint-Étienne & Co"]}


def test_stubs_error_is_a_tool_error_and_a_runtime_error():
    assert issubclass(mcp_server.StubsError, mcp_server.ToolError)
    assert issubclass(mcp_server.StubsError, RuntimeError)


@pytest.mark.parametrize("s", ["The Paper Lanterns", "  Jack’s   BAND ", "Ｃｏｌｄ Harbor", "“Quoted”", "", None])
def test_local_norm_matches_the_app(s):
    assert mcp_server._norm(s) == common.norm(s)
    assert mcp_server._venue_norm(s) == common.venue_norm(s)


def test_mcp_server_never_imports_the_app():
    source = open(mcp_server.__file__, encoding="utf-8").read()
    for module in ("app", "settings", "common", "db", "records", "browse", "people", "flask", "sqlite3"):
        assert not re.search(rf"^\s*(import|from) {module}\b", source, re.M), module
