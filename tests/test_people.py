"""Task 12: companions (people tagged on shows), editor-only."""
import pytest

import people
from conftest import mk_show, mk_venue
from common import ApiError


@pytest.fixture
def show(con):
    v = mk_venue(con, "Granite Hall")
    return mk_show(con, "The Paper Lanterns", "2019-06-01", v)


def test_create_twice_is_one_person(con):
    a = people.create_person(con, "Alex")
    assert people.create_person(con, "Alex") == a
    assert people.create_person(con, "  aLEX ") == a
    assert [p["name"] for p in people.list_people(con)] == ["Alex"]


def test_create_validates_name(con):
    with pytest.raises(ApiError) as e:
        people.create_person(con, "x" * 61)
    assert e.value.status == 400
    with pytest.raises(ApiError):
        people.create_person(con, "   ")
    assert people.create_person(con, "x" * 60)


def test_set_and_replace_companions(con, show):
    a, s, j = (people.create_person(con, n) for n in ("Alex", "Sam", "Jordan"))
    people.set_companions(con, show, [a, s])
    assert {p["name"] for p in people.companions(con, show)} == {"Alex", "Sam"}
    people.set_companions(con, show, [j, j])
    assert people.companions(con, show) == [{"id": j, "name": "Jordan"}]
    counts = {p["name"]: p["shows"] for p in people.list_people(con)}
    assert counts == {"Alex": 0, "Sam": 0, "Jordan": 1}
    people.set_companions(con, show, [])
    assert people.companions(con, show) == []


def test_unknown_person_rejected_and_nothing_changes(con, show):
    a = people.create_person(con, "Alex")
    people.set_companions(con, show, [a])
    with pytest.raises(ApiError) as e:
        people.set_companions(con, show, [a, 9999])
    assert e.value.status == 400
    assert people.companions(con, show) == [{"id": a, "name": "Alex"}]


def test_person_shows_newest_first(con):
    v = mk_venue(con, "Granite Hall")
    old = mk_show(con, "Cold Harbor", "2010-03", v)
    new = mk_show(con, "Marigold Static", "2020-08-09", v)
    a = people.create_person(con, "Riley")
    people.set_companions(con, old, [a])
    people.set_companions(con, new, [a])
    rows = people.person_shows(con, a)
    assert [r["id"] for r in rows] == [new, old]
    assert rows[0] == {"id": new, "date": "2020-08-09", "precision": "day", "billing": "Marigold Static",
                       "venue_label": "Granite Hall"}


def test_routes_need_an_editor(client):
    for method, path in (("get", "/admin/api/people"), ("post", "/admin/api/people"),
                         ("get", "/admin/api/shows/1/people"), ("put", "/admin/api/shows/1/people"),
                         ("get", "/admin/api/people/1/shows")):
        r = getattr(client, method)(path, json={"name": "Alex"} if method != "get" else None)
        assert r.status_code == 401, path


def test_routes_roundtrip(editor, show):
    r = editor.post("/admin/api/people", json={"name": "Alex"})
    assert r.status_code == 201
    pid = r.get_json()["id"]
    again = editor.post("/admin/api/people", json={"name": "alex"})
    assert again.status_code == 200 and again.get_json()["id"] == pid
    assert editor.post("/admin/api/people", json={}).status_code == 400
    assert editor.put(f"/admin/api/shows/{show}/people", json={"person_ids": [pid]}).status_code == 200
    assert editor.get(f"/admin/api/shows/{show}/people").get_json()["people"] == [{"id": pid, "name": "Alex"}]
    assert editor.get("/admin/api/people").get_json()["people"] == [{"id": pid, "name": "Alex", "shows": 1}]
    rows = editor.get(f"/admin/api/people/{pid}/shows").get_json()["shows"]
    assert [r["id"] for r in rows] == [show]


def test_route_errors(editor, show):
    assert editor.put(f"/admin/api/shows/{show}/people", json={"person_ids": [9999]}).status_code == 400
    assert editor.put(f"/admin/api/shows/{show}/people", json={"person_ids": "1"}).status_code == 400
    assert editor.put(f"/admin/api/shows/{show}/people", json={"person_ids": [True]}).status_code == 400
    assert editor.put(f"/admin/api/shows/{show}/people", json={}).status_code == 400
    assert editor.put("/admin/api/shows/9999/people", json={"person_ids": []}).status_code == 404
    assert editor.get("/admin/api/shows/9999/people").status_code == 404
    assert editor.get("/admin/api/people/9999/shows").status_code == 404


def test_read_key_cannot_write(client, show):
    c = client
    c.environ_base.update(HTTP_X_API_KEY="test-read-key")
    assert c.get("/admin/api/people").status_code == 200
    assert c.post("/admin/api/people", json={"name": "Sam"}).status_code == 403
    assert c.put(f"/admin/api/shows/{show}/people", json={"person_ids": []}).status_code == 403
