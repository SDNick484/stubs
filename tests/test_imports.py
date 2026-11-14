"""Task 4: import batches (imports.py): validate, match (spec 7.3 step 2), review edits and apply. Review focus 2:
Apply pressed twice (a double click, or the review open in two tabs) creates the shows once; the second is 409."""
import os
import threading

import pytest

import db
import imports
import records
import settings
from common import ApiError
from conftest import mk_artist, mk_show, mk_venue
from csvimport import parse_csv
from imports import (apply_batch, batch_view, create_batch, discard_batch, match, pending_batches, update_row,
                     validate_proposal)

with open(os.path.join(os.path.dirname(__file__), "fixtures", "concerts.csv"), "rb") as _f:
    FIXTURE = _f.read()


# ---------------------------------------------------------------- helpers (this file only)
def prop(billing="Cold Harbor", date="2016-12-01", venue="The Bellwether", city="Oakland", region="CA", country="US",
         acts=None, **kw):
    """A valid API-style proposal; validate_proposal fills in the rest."""
    p = {"date": date, "status": "attended", "billing": billing, "venue": venue, "city": city, "region": region,
         "country": country, "acts": acts if acts is not None else [{"name": billing, "role": "headliner"}]}
    p.update(kw)
    return p


def row(view, row_no):
    return next(r for r in view["rows"] if r["row_no"] == row_no)


def count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def show_by(con, billing, date):
    return con.execute("SELECT * FROM show WHERE billing = ? AND date = ?", (billing, date)).fetchone()


def status_of(con, batch_id):
    return con.execute("SELECT status FROM import_batch WHERE id = ?", (batch_id,)).fetchone()[0]


def raises(status, fn, *a, **kw):
    with pytest.raises(ApiError) as e:
        fn(*a, **kw)
    assert e.value.status == status, e.value.message
    return e.value.message


@pytest.fixture
def fixture_batch(con):
    return create_batch(con, "csv", "CSV import 2026-11-14", parse_csv(FIXTURE))


# ---------------------------------------------------------------- create and match
def test_fixture_counts(con, fixture_batch):
    view = batch_view(con, fixture_batch)
    assert view["counts"] == {"new": 11, "have": 1, "maybe": 0, "skip": 3}
    assert (view["id"], view["origin"], view["label"], view["status"]) == (
        fixture_batch, "csv", "CSV import 2026-11-14", "pending")
    assert [r["row_no"] for r in view["rows"]] == list(range(1, 16))
    assert {r["row_no"] for r in view["rows"] if r["verdict"] == "skip"} == {8, 9, 14}
    assert all(r["include"] is (r["verdict"] == "new") for r in view["rows"])
    assert set(view["rows"][0]) == {"id", "row_no", "verdict", "reason", "include", "parsed", "match", "differs"}


def test_second_row_matches_first_within_batch(con, fixture_batch):
    r2 = row(batch_view(con, fixture_batch), 2)
    assert r2["verdict"] == "have" and r2["include"] is False
    assert "row 1" in r2["reason"]
    assert r2["match"] is None and r2["differs"] == []


def test_skip_rows_keep_their_reason(con, fixture_batch):
    view = batch_view(con, fixture_batch)
    assert row(view, 8)["reason"] == "not attended"
    assert row(view, 14)["reason"].startswith("bad date")


def test_apply_creates_shows_lineups_sources(con, fixture_batch):
    assert apply_batch(con, fixture_batch) == {"created": 11}
    assert count(con, "show") == 11
    assert con.execute("SELECT COUNT(*) FROM artist WHERE norm=?", ("ruth's garden",)).fetchone()[0] == 1
    assert count(con, "venue") == 7  # "The Bellwether" and "Bellwether" are one venue
    # every show has the batch label as a source
    for s in con.execute("SELECT id FROM show"):
        labels = [r[0] for r in con.execute("SELECT label FROM source WHERE show_id = ? AND lineup_id IS NULL", (s[0],))]
        assert "CSV import 2026-11-14" in labels
    year_only = show_by(con, "Velvet Arcade", "2027")
    assert (year_only["status"], year_only["date_precision"], year_only["sort_date"]) == ("upcoming", "year", "2027-01-01")
    assert show_by(con, "Juniper & The Owls", "2026-12-05")["status"] == "upcoming"

    s4 = show_by(con, "Cold Harbor + Velvet Arcade", "2009-06-20")
    lineup = con.execute("SELECT l.id, a.name, l.role, l.position FROM lineup l JOIN artist a ON a.id = l.artist_id "
                         "WHERE l.show_id = ? ORDER BY l.position", (s4["id"],)).fetchall()
    assert [(r["name"], r["role"]) for r in lineup] == [
        ("Cold Harbor", "coheadliner"), ("Velvet Arcade", "coheadliner"), ("Ruth’s Garden", "opener")]
    assert [r[0] for r in con.execute("SELECT label FROM source WHERE lineup_id = ?", (lineup[2]["id"],))] == ["setlist.fm"]
    assert sorted(r[0] for r in con.execute("SELECT label FROM source WHERE show_id = ? AND lineup_id IS NULL",
                                            (s4["id"],))) == ["CSV import 2026-11-14", "Ticketmaster"]

    s6 = show_by(con, "Juniper & The Owls", "2011-09-09")
    opener = con.execute("SELECT confidence, confidence_note FROM lineup WHERE show_id = ? AND role = 'opener'",
                         (s6["id"],)).fetchone()
    assert tuple(opener) == ("likely", "tour-wide support; night not confirmed")
    fest = show_by(con, "Lantern Fest", "2007-08-11")
    assert fest["notes"] == "Day 1"
    assert show_by(con, "Marigold Static", "2015-10-10")["confidence"] == "likely"
    assert show_by(con, "The Paper Lanterns", "2005-04-02")["venue_label"] == "The Bellwether"
    assert show_by(con, "The Paper Lanterns", "2005-04-03") is None  # row 2 is "have"
    assert status_of(con, fixture_batch) == "applied"
    assert con.execute("SELECT applied_at FROM import_batch WHERE id = ?", (fixture_batch,)).fetchone()[0]


def test_reimport_adds_nothing(con, fixture_batch):
    apply_batch(con, fixture_batch)
    b2 = create_batch(con, "csv", "t2", parse_csv(FIXTURE))
    assert batch_view(con, b2)["counts"] == {"new": 0, "have": 12, "maybe": 0, "skip": 3}
    assert apply_batch(con, b2) == {"created": 0}
    assert count(con, "show") == 11


def test_have_row_lists_differences_and_is_never_applied(con, fixture_batch):
    apply_batch(con, fixture_batch)
    sid = show_by(con, "Cold Harbor", "2016-12-01")["id"]
    con.execute("UPDATE show SET tour = 'Hand Tour' WHERE id = ?", (sid,))
    b2 = create_batch(con, "csv", "t2", parse_csv(FIXTURE))
    view = batch_view(con, b2)
    r11 = row(view, 11)
    assert (r11["verdict"], r11["include"], r11["differs"]) == ("have", False, ["tour"])
    assert r11["match"] == {"id": sid, "date": "2016-12-01", "billing": "Cold Harbor", "venue_label": "The Bellwether"}
    assert row(view, 1)["differs"] == []                    # unchanged since the first import
    assert "date" in row(view, 2)["differs"]                # matched to row 1's show, a day apart
    apply_batch(con, b2)
    assert con.execute("SELECT tour FROM show WHERE id = ?", (sid,)).fetchone()[0] == "Hand Tour"
    assert count(con, "show") == 11


def test_maybe_when_venue_differs_same_city(con):
    v = mk_venue(con, "Fox Hall", "Oakland")
    sid = mk_show(con, "Cold Harbor", "2016-12-02", v)
    b = create_batch(con, "api", "t", [prop()])
    r = batch_view(con, b)["rows"][0]
    assert (r["verdict"], r["include"], r["match"]["id"]) == ("maybe", False, sid)
    assert "Cold Harbor" in r["reason"]


def test_same_city_without_shared_artist_is_new(con):
    v = mk_venue(con, "Fox Hall", "Oakland")
    mk_show(con, "Marigold Static", "2016-12-01", v)
    b = create_batch(con, "api", "t", [prop()])
    assert batch_view(con, b)["counts"]["new"] == 1


def test_two_days_apart_is_new(con):
    v = mk_venue(con, "The Bellwether")
    mk_show(con, "Cold Harbor", "2016-12-03", v)
    assert match(con, validate_proposal(prop()))[0] == "new"


def test_year_precision_maybe(con):
    v = mk_venue(con, "Old Mill Theatre", "Portland", "OR")
    sid = mk_show(con, "Velvet Arcade", "2027", v, status="upcoming")
    p = prop("Velvet Arcade", "2027-03-14", "Old Mill Theatre", "Portland", "OR")
    assert match(con, validate_proposal(p))[:2] == ("maybe", sid)
    b = create_batch(con, "api", "t", [p])
    assert batch_view(con, b)["rows"][0]["verdict"] == "maybe"


def test_year_only_against_january_first_is_maybe_not_have(con):
    # Both sort_dates are 2027-01-01, but "some time in 2027" is not the same night as 1 January.
    v = mk_venue(con, "Old Mill Theatre", "Portland", "OR")
    sid = mk_show(con, "Velvet Arcade", "2027", v, status="upcoming")
    p = prop("Velvet Arcade", "2027-01-01", "Old Mill Theatre", "Portland", "OR")
    assert match(con, validate_proposal(p))[:2] == ("maybe", sid)


def test_month_precision_have_when_same_month(con):
    v = mk_venue(con, "Old Mill Theatre", "Portland", "OR")
    sid = mk_show(con, "Velvet Arcade", "2025-07", v)
    p = prop("Velvet Arcade", "2025-07", "Old Mill Theatre", "Portland", "OR")
    assert match(con, validate_proposal(p))[:2] == ("have", sid)


def test_renamed_venue_still_matches(con, fixture_batch):
    apply_batch(con, fixture_batch)
    vid = records.find_venue(con, "The Bellwether", "Oakland")
    con.execute("UPDATE venue SET name = 'Bellwether Ballroom' WHERE id = ?", (vid,))
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, 'Bellwether Ballroom', 'bellwether ballroom')",
                (vid,))
    b2 = create_batch(con, "csv", "t2", parse_csv(FIXTURE))
    view = batch_view(con, b2)
    assert view["counts"] == {"new": 0, "have": 12, "maybe": 0, "skip": 3}
    assert row(view, 11)["match"]["venue_label"] == "The Bellwether"


def test_artist_alias_matches(con):
    v = mk_venue(con, "The Bellwether")
    sid = mk_show(con, "Cold Harbor", "2016-12-01", v)
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('cold harbour', ?)", (mk_artist(con, "Cold Harbor"),))
    assert match(con, validate_proposal(prop("Cold Harbour")))[:2] == ("have", sid)


def test_ticking_a_maybe_creates_a_new_show(con):
    v = mk_venue(con, "Fox Hall", "Oakland")
    mk_show(con, "Cold Harbor", "2016-12-02", v)
    b = create_batch(con, "api", "t", [prop()])
    rid = batch_view(con, b)["rows"][0]["id"]
    assert update_row(con, rid, include=True)["include"] is True
    assert apply_batch(con, b) == {"created": 1}
    assert count(con, "show") == 2
    assert count(con, "artist") == 1


def test_unticked_new_row_is_not_applied(con, fixture_batch):
    r1 = row(batch_view(con, fixture_batch), 1)
    update_row(con, r1["id"], include=False)
    assert apply_batch(con, fixture_batch) == {"created": 10}
    assert show_by(con, "The Paper Lanterns", "2005-04-02") is None


def test_cannot_include_have_or_skip(con, fixture_batch):
    view = batch_view(con, fixture_batch)
    raises(400, update_row, con, row(view, 2)["id"], include=True)
    raises(400, update_row, con, row(view, 8)["id"], include=True)
    raises(400, update_row, con, row(view, 1)["id"], include="yes")
    assert row(batch_view(con, fixture_batch), 2)["include"] is False


def test_editing_a_row_rematches_it(con, fixture_batch):
    view = batch_view(con, fixture_batch)
    # fixing row 14's bad date makes it a new show, ticked
    fixed = {**row(view, 14)["parsed"], "date": "2018-03-04", "skip_reason": None, "status": "attended"}
    r = update_row(con, row(view, 14)["id"], parsed=fixed)
    assert (r["verdict"], r["include"], r["parsed"]["date"], r["row_no"]) == ("new", True, "2018-03-04", 14)
    # moving row 3 onto row 1's night and billing makes it the same show as row 1
    same = {**row(view, 3)["parsed"], "date": "2005-04-02", "venue": "The Bellwether", "city": "Oakland",
            "billing": "The Paper Lanterns"}
    r = update_row(con, row(view, 3)["id"], parsed=same)
    assert (r["verdict"], r["include"]) == ("have", False) and "row 1" in r["reason"]
    assert batch_view(con, fixture_batch)["counts"] == {"new": 11, "have": 2, "maybe": 0, "skip": 2}
    raises(400, update_row, con, row(view, 1)["id"], parsed={**row(view, 1)["parsed"], "date": "next week"})


def test_update_row_unknown_is_404(con):
    raises(404, update_row, con, 999, include=True)


# ---------------------------------------------------------------- apply once (review focus 2)
def test_second_apply_is_409_and_creates_nothing(con, fixture_batch):
    assert apply_batch(con, fixture_batch) == {"created": 11}
    before = [count(con, t) for t in ("show", "lineup", "source", "artist", "venue")]
    assert raises(409, apply_batch, con, fixture_batch) == "This import was already applied."
    assert [count(con, t) for t in ("show", "lineup", "source", "artist", "venue")] == before
    assert status_of(con, fixture_batch) == "applied"


def test_concurrent_applies_create_once(con, fixture_batch):
    """Two requests (two tabs) applying at the same moment, each on its own connection."""
    barrier, results = threading.Barrier(2), []

    def worker():
        c = db.connect(settings.DB_PATH)
        try:
            barrier.wait()
            results.append(apply_batch(c, fixture_batch))
        except ApiError as e:
            results.append(e.status)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [409, {"created": 11}]
    assert count(con, "show") == 11


def test_apply_is_one_transaction(con, fixture_batch, monkeypatch):
    real, calls = records.add_source, []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 6:
            raise ApiError(400, "boom")
        return real(*a, **kw)

    monkeypatch.setattr(records, "add_source", flaky)
    raises(400, apply_batch, con, fixture_batch)
    assert [count(con, t) for t in ("show", "lineup", "source", "artist", "venue", "run")] == [0] * 6
    assert status_of(con, fixture_batch) == "pending"
    monkeypatch.setattr(records, "add_source", real)
    assert apply_batch(con, fixture_batch) == {"created": 11}


def test_two_pending_batches_of_one_file_create_once(con):
    """An upload sent twice (double click on Upload) gives two pending batches; applying both adds nothing twice."""
    b1 = create_batch(con, "csv", "a", parse_csv(FIXTURE))
    b2 = create_batch(con, "csv", "b", parse_csv(FIXTURE))
    assert apply_batch(con, b1) == {"created": 11}
    assert apply_batch(con, b2) == {"created": 0}
    assert count(con, "show") == 11
    assert batch_view(con, b2)["counts"]["have"] == 12


def test_rows_frozen_after_apply(con, fixture_batch):
    apply_batch(con, fixture_batch)
    raises(409, update_row, con, row(batch_view(con, fixture_batch), 1)["id"], include=False)
    raises(409, discard_batch, con, fixture_batch)


def test_discard(con, fixture_batch):
    assert [b["id"] for b in pending_batches(con)] == [fixture_batch]
    discard_batch(con, fixture_batch)
    assert status_of(con, fixture_batch) == "discarded"
    assert pending_batches(con) == []
    assert raises(409, apply_batch, con, fixture_batch) == "This import was discarded."
    assert count(con, "show") == 0
    raises(404, discard_batch, con, 999)
    raises(404, apply_batch, con, 999)
    raises(404, batch_view, con, 999)


def test_pending_batches_shape(con, fixture_batch):
    [b] = pending_batches(con)
    assert set(b) == {"id", "origin", "label", "status", "created_at", "counts"}
    assert b["counts"] == {"new": 11, "have": 1, "maybe": 0, "skip": 3}


# ---------------------------------------------------------------- proposals
def test_validate_fills_defaults():
    p = validate_proposal({"date": " 2025-07-19 ", "status": "attended", "billing": "Cold Harbor",
                           "venue": "The Bellwether", "city": "Oakland"})
    assert p == {"row_no": None, "date": "2025-07-19", "status": "attended", "billing": "Cold Harbor",
                 "acts": [{"name": "Cold Harbor", "role": "headliner", "confidence": "confirmed",
                           "confidence_note": None, "sources": []}],
                 "venue": "The Bellwether", "city": "Oakland", "region": None, "country": None, "sources": [],
                 "notes": None, "tour": None, "run": None, "confidence": "confirmed", "confidence_note": None,
                 "flags": [], "skip_reason": None}


def test_validate_accepts_a_parsed_csv_row():
    for p in parse_csv(FIXTURE):
        assert validate_proposal(p) == p


@pytest.mark.parametrize("change, words", [
    ({"date": "next week"}, "date"),
    ({"date": None}, "date"),
    ({"status": "missed"}, "status"),
    ({"billing": ""}, "billing"),
    ({"billing": "x" * 301}, "billing"),
    ({"venue": None}, "venue"),
    ({"city": 5}, "city"),
    ({"country": "France"}, "country"),
    ({"acts": "Cold Harbor"}, "acts"),
    ({"acts": [{"name": "Cold Harbor", "role": "drummer"}]}, "role"),
    ({"acts": [{"name": "Cold Harbor"}, {"name": "cold  harbor"}]}, "twice"),
    ({"acts": [{"role": "headliner"}]}, "name"),
    ({"sources": [{"label": "x", "url": "javascript:alert(1)"}]}, "url"),
    ({"sources": [{"url": "https://concerts.example.com/"}]}, "label"),
    ({"confidence": "maybe"}, "confidence"),
    ({"flags": "check acts"}, "flags"),
])
def test_validate_rejects_bad_proposals(change, words):
    msg = raises(400, validate_proposal, prop(**change))
    assert words in msg.lower()


def test_validate_rejects_non_object():
    raises(400, validate_proposal, ["Cold Harbor"])


def test_skip_rows_need_little():
    p = validate_proposal({"billing": "", "skip_reason": "no artist", "date": None, "status": None})
    assert p["skip_reason"] == "no artist" and p["acts"] == []


def test_source_as_plain_text():
    p = validate_proposal(prop(sources=["Ticketmaster", {"label": "Venue page", "url": "https://concerts.example.com/x"}]))
    assert p["sources"] == [{"label": "Ticketmaster", "url": None},
                            {"label": "Venue page", "url": "https://concerts.example.com/x"}]


def test_create_batch_names_the_bad_row(con):
    msg = raises(400, create_batch, con, "api", "t", [prop(), prop(date="next week")])
    assert msg.startswith("Row 2: ")
    assert count(con, "import_batch") == 0
    raises(400, create_batch, con, "api", "t", [])
    raises(400, create_batch, con, "api", "", [prop()])


def test_api_rows_are_numbered_by_position(con):
    b = create_batch(con, "api", "t", [prop(row_no=7), prop("Marigold Static", row_no=7)])
    assert [r["row_no"] for r in batch_view(con, b)["rows"]] == [1, 2]


def test_apply_api_batch_with_run_tour_and_alias_duplicate(con):
    a = mk_artist(con, "Cold Harbor")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('cold harbour', ?)", (a,))
    p = prop("Harbor Days", "2009-08-01", "Harbor Green", "San Francisco", run="Harbor Days 2009", tour="Summer",
             acts=[{"name": "Cold Harbor", "role": "performer", "sources": [{"label": "poster"}]},
                   {"name": "Cold Harbour", "role": "performer", "sources": [{"label": "wristband"}]}],
             sources=[{"label": "Venue page", "url": "https://concerts.example.com/hd"}])
    b = create_batch(con, "api", "Claude finds 2026-11-14", [p])
    assert apply_batch(con, b) == {"created": 1}
    s = show_by(con, "Harbor Days", "2009-08-01")
    assert (s["tour"], con.execute("SELECT name FROM run WHERE id = ?", (s["run_id"],)).fetchone()[0]) == (
        "Summer", "Harbor Days 2009")
    assert count(con, "lineup") == 1  # two spellings of one artist: one entry with both sources
    assert sorted(r[0] for r in con.execute("SELECT label FROM source WHERE lineup_id IS NOT NULL")) == [
        "poster", "wristband"]
    assert sorted(r[0] for r in con.execute("SELECT label FROM source WHERE lineup_id IS NULL")) == [
        "Claude finds 2026-11-14", "Venue page"]


def test_imports_module_never_imports_app():
    with open(imports.__file__, encoding="utf-8") as f:
        assert "import app" not in f.read()
