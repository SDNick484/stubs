"""Task 13: the to-confirm list."""
import pytest

import confirm
from common import ApiError
from conftest import mk_show, mk_venue


@pytest.fixture
def seeded(con):
    v = mk_venue(con, "Granite Hall")
    old = mk_show(con, "The Paper Lanterns", "2015-03-02", v, lineup=(("The Paper Lanterns", "headliner"),
                                                                       ("Ninefold", "opener")))
    con.execute("UPDATE lineup SET confidence = 'likely', confidence_note = 'maybe them' "
                "WHERE show_id = ? AND role = 'opener'", (old,))
    likely = mk_show(con, "Marigold Static", "2012-06", v, confidence="likely", confidence_note="date from memory")
    future = mk_show(con, "Cold Harbor", "2027-01-05", v, status="upcoming", confidence="likely")
    sure = mk_show(con, "Quiet Maps", "2018-01-01", v)
    opener = con.execute("SELECT id FROM lineup WHERE show_id = ? AND role = 'opener'", (old,)).fetchone()["id"]
    return {"old": old, "likely": likely, "future": future, "sure": sure, "opener": opener}


def test_lists_likely_oldest_first_with_notes(con, seeded):
    items = confirm.to_confirm(con)
    assert [(i["kind"], i["show"]["id"]) for i in items] == [
        ("show", seeded["likely"]), ("lineup", seeded["old"]), ("show", seeded["future"])]
    assert items[0]["note"] == "date from memory" and items[0]["artist"] is None
    assert items[1]["artist"] == "Ninefold" and items[1]["note"] == "maybe them"
    assert items[1]["id"] == seeded["opener"]
    assert items[0]["show"]["precision"] == "month"


def test_confirm_lineup_adds_source_and_clears(con, seeded):
    v0 = con.execute("SELECT version FROM show WHERE id = ?", (seeded["old"],)).fetchone()["version"]
    confirm.confirm(con, "lineup", seeded["opener"], "stub scan", "https://concerts.example.com/s")
    assert [i["id"] for i in confirm.to_confirm(con) if i["kind"] == "lineup"] == []
    src = con.execute("SELECT * FROM source WHERE show_id = ?", (seeded["old"],)).fetchall()
    assert len(src) == 1 and src[0]["lineup_id"] == seeded["opener"] and src[0]["label"] == "stub scan"
    row = con.execute("SELECT confidence, confidence_note FROM lineup WHERE id = ?", (seeded["opener"],)).fetchone()
    assert (row["confidence"], row["confidence_note"]) == ("confirmed", None)
    assert con.execute("SELECT version FROM show WHERE id = ?", (seeded["old"],)).fetchone()["version"] == v0 + 1


def test_confirm_show_without_label_adds_no_source(con, seeded):
    confirm.confirm(con, "show", seeded["likely"])
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    assert seeded["likely"] not in [i["show"]["id"] for i in confirm.to_confirm(con)]
    confirm.confirm(con, "show", seeded["future"], "ticket email")
    s = con.execute("SELECT * FROM source").fetchone()
    assert s["show_id"] == seeded["future"] and s["lineup_id"] is None


def test_errors(con, seeded):
    with pytest.raises(ApiError) as e:
        confirm.confirm(con, "show", 9999)
    assert e.value.status == 404
    with pytest.raises(ApiError) as e:
        confirm.confirm(con, "lineup", 9999)
    assert e.value.status == 404
    with pytest.raises(ApiError) as e:
        confirm.confirm(con, "venue", 1)
    assert e.value.status == 400


def test_routes(client, editor, con, seeded):
    assert client.get("/admin/api/to-confirm").status_code == 401
    assert client.post("/admin/api/to-confirm/confirm", json={"kind": "show", "id": 1}).status_code == 401
    items = editor.get("/admin/api/to-confirm").get_json()["items"]
    assert len(items) == 3
    post = lambda body: editor.post("/admin/api/to-confirm/confirm", json=body)  # noqa: E731
    assert post({"kind": "lineup", "id": seeded["opener"], "source_label": "stub scan"}).status_code == 200
    assert len(editor.get("/admin/api/to-confirm").get_json()["items"]) == 2
    assert post({"kind": "lineup", "id": 9999}).status_code == 404
    assert post({"kind": "nope", "id": 1}).status_code == 400
    assert post({"kind": "show", "id": "1"}).status_code == 400
    assert post({"kind": "show", "id": True}).status_code == 400
    assert post({"kind": "show", "id": seeded["likely"], "source_label": 5}).status_code == 400
