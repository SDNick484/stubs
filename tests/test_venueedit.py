"""Task 20: venue editing (venueedit.py) and its routes under /admin/api/venues/<id>."""
import pytest

import records
from conftest import READ_KEY, mk_show, mk_venue


def url(vid, tail=""):
    return f"/admin/api/venues/{vid}{tail}"


def names(con, vid):
    return sorted(r[0] for r in con.execute("SELECT name FROM venue_name WHERE venue_id = ?", (vid,)))


@pytest.fixture
def hall(con):
    vid = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.27)
    sid = mk_show(con, "The Paper Lanterns", "2019-05-04", vid)
    return vid, sid


def test_rename_keeps_old_name_and_labels(editor, con, hall):
    vid, sid = hall
    r = editor.patch(url(vid), json={"name": "Basalt Arena"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "Basalt Arena"
    assert [n["name"] for n in body["names"]] == ["Basalt Arena", "Granite Hall"]
    assert body["names"][0]["current"] and body["names"][0]["shows"] == 0
    assert body["names"][1]["shows"] == 1
    assert con.execute("SELECT venue_label FROM show WHERE id = ?", (sid,)).fetchone()[0] == "Granite Hall"
    assert records.find_venue(con, "Granite Hall", "Oakland") == vid
    assert records.find_venue(con, "Basalt Arena", "Oakland") == vid
    assert records.get_or_create_venue(con, "Granite Hall", "Oakland", "CA", "US") == vid


def test_rename_to_a_former_name_adds_nothing(editor, con, hall):
    vid, _ = hall
    editor.patch(url(vid), json={"name": "Basalt Arena"})
    editor.patch(url(vid), json={"name": "Granite Hall"})
    assert names(con, vid) == ["Basalt Arena", "Granite Hall"]
    assert con.execute("SELECT name FROM venue WHERE id = ?", (vid,)).fetchone()[0] == "Granite Hall"


def test_city_change_keeps_coordinates(editor, con, hall):
    vid, _ = hall
    body = editor.patch(url(vid), json={"city": "Berkeley", "region": "CA", "country": "us"}).get_json()
    assert body["city"] == "Berkeley" and body["country"] == "US"
    assert body["coordinates_kept"] is True and (body["lat"], body["lon"]) == (37.8, -122.27)
    assert editor.patch(url(vid), json={"region": None}).get_json()["coordinates_kept"] is True
    assert editor.patch(url(vid), json={}).get_json()["coordinates_kept"] is False
    no_coords = mk_venue(con, "Cinder Room")
    assert editor.patch(url(no_coords), json={"city": "Fresno"}).get_json()["coordinates_kept"] is False


@pytest.mark.parametrize("body", [{"name": ""}, {"city": " "}, {"name": 5}, {"country": "USA"}, {"colour": "red"},
                                  {"name": "x" * 201}, {"region": "x" * 41}])
def test_bad_fields_are_400(editor, hall, body):
    assert editor.patch(url(hall[0]), json=body).status_code == 400


def test_unknown_venue_is_404(editor):
    assert editor.patch(url(999), json={"city": "X"}).status_code == 404
    assert editor.get(url(999, "/names")).status_code == 404


def test_rename_into_another_venues_name_is_409(editor, con, hall):
    vid, _ = hall
    other = mk_venue(con, "Basalt Arena")
    r = editor.patch(url(vid), json={"name": "the basalt arena"})
    assert r.status_code == 409
    assert r.get_json()["error"] == "That name belongs to Basalt Arena. Merge them instead."
    assert names(con, vid) == ["Granite Hall"]
    # another city is fine
    far = mk_venue(con, "Basalt Arena", city="Fresno")
    assert editor.patch(url(far), json={"name": "Granite Hall"}).status_code == 200
    assert other != far


def test_moving_into_a_clashing_city_is_409(editor, con, hall):
    vid, _ = hall
    mk_venue(con, "Granite Hall", city="Fresno")
    assert editor.patch(url(vid), json={"city": "Fresno"}).status_code == 409
    assert con.execute("SELECT city FROM venue WHERE id = ?", (vid,)).fetchone()[0] == "Oakland"


def test_add_name(editor, con, hall):
    vid, sid = hall
    r = editor.post(url(vid, "/names"), json={"name": "The Old Armory"})
    assert r.status_code == 201
    assert [n["name"] for n in r.get_json()["names"]] == ["Granite Hall", "The Old Armory"]
    assert records.find_venue(con, "Old Armory", "Oakland") == vid
    # a show can now carry it on its ticket
    s = editor.patch(f"/admin/api/shows/{sid}", json={"venue_label": "The Old Armory",
                                                      "version": con.execute("SELECT version FROM show WHERE id = ?",
                                                                             (sid,)).fetchone()[0]})
    assert s.status_code == 200
    assert editor.get(url(vid, "/names")).get_json()["names"][1]["shows"] == 1


def test_add_existing_name_is_a_noop(editor, con, hall):
    vid, _ = hall
    for name in ("Granite Hall", "the granite  hall"):
        r = editor.post(url(vid, "/names"), json={"name": name})
        assert r.status_code == 200
    assert names(con, vid) == ["Granite Hall"]


def test_add_name_another_venue_has_is_409(editor, con, hall):
    vid, _ = hall
    mk_venue(con, "Basalt Arena")
    r = editor.post(url(vid, "/names"), json={"name": "Basalt Arena"})
    assert r.status_code == 409
    assert r.get_json()["error"] == "That name belongs to Basalt Arena. Merge them instead."
    assert editor.post(url(vid, "/names"), json={}).status_code == 400


def test_remove_name(editor, con, hall):
    vid, _ = hall
    editor.post(url(vid, "/names"), json={"name": "The Old Armory"})
    r = editor.delete(url(vid, "/names"), json={"name": "the old armory"})
    assert r.status_code == 200
    assert [n["name"] for n in r.get_json()["names"]] == ["Granite Hall"]
    assert records.find_venue(con, "Old Armory", "Oakland") is None
    assert editor.delete(url(vid, "/names"), json={"name": "Old Armory"}).status_code == 404


def test_remove_current_or_used_name_is_409(editor, con, hall):
    vid, _ = hall
    r = editor.delete(url(vid, "/names"), json={"name": "Granite Hall"})
    assert (r.status_code, r.get_json()["error"]) == (409, "Rename the venue first.")
    editor.patch(url(vid), json={"name": "Basalt Arena"})
    r = editor.delete(url(vid, "/names"), json={"name": "Granite Hall"})
    assert (r.status_code, r.get_json()["error"]) == (409, "1 show has this name on the ticket.")
    assert names(con, vid) == ["Basalt Arena", "Granite Hall"]


def test_many_shows_use_a_name(editor, con, hall):
    vid, _ = hall
    mk_show(con, "Cold Harbor", "2020-01-01", vid)
    editor.patch(url(vid), json={"name": "Basalt Arena"})
    assert editor.delete(url(vid, "/names"), json={"name": "Granite Hall"}).get_json()["error"] == \
        "2 shows have this name on the ticket."


def test_public_names_unchanged(client, editor, con, hall):
    vid, _ = hall
    before = client.get(f"/api/venues/{vid}").get_json()
    editor.post(url(vid, "/names"), json={"name": "The Old Armory"})
    after = client.get(f"/api/venues/{vid}").get_json()
    assert set(after) == set(before) and "coordinates_kept" not in after
    # the public page still hides a name only an upcoming show's ticket uses
    mk_show(con, "Cold Harbor", "2027-01-01", vid, status="upcoming", venue_label="Hidden Name")
    assert "Hidden Name" not in client.get(f"/api/venues/{vid}").get_json()["names"]


def test_access(client, con, hall):
    vid, _ = hall
    for method, tail, body in (("patch", "", {"city": "X"}), ("post", "/names", {"name": "Y"}),
                               ("delete", "/names", {"name": "Y"}), ("get", "/names", None)):
        assert getattr(client, method)(url(vid, tail), json=body).status_code == 401
        r = getattr(client, method)(url(vid, tail), json=body, headers={"X-API-Key": READ_KEY})
        assert r.status_code == (200 if method == "get" else 403)
