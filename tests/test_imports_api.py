"""Task 4: the import API under /admin/api/imports (CSV upload, API batches, review, row edits, apply, discard)."""
import io
import os

import pytest

import imports
from conftest import READ_KEY

with open(os.path.join(os.path.dirname(__file__), "fixtures", "concerts.csv"), "rb") as _f:
    FIXTURE = _f.read()

SHOW = {"date": "2016-12-01", "status": "attended", "billing": "Cold Harbor", "venue": "The Bellwether",
        "city": "Oakland", "region": "CA", "country": "US",
        "sources": [{"label": "Venue page", "url": "https://concerts.example.com/cold-harbor"}]}


def upload(c, data=FIXTURE, name="concerts.csv"):
    return c.post("/admin/api/imports/csv", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def show_count(c):
    return len(c.get("/admin/api/shows").get_json())


def test_csv_upload_then_review_then_apply(editor, clock):
    clock.set("2026-11-14")
    r = upload(editor)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert set(body) == {"id", "counts"}
    assert body["counts"] == {"new": 11, "have": 1, "maybe": 0, "skip": 3}

    view = editor.get(f"/admin/api/imports/{body['id']}").get_json()
    assert (view["label"], view["origin"], view["status"], len(view["rows"])) == (
        "CSV import 2026-11-14", "csv", "pending", 15)
    assert editor.get("/admin/api/imports").get_json()["batches"][0]["id"] == body["id"]

    r = editor.post(f"/admin/api/imports/{body['id']}/apply")
    assert (r.status_code, r.get_json()) == (200, {"created": 11})
    assert show_count(editor) == 11
    sid = editor.get("/admin/api/shows").get_json()[0]["id"]
    detail = editor.get(f"/admin/api/shows/{sid}").get_json()
    assert "CSV import 2026-11-14" in [s["label"] for s in detail["sources"]]
    assert editor.get("/admin/api/imports").get_json() == {"batches": []}
    assert editor.get(f"/admin/api/imports/{body['id']}").get_json()["status"] == "applied"


def test_apply_twice_over_http_is_409(editor):
    bid = upload(editor).get_json()["id"]
    assert editor.post(f"/admin/api/imports/{bid}/apply").status_code == 200
    r = editor.post(f"/admin/api/imports/{bid}/apply")
    assert (r.status_code, r.get_json()) == (409, {"error": "This import was already applied."})
    assert show_count(editor) == 11


def test_api_batch_with_write_key(editor):
    r = editor.post("/admin/api/imports", json={"label": "Claude finds", "shows": [SHOW]})
    assert r.status_code == 201
    body = r.get_json()
    assert body["counts"] == {"new": 1, "have": 0, "maybe": 0, "skip": 0}
    [b] = editor.get("/admin/api/imports").get_json()["batches"]
    assert (b["id"], b["origin"], b["label"]) == (body["id"], "api", "Claude finds")
    assert editor.post(f"/admin/api/imports/{body['id']}/apply").get_json() == {"created": 1}


def test_api_batch_bearer_and_default_label(client, clock):
    clock.set("2026-11-14")
    r = client.post("/admin/api/imports", json={"shows": [SHOW]},
                    headers={"Authorization": "Bearer test-write-key"})
    assert r.status_code == 201
    view = client.get(f"/admin/api/imports/{r.get_json()['id']}", headers={"X-API-Key": READ_KEY}).get_json()
    assert view["label"] == "API import 2026-11-14"


def test_api_batch_rejects_bad_proposal(editor):
    r = editor.post("/admin/api/imports", json={"label": "x", "shows": [{**SHOW, "date": "next week"}]})
    assert r.status_code == 400
    assert r.get_json()["error"].startswith("Row 1: ") and "date" in r.get_json()["error"]
    assert editor.get("/admin/api/imports").get_json() == {"batches": []}


@pytest.mark.parametrize("body", [{"label": "x"}, {"label": "x", "shows": []}, {"label": "x", "shows": "Cold Harbor"},
                                  {"label": 5, "shows": [SHOW]}, ["not an object"]])
def test_api_batch_bad_bodies(editor, body):
    assert editor.post("/admin/api/imports", json=body).status_code == 400


def test_csv_upload_errors(editor, monkeypatch):
    r = editor.post("/admin/api/imports/csv", data={}, content_type="multipart/form-data")
    assert (r.status_code, r.get_json()) == (400, {"error": "Choose a CSV file."})
    r = upload(editor, b"date,artist,city,status\n2005-04-02,Cold Harbor,\"Oakland, CA\",attended\n")
    assert (r.status_code, r.get_json()) == (400, {"error": "missing column: venue"})
    r = upload(editor, b"date,artist,venue,city,status\n")
    assert (r.status_code, r.get_json()) == (400, {"error": "No shows to import."})
    monkeypatch.setattr(imports, "MAX_CSV_BYTES", 100)
    assert upload(editor).status_code == 413
    assert editor.get("/admin/api/imports").get_json() == {"batches": []}


def test_patch_row(editor):
    bid = upload(editor).get_json()["id"]
    rows = {r["row_no"]: r for r in editor.get(f"/admin/api/imports/{bid}").get_json()["rows"]}
    r = editor.patch(f"/admin/api/import-rows/{rows[1]['id']}", json={"include": False})
    assert r.status_code == 200 and r.get_json()["include"] is False and r.get_json()["id"] == rows[1]["id"]
    r = editor.patch(f"/admin/api/import-rows/{rows[2]['id']}", json={"include": True})
    assert r.status_code == 400
    fixed = {**rows[14]["parsed"], "date": "2018-03-04", "skip_reason": None}
    r = editor.patch(f"/admin/api/import-rows/{rows[14]['id']}", json={"parsed": fixed})
    assert (r.status_code, r.get_json()["verdict"], r.get_json()["include"]) == (200, "new", True)
    assert editor.patch(f"/admin/api/import-rows/{rows[1]['id']}", json={}).status_code == 400
    assert editor.patch("/admin/api/import-rows/9999", json={"include": True}).status_code == 404
    assert editor.post(f"/admin/api/imports/{bid}/apply").get_json() == {"created": 11}  # 11 - row 1 + row 14


def test_discard_route(editor):
    bid = upload(editor).get_json()["id"]
    r = editor.post(f"/admin/api/imports/{bid}/discard")
    assert (r.status_code, r.get_json()) == (200, {"ok": True})
    assert editor.get("/admin/api/imports").get_json() == {"batches": []}
    assert editor.post(f"/admin/api/imports/{bid}/apply").status_code == 409
    assert editor.post("/admin/api/imports/9999/discard").status_code == 404
    assert editor.get("/admin/api/imports/9999").status_code == 404
    assert show_count(editor) == 0


def test_imports_are_editor_only(client, editor):
    bid = upload(editor).get_json()["id"]
    rid = editor.get(f"/admin/api/imports/{bid}").get_json()["rows"][0]["id"]
    calls = [("get", "/admin/api/imports"), ("get", f"/admin/api/imports/{bid}"),
             ("post", "/admin/api/imports/csv"), ("post", "/admin/api/imports"),
             ("patch", f"/admin/api/import-rows/{rid}"), ("post", f"/admin/api/imports/{bid}/apply"),
             ("post", f"/admin/api/imports/{bid}/discard")]
    for method, url in calls:
        r = getattr(client, method)(url, json={"include": False})
        assert (r.status_code, r.get_json()) == (401, {"error": "Sign in to edit"}), url
    read = {"X-API-Key": READ_KEY}
    assert client.get(f"/admin/api/imports/{bid}", headers=read).status_code == 200
    for method, url in calls[2:]:
        assert getattr(client, method)(url, json={"include": False}, headers=read).status_code == 403, url
    r = editor.post(f"/admin/api/imports/{bid}/apply", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert editor.get(f"/admin/api/imports/{bid}").get_json()["status"] == "pending"
