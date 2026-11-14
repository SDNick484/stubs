"""Task 11: show editing (spec 7.8, 7.10). editing.py's writers and the /admin/api/ routes that create, edit and delete
shows, set lineups, add and remove sources and list runs. Every save sends the show's version; a stale one is 409 and
writes nothing."""
import pytest

import editing
from common import ApiError
from conftest import READ_KEY, mk_artist, mk_show, mk_venue

STALE = "Someone else just changed this show. Reload to see it."


def detail(editor, sid):
    r = editor.get(f"/admin/api/shows/{sid}")
    assert r.status_code == 200
    return r.get_json()


def entry(e, **kw):
    """A lineup entry as the edit form sends it, from a show detail's lineup entry."""
    out = {"id": e["id"], "name": e["name"], "role": e["role"], "confidence": e["confidence"],
           "confidence_note": e["confidence_note"], "rating": e["rating"]}
    out.update(kw)
    return out


def lineup_rows(con, sid):
    return [tuple(r) for r in con.execute(
        "SELECT a.name, l.role, l.position, l.confidence, l.confidence_note, l.rating FROM lineup l "
        "JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? ORDER BY l.position", (sid,))]


@pytest.fixture
def show(con):
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "Marigold Static", "2019-05-04", v,
                   lineup=(("Marigold Static", "headliner"), ("Cold Harbor", "opener")), notes="with Alex")


# ---------------------------------------------------------------- versions
def test_stale_version_is_409_and_saves_nothing(editor, con, show):
    v1 = detail(editor, show)["version"]
    r = editor.patch(f"/admin/api/shows/{show}", json={"version": v1, "billing": "Marigold Static live"})
    assert r.status_code == 200 and r.get_json()["version"] == v1 + 1
    r = editor.patch(f"/admin/api/shows/{show}", json={"version": v1, "billing": "Second tab", "notes": "x",
                                                       "run": "Harbor Days 2019", "venue_label": "Granite Hall Annex"})
    assert r.status_code == 409 and r.get_json() == {"error": STALE}
    row = con.execute("SELECT billing, notes, run_id, version FROM show WHERE id = ?", (show,)).fetchone()
    assert tuple(row) == ("Marigold Static live", "with Alex", None, v1 + 1)
    assert con.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM venue_name").fetchone()[0] == 1
    d = detail(editor, show)
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": v1, "lineup": [entry(d["lineup"][0])]})
    assert r.status_code == 409 and r.get_json() == {"error": STALE}
    assert len(lineup_rows(con, show)) == 2


def test_a_merge_makes_an_open_form_stale(editor, con, show):
    v1 = detail(editor, show)["version"]
    other = mk_venue(con, "Granite Hall", city="Oakland")
    assert editor.post("/admin/api/venues/merge", json={"keep": other, "drop": 1}).status_code == 200
    r = editor.patch(f"/admin/api/shows/{show}", json={"version": v1, "tour": "Spring"})
    assert r.status_code == 409


def test_version_must_be_a_number(editor, show):
    for bad in (None, "1", True, 1.5):
        body = {"billing": "X"} if bad is None else {"version": bad, "billing": "X"}
        r = editor.patch(f"/admin/api/shows/{show}", json=body)
        assert r.status_code == 400, bad
    assert editor.patch("/admin/api/shows/999", json={"version": 1, "billing": "X"}).status_code == 404


# ---------------------------------------------------------------- show fields
def test_patch_fields(editor, con, show):
    v = detail(editor, show)["version"]
    r = editor.patch(f"/admin/api/shows/{show}", json={
        "version": v, "status": "upcoming", "date": "2027", "billing": "  Marigold Static  ", "tour": "Static Tour",
        "notes": "", "rating": 4, "confidence": "likely", "confidence_note": "date from memory", "run": "Harbor Days"})
    assert r.status_code == 200
    d = r.get_json()
    assert (d["status"], d["date"], d["precision"], d["sort_date"]) == ("upcoming", "2027", "year", "2027-01-01")
    assert (d["billing"], d["tour"], d["notes"], d["rating"]) == ("Marigold Static", "Static Tour", None, 4)
    assert (d["confidence"], d["confidence_note"], d["run"]) == ("likely", "date from memory", "Harbor Days")
    v = d["version"]
    d = editor.patch(f"/admin/api/shows/{show}", json={"version": v, "run": None, "rating": None}).get_json()
    assert d["run"] is None and d["rating"] is None
    assert con.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1  # runs are never cascaded


def test_run_reuses_an_existing_run(editor, con, show):
    rid = con.execute("INSERT INTO run (name) VALUES ('Harbor Days 2009')").lastrowid
    v = detail(editor, show)["version"]
    editor.patch(f"/admin/api/shows/{show}", json={"version": v, "run": "harbor days 2009"})
    assert con.execute("SELECT run_id FROM show WHERE id = ?", (show,)).fetchone()[0] == rid


@pytest.mark.parametrize("field,value", [
    ("rating", 0), ("rating", 6), ("rating", "3"), ("rating", True), ("rating", 2.5),
    ("status", "missed"), ("confidence", "maybe"), ("date", "2019-13-01"), ("date", "May 2019"),
    ("billing", ""), ("billing", "x" * 201), ("notes", "x" * 5001), ("venue_id", 999), ("color", "red"),
])
def test_bad_fields_are_400_and_save_nothing(editor, con, show, field, value):
    before = tuple(con.execute("SELECT * FROM show WHERE id = ?", (show,)).fetchone())
    r = editor.patch(f"/admin/api/shows/{show}", json={"version": 1, field: value})
    assert r.status_code == 400, r.get_json()
    assert "error" in r.get_json()
    assert tuple(con.execute("SELECT * FROM show WHERE id = ?", (show,)).fetchone()) == before


def test_rating_out_of_range_is_400(editor, con, show):
    for bad in (0, 6, -1):
        r = editor.patch(f"/admin/api/shows/{show}", json={"version": 1, "rating": bad})
        assert r.status_code == 400
    d = detail(editor, show)
    r = editor.put(f"/admin/api/shows/{show}/lineup",
                   json={"version": d["version"], "lineup": [entry(d["lineup"][0], rating=9)]})
    assert r.status_code == 400
    assert con.execute("SELECT rating FROM show WHERE id = ?", (show,)).fetchone()[0] is None
    assert len(lineup_rows(con, show)) == 2


# ---------------------------------------------------------------- venues
def test_venue_label_new_name_becomes_former_name(editor, con, show):
    vid = con.execute("SELECT venue_id FROM show WHERE id = ?", (show,)).fetchone()[0]
    r = editor.patch(f"/admin/api/shows/{show}", json={"version": 1, "venue_label": "Granite Pavilion"})
    assert r.status_code == 200
    assert r.get_json()["venue"]["label"] == "Granite Pavilion"
    names = editor.get(f"/admin/api/venues/{vid}").get_json()["names"]
    assert names == ["Granite Hall", "Granite Pavilion"]  # the current name is unchanged, the new one is former
    assert con.execute("SELECT name FROM venue WHERE id = ?", (vid,)).fetchone()[0] == "Granite Hall"
    # A name the venue already has (by venue_norm) is not added again.
    editor.patch(f"/admin/api/shows/{show}", json={"version": 2, "venue_label": "The Granite Pavilion"})
    assert con.execute("SELECT COUNT(*) FROM venue_name WHERE venue_id = ?", (vid,)).fetchone()[0] == 2


def test_moving_a_show_to_another_venue(editor, con, show):
    other = mk_venue(con, "The Bellwether")
    d = editor.patch(f"/admin/api/shows/{show}", json={"version": 1, "venue_id": other}).get_json()
    assert d["venue"]["id"] == other and d["venue"]["label"] == "The Bellwether"
    d = editor.patch(f"/admin/api/shows/{show}", json={"version": 2, "venue": "Rio Theatre", "city": "Santa Cruz",
                                                       "region": "CA", "country": "us"}).get_json()
    assert d["venue"]["label"] == "Rio Theatre" and d["venue"]["city"] == "Santa Cruz"
    assert d["venue"]["country"] == "US"


def test_create_show_with_new_venue(editor, con):
    r = editor.post("/admin/api/shows", json={
        "billing": "Cold Harbor", "date": "2024-08-17", "status": "attended",
        "venue": "The Rio Theatre", "city": "Santa Cruz", "region": "CA", "country": "US",
        "lineup": [{"name": "Cold Harbor", "role": "headliner"},
                   {"name": "Ruth's Garden", "role": "opener", "confidence": "likely", "confidence_note": "poster"}]})
    assert r.status_code == 201
    sid = r.get_json()["id"]
    d = detail(editor, sid)
    assert (d["billing"], d["date"], d["status"], d["version"]) == ("Cold Harbor", "2024-08-17", "attended", 1)
    assert d["venue"]["label"] == "The Rio Theatre" and d["venue"]["city"] == "Santa Cruz"
    assert lineup_rows(con, sid) == [("Cold Harbor", "headliner", 1, "confirmed", None, None),
                                     ("Ruth's Garden", "opener", 2, "likely", "poster", None)]
    # The same venue typed again (without "The") is found, not duplicated.
    r = editor.post("/admin/api/shows", json={"billing": "Cold Harbor", "date": "2025", "status": "upcoming",
                                              "venue": "Rio Theatre", "city": "santa cruz"})
    assert r.status_code == 201
    assert con.execute("SELECT COUNT(*) FROM venue").fetchone()[0] == 1
    d = detail(editor, r.get_json()["id"])
    assert [(e["name"], e["role"]) for e in d["lineup"]] == [("Cold Harbor", "headliner")]  # default lineup


def test_create_show_at_an_existing_venue(editor, con):
    v = mk_venue(con, "Granite Hall")
    r = editor.post("/admin/api/shows", json={"billing": "Harbor Days", "date": "2009-07", "venue_id": v,
                                              "venue_label": "Granite Hall Lawn", "run": "Harbor Days 2009",
                                              "notes": "with Sam", "rating": 5, "lineup": []})
    assert r.status_code == 201
    d = detail(editor, r.get_json()["id"])
    assert (d["status"], d["precision"], d["run"], d["notes"], d["rating"]) == \
        ("attended", "month", "Harbor Days 2009", "with Sam", 5)
    assert d["lineup"] == [] and d["venue"]["label"] == "Granite Hall Lawn"
    assert con.execute("SELECT COUNT(*) FROM venue_name WHERE venue_id = ?", (v,)).fetchone()[0] == 2


@pytest.mark.parametrize("body", [
    {"date": "2024-08-17", "venue_id": 1},                                      # no billing
    {"billing": "X", "venue_id": 1},                                            # no date
    {"billing": "X", "date": "2024", "venue_id": 99},                           # unknown venue
    {"billing": "X", "date": "2024"},                                           # no venue at all
    {"billing": "X", "date": "2024", "venue": "Rio Theatre"},                   # a new venue needs a city
    {"billing": "X", "date": "2024", "venue_id": 1, "status": "canceled"},
    {"billing": "X", "date": "2024", "venue_id": 1, "lineup": [{"name": "A", "role": "drummer"}]},
    {"billing": "X", "date": "2024", "venue_id": 1, "lineup": [{"name": "A", "role": "headliner"},
                                                             {"name": "a", "role": "opener"}]},
    {"billing": "X", "date": "2024", "venue": "Rio", "city": "Santa Cruz", "country": "USA"},
])
def test_create_refuses_bad_input_and_writes_nothing(editor, con, body):
    mk_venue(con, "Granite Hall")
    r = editor.post("/admin/api/shows", json=body)
    assert r.status_code == 400, r.get_json()
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM venue").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 0


# ---------------------------------------------------------------- lineups
def test_lineup_reorder_role_and_likely(editor, con, show):
    d = detail(editor, show)
    head, opener = d["lineup"]
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": d["version"], "lineup": [
        entry(opener, role="coheadliner", confidence="likely", confidence_note="only on the poster", rating=4),
        entry(head, role="coheadliner"),
    ]})
    assert r.status_code == 200
    out = r.get_json()
    assert out["version"] == d["version"] + 1
    assert [e["id"] for e in out["lineup"]] == [opener["id"], head["id"]]  # ids kept, order changed
    assert lineup_rows(con, show) == [("Cold Harbor", "coheadliner", 1, "likely", "only on the poster", 4),
                                      ("Marigold Static", "coheadliner", 2, "confirmed", None, None)]


def test_removing_a_lineup_entry_drops_its_sources(editor, con, show):
    d = detail(editor, show)
    head, opener = d["lineup"]
    r = editor.post(f"/admin/api/shows/{show}/sources", json={"label": "Poster", "lineup_id": opener["id"]})
    assert r.status_code == 201
    editor.post(f"/admin/api/shows/{show}/sources", json={"label": "Ticketmaster"})
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": d["version"], "lineup": [entry(head)]})
    assert r.status_code == 200
    assert lineup_rows(con, show) == [("Marigold Static", "headliner", 1, "confirmed", None, None)]
    assert [s[0] for s in con.execute("SELECT label FROM source WHERE show_id = ?", (show,))] == ["Ticketmaster"]
    assert con.execute("SELECT COUNT(*) FROM artist WHERE name = 'Cold Harbor'").fetchone()[0] == 1  # never cascaded


def test_new_lineup_name_resolves_existing_artist(editor, con, show):
    existing = mk_artist(con, "Ruth's Garden")
    d = detail(editor, show)
    lineup = [entry(e) for e in d["lineup"]] + [{"name": "  Ruth’s   Garden ", "role": "opener"}]
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": d["version"], "lineup": lineup})
    assert r.status_code == 200
    last = r.get_json()["lineup"][-1]
    assert last["artist_id"] == existing and last["name"] == "Ruth's Garden"
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 3


def test_renaming_entries_keeps_their_sources_even_when_swapped(editor, con, show):
    d = detail(editor, show)
    head, opener = d["lineup"]
    editor.post(f"/admin/api/shows/{show}/sources", json={"label": "Poster", "lineup_id": opener["id"]})
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": d["version"], "lineup": [
        entry(head, name="Cold Harbor"), entry(opener, name="Marigold Static")]})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()["lineup"]
    assert [(e["id"], e["name"]) for e in out] == [(head["id"], "Cold Harbor"), (opener["id"], "Marigold Static")]
    assert [s["label"] for s in out[1]["sources"]] == ["Poster"]


@pytest.mark.parametrize("lineup", [
    [{"name": "A", "role": "headliner"}, {"name": " a ", "role": "opener"}],  # one artist twice
    [{"name": "", "role": "headliner"}],
    [{"name": "A", "role": "headliner", "confidence": "sure"}],
    [{"name": "A", "role": "headliner", "id": 99999}],                         # an entry of no show, or another
    "Cold Harbor",
    [["Cold Harbor", "opener"]],
])
def test_bad_lineups_are_400_and_change_nothing(editor, con, show, lineup):
    before = lineup_rows(con, show)
    r = editor.put(f"/admin/api/shows/{show}/lineup", json={"version": 1, "lineup": lineup})
    assert r.status_code == 400, r.get_json()
    assert lineup_rows(con, show) == before
    assert con.execute("SELECT version FROM show WHERE id = ?", (show,)).fetchone()[0] == 1


def test_lineup_id_from_another_show_is_refused(editor, con, show):
    v = mk_venue(con, "The Bellwether")
    other = mk_show(con, "Cold Harbor", "2020-01-01", v)
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ?", (other,)).fetchone()[0]
    r = editor.put(f"/admin/api/shows/{show}/lineup",
                   json={"version": 1, "lineup": [{"id": lid, "name": "Cold Harbor", "role": "opener"}]})
    assert r.status_code == 400
    assert con.execute("SELECT show_id, role FROM lineup WHERE id = ?", (lid,)).fetchone()[:] == (other, "headliner")


def test_set_lineup_directly(con, show):
    lineup = [{"name": "Juniper & The Owls", "role": "performer"}]
    assert editing.set_lineup(con, show, lineup, 1) == 2
    assert lineup_rows(con, show) == [("Juniper & The Owls", "performer", 1, "confirmed", None, None)]
    with pytest.raises(ApiError) as e:
        editing.set_lineup(con, show, lineup, 1)
    assert e.value.status == 409


# ---------------------------------------------------------------- sources
def test_sources_add_and_remove(editor, con, show):
    r = editor.post(f"/admin/api/shows/{show}/sources",
                    json={"label": "Ticketmaster", "url": "https://concerts.example.com/t/1"})
    assert r.status_code == 201
    src = r.get_json()
    assert (src["label"], src["url"], src["lineup_id"]) == ("Ticketmaster", "https://concerts.example.com/t/1", None)
    assert detail(editor, show)["sources"] == [{"id": src["id"], "label": "Ticketmaster",
                                                "url": "https://concerts.example.com/t/1"}]
    assert detail(editor, show)["version"] == 1  # sources are not part of a form save
    r = editor.delete(f"/admin/api/sources/{src['id']}")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert detail(editor, show)["sources"] == []
    assert editor.delete(f"/admin/api/sources/{src['id']}").status_code == 404


@pytest.mark.parametrize("body", [
    {"label": ""}, {"url": "https://concerts.example.com/"}, {"label": "x" * 201},
    {"label": "Site", "url": "javascript:alert(1)"}, {"label": "Site", "url": "concerts.example.com/page"},
    {"label": "Site", "lineup_id": 99999}, {"label": "Site", "lineup_id": "1"},
])
def test_bad_sources_are_400(editor, con, show, body):
    r = editor.post(f"/admin/api/shows/{show}/sources", json=body)
    assert r.status_code == 400, r.get_json()
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0


def test_source_on_a_missing_show_is_404(editor):
    assert editor.post("/admin/api/shows/999/sources", json={"label": "Site"}).status_code == 404


def test_remove_source_directly(con, show):
    sid = con.execute("INSERT INTO source (show_id, label) VALUES (?, 'Last.fm')", (show,)).lastrowid
    editing.remove_source(con, sid)
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    with pytest.raises(ApiError) as e:
        editing.remove_source(con, sid)
    assert e.value.status == 404


# ---------------------------------------------------------------- runs and deleting
def test_runs_list(editor, con, show):
    v = mk_venue(con, "The Bellwether")
    rid = con.execute("INSERT INTO run (name) VALUES ('Harbor Days 2009')").lastrowid
    con.execute("INSERT INTO run (name) VALUES ('Autumn Stand')")
    mk_show(con, "Day one", "2009-07-01", v, run_id=rid)
    mk_show(con, "Day two", "2009-07-02", v, run_id=rid)
    r = editor.get("/admin/api/runs")
    assert r.status_code == 200
    assert [(x["name"], x["shows"]) for x in r.get_json()] == [("Autumn Stand", 0), ("Harbor Days 2009", 2)]


def test_delete_show(editor, con, show):
    editor.post(f"/admin/api/shows/{show}/sources", json={"label": "Ticketmaster"})
    r = editor.delete(f"/admin/api/shows/{show}")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert editor.get(f"/admin/api/shows/{show}").status_code == 404
    for table in ("show", "lineup", "source"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 2  # artists and venues stay
    assert con.execute("SELECT COUNT(*) FROM venue").fetchone()[0] == 1
    assert editor.delete(f"/admin/api/shows/{show}").status_code == 404


# ---------------------------------------------------------------- access
def test_editing_is_editor_only(client, con, show):
    calls = [("patch", f"/admin/api/shows/{show}", {"version": 1, "billing": "X"}),
             ("put", f"/admin/api/shows/{show}/lineup", {"version": 1, "lineup": []}),
             ("post", "/admin/api/shows", {"billing": "X", "date": "2024", "venue_id": 1}),
             ("post", f"/admin/api/shows/{show}/sources", {"label": "X"}),
             ("delete", "/admin/api/sources/1", None),
             ("delete", f"/admin/api/shows/{show}", None),
             ("get", "/admin/api/runs", None)]
    for method, url, body in calls:
        r = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
        assert r.status_code == 401, (method, url)
        assert r.get_json() == {"error": "Sign in to edit"}
    for method, url, body in calls[:-1]:
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(url, headers={"X-API-Key": READ_KEY}, **kw)
        assert r.status_code == 403, (method, url)
    assert client.get("/admin/api/runs", headers={"X-API-Key": READ_KEY}).status_code == 200
    assert con.execute("SELECT billing, version FROM show WHERE id = ?", (show,)).fetchone()[:] == \
        ("Marigold Static", 1)
