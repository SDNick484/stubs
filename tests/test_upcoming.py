"""Task 14: upcoming shows and "Did you go?"."""
import os

import pytest

import settings
from common import ApiError, today
from conftest import mk_show, mk_venue
from upcoming import due, resolve, upcoming


@pytest.fixture
def v(con):
    return mk_venue(con, "The Bellwether")


def test_day_show_is_due_the_day_after(con, clock, v):
    s = mk_show(con, "Juniper & The Owls", "2026-12-05", v, status="upcoming")
    clock.set("2026-12-05")
    assert due(con, today()) == []
    clock.set("2026-12-06")
    assert [r["id"] for r in due(con, today())] == [s]


def test_year_only_show_is_due_after_the_year(con, clock, v):
    s = mk_show(con, "Marigold Static", "2027", v, status="upcoming")
    clock.set("2027-12-31")
    assert due(con, today()) == []
    clock.set("2028-01-01")
    assert [r["id"] for r in due(con, today())] == [s]


def test_month_show_is_due_after_the_month(con, clock, v):
    s = mk_show(con, "Cold Harbor", "2027-02", v, status="upcoming")
    clock.set("2027-02-28")
    assert due(con, today()) == []
    clock.set("2027-03-01")
    assert [r["id"] for r in due(con, today())] == [s]


def test_attended_shows_are_never_listed(con, clock, v):
    mk_show(con, "The Paper Lanterns", "2020-01-01", v)
    assert due(con, "2030-01-01") == [] and upcoming(con) == []


def test_upcoming_lists_soonest_first_in_the_row_shape(con, v):
    b = mk_show(con, "B", "2027-05-01", v, status="upcoming")
    a = mk_show(con, "A", "2026-12-05", v, status="upcoming")
    rows = upcoming(con)
    assert [r["id"] for r in rows] == [a, b]
    assert rows[0] == {"id": a, "date": "2026-12-05", "precision": "day", "billing": "A", "venue_label": "The Bellwether"}


def test_yes_makes_it_attended_and_counted(editor, con, clock, v):
    clock.set("2026-12-06")
    s = mk_show(con, "Juniper & The Owls", "2026-12-05", v, status="upcoming")
    version = con.execute("SELECT version FROM show WHERE id = ?", (s,)).fetchone()["version"]
    assert editor.post(f"/admin/api/did-you-go/{s}", json={"went": True}).status_code == 200
    row = con.execute("SELECT status, version FROM show WHERE id = ?", (s,)).fetchone()
    assert row["status"] == "attended" and row["version"] == version + 1
    assert editor.get("/api/stats").get_json()["totals"]["shows"] == 1
    assert editor.get("/admin/api/did-you-go").get_json() == {"shows": []}


def test_no_deletes_it_and_its_images(editor, con, clock, v):
    clock.set("2026-12-06")
    s = mk_show(con, "Juniper & The Owls", "2026-12-05", v, status="upcoming")
    os.makedirs(settings.IMAGE_DIR, exist_ok=True)
    path = os.path.join(settings.IMAGE_DIR, "p.jpg")
    open(path, "wb").write(b"x")
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'poster', 'p.jpg')", (s,))
    assert editor.post(f"/admin/api/did-you-go/{s}", json={"went": False}).status_code == 200
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM image").fetchone()[0] == 0
    assert not os.path.exists(path)


def test_resolving_an_attended_show_is_409(editor, con, v):
    s = mk_show(con, "The Paper Lanterns", "2020-01-01", v)
    for went in (True, False):
        r = editor.post(f"/admin/api/did-you-go/{s}", json={"went": went})
        assert r.status_code == 409 and "error" in r.get_json()
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 1


def test_resolve_missing_show_is_404(con):
    with pytest.raises(ApiError) as e:
        resolve(con, 999, True)
    assert e.value.status == 404


def test_went_must_be_a_bool(editor, con, v):
    s = mk_show(con, "X", "2026-12-05", v, status="upcoming")
    assert editor.post(f"/admin/api/did-you-go/{s}", json={"went": "yes"}).status_code == 400
    assert editor.post(f"/admin/api/did-you-go/{s}", json={}).status_code == 400


def test_everything_is_editor_only(client, con, v):
    s = mk_show(con, "X", "2020-12-05", v, status="upcoming")
    assert client.get("/admin/api/did-you-go").status_code == 401
    assert client.get("/admin/api/upcoming").status_code == 401
    assert client.post(f"/admin/api/did-you-go/{s}", json={"went": True}).status_code == 401
    assert client.get(f"/api/shows/{s}").status_code == 404


def test_routes_list_for_the_editor(editor, con, clock, v):
    clock.set("2026-12-06")
    past = mk_show(con, "Past", "2026-12-05", v, status="upcoming")
    ahead = mk_show(con, "Ahead", "2027-01-05", v, status="upcoming")
    assert [r["id"] for r in editor.get("/admin/api/did-you-go").get_json()["shows"]] == [past]
    assert [r["id"] for r in editor.get("/admin/api/upcoming").get_json()["shows"]] == [past, ahead]
