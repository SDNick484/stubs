"""Task 17: posters, photos and stubs. Images are tiny generated files (magic bytes, or a real one-pixel PNG)."""
import io
import os
import struct
import zlib

import pytest

import images
import settings
from common import ApiError
from conftest import mk_show, mk_venue


def png(seed=0):
    """A valid 1x1 PNG whose colour depends on seed, so each seed is a different file."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"\x00" + bytes([seed % 256, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(seed=0):
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes([seed % 256]) * 8 + b"\xff\xd9"


def webp(seed=0):
    return b"RIFF" + struct.pack("<I", 12) + b"WEBPVP8 " + bytes([seed % 256]) * 8


@pytest.fixture
def show(con):
    return mk_show(con, "The Paper Lanterns", "2025-07-19", mk_venue(con, "Granite Hall"))


@pytest.fixture
def upcoming(con):
    return mk_show(con, "Cold Harbor", "2099-09-09", mk_venue(con, "Granite Hall", "Reno", "NV"), status="upcoming")


def upload(c, show_id, data, kind="photo", name="x.jpg", **form):
    return c.post(f"/admin/api/shows/{show_id}/images", data={"file": (io.BytesIO(data), name), "kind": kind, **form},
                  content_type="multipart/form-data")


def files():
    return sorted(os.listdir(settings.IMAGE_DIR)) if settings.IMAGE_DIR.exists() else []


# ---------------------------------------------------------------- sniffing
def test_sniff_image():
    assert images.sniff_image(jpeg()) == ".jpg"
    assert images.sniff_image(png()) == ".png"
    assert images.sniff_image(webp()) == ".webp"
    assert images.sniff_image(b"hello") is None
    assert images.sniff_image(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None
    assert images.sniff_image(b"") is None
    assert images.MAX_IMAGE_BYTES == 8 * 1024 * 1024


# ---------------------------------------------------------------- upload and serve
def test_upload_photo_and_serve(editor, client, con, show):
    r = upload(editor, show, png(1), caption="Front row")
    assert r.status_code == 201
    item = r.get_json()
    assert item["kind"] == "photo" and item["caption"] == "Front row"
    row = con.execute("SELECT * FROM image WHERE id = ?", (item["id"],)).fetchone()
    assert row["file"].startswith(f"{show}-photo-") and row["file"].endswith(".png")
    assert len(row["file"].split("-")[2].split(".")[0]) == 12
    assert (settings.IMAGE_DIR / row["file"]).read_bytes() == png(1)
    # public may see the photo, by the public URL; the editor's URL is under /admin
    pub = client.get(f"/api/shows/{show}/images").get_json()
    assert pub == [{"id": item["id"], "kind": "photo", "url": f"/images/{row['file']}", "caption": "Front row"}]
    got = client.get(pub[0]["url"])
    assert got.status_code == 200 and got.data == png(1) and got.mimetype == "image/png"
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    ed = editor.get(f"/admin/api/shows/{show}/images").get_json()
    assert ed[0]["url"] == f"/admin/images/{row['file']}"
    assert editor.get(ed[0]["url"]).data == png(1)
    assert client.get(ed[0]["url"]).status_code == 401


def test_photos_keep_upload_order_and_can_be_moved(editor, client, show):
    ids = [upload(editor, show, png(i)).get_json()["id"] for i in (1, 2, 3)]
    listed = lambda: [i["id"] for i in client.get(f"/api/shows/{show}/images").get_json()]  # noqa: E731
    assert listed() == ids
    r = editor.patch(f"/admin/api/images/{ids[2]}", json={"position": 0, "caption": "Encore"})
    assert r.status_code == 200 and r.get_json()["caption"] == "Encore"
    assert listed() == [ids[2], ids[0], ids[1]]
    assert editor.patch(f"/admin/api/images/{ids[2]}", json={"caption": ""}).get_json()["caption"] is None
    assert editor.patch(f"/admin/api/images/{ids[2]}", json={"position": -1}).status_code == 400
    assert editor.patch("/admin/api/images/9999", json={"caption": "x"}).status_code == 404


def test_poster_is_editor_only_unless_public_posters(client, editor, con, monkeypatch, show):
    item = upload(editor, show, png(5), kind="poster").get_json()
    file = con.execute("SELECT file FROM image WHERE id = ?", (item["id"],)).fetchone()["file"]
    assert client.get(f"/images/{file}").status_code == 404
    assert client.get(f"/api/shows/{show}/images").get_json() == []
    assert [i["kind"] for i in editor.get(f"/admin/api/shows/{show}/images").get_json()] == ["poster"]
    monkeypatch.setattr(settings, "PUBLIC_POSTERS", True)
    assert client.get(f"/images/{file}").status_code == 200
    assert [i["kind"] for i in client.get(f"/api/shows/{show}/images").get_json()] == ["poster"]


def test_stub_never_public(client, editor, con, monkeypatch, show):
    monkeypatch.setattr(settings, "PUBLIC_POSTERS", True)
    upload(editor, show, png(6), kind="stub")
    file = con.execute("SELECT file FROM image WHERE kind = 'stub'").fetchone()["file"]
    assert client.get(f"/images/{file}").status_code == 404
    assert client.get(f"/api/shows/{show}/images").get_json() == []
    assert editor.get(f"/images/{file}").status_code == 200  # an editor may use either path
    assert [i["kind"] for i in editor.get(f"/admin/api/shows/{show}/images").get_json()] == ["stub"]


def test_public_view_off_hides_images(client, editor, con, monkeypatch, show):
    upload(editor, show, png(7))
    file = con.execute("SELECT file FROM image").fetchone()["file"]
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    assert client.get(f"/images/{file}").status_code == 404
    assert client.get(f"/api/shows/{show}/images").status_code == 403
    assert editor.get(f"/images/{file}").status_code == 200


def test_new_poster_replaces_old_file(editor, con, show):
    upload(editor, show, png(1), kind="poster")
    assert len(files()) == 1
    second = upload(editor, show, jpeg(2), kind="poster").get_json()["id"]
    rows = con.execute("SELECT id, file FROM image WHERE kind = 'poster'").fetchall()
    assert [r["id"] for r in rows] == [second]  # (SQLite may reuse the deleted id)
    assert files() == [rows[0]["file"]] and rows[0]["file"].endswith(".jpg")


def test_same_poster_again_keeps_its_file(editor, con, show):
    upload(editor, show, png(1), kind="poster")
    upload(editor, show, png(1), kind="poster")
    assert con.execute("SELECT COUNT(*) FROM image").fetchone()[0] == 1
    assert len(files()) == 1


def test_delete_removes_row_and_file_but_not_a_shared_file(editor, con, show):
    a = upload(editor, show, png(1)).get_json()["id"]
    b = upload(editor, show, png(1)).get_json()["id"]  # same bytes, same name, second row
    assert len(files()) == 1
    assert editor.delete(f"/admin/api/images/{a}").status_code == 200
    assert len(files()) == 1
    assert editor.delete(f"/admin/api/images/{b}").status_code == 200
    assert files() == [] and con.execute("SELECT COUNT(*) FROM image").fetchone()[0] == 0
    assert editor.delete(f"/admin/api/images/{b}").status_code == 404


def test_not_an_image_is_400(editor, show):
    r = upload(editor, show, b"hello", name="x.jpg")
    assert r.status_code == 400 and "image" in r.get_json()["error"]
    assert files() == []


def test_too_big_is_413(editor, show):
    r = upload(editor, show, png() + b"\x00" * images.MAX_IMAGE_BYTES)
    assert r.status_code == 413
    assert files() == []


def test_bad_kind_caption_and_missing_file(editor, show):
    assert upload(editor, show, png(), kind="video").status_code == 400
    assert upload(editor, show, png(), caption="x" * 201).status_code == 400
    assert editor.post(f"/admin/api/shows/{show}/images", data={"kind": "photo"}).status_code == 400
    assert upload(editor, 9999, png()).status_code == 404
    assert files() == []


def test_upload_needs_an_editor_and_not_the_read_key(client, show):
    assert upload(client, show, png()).status_code == 401
    c = client.application.test_client()
    c.environ_base.update(HTTP_X_API_KEY="test-read-key")
    assert upload(c, show, png()).status_code == 403
    assert c.get(f"/admin/api/shows/{show}/images").status_code == 200


def test_upcoming_show_images_hidden_from_public(client, editor, con, monkeypatch, upcoming):
    monkeypatch.setattr(settings, "PUBLIC_POSTERS", True)
    assert upload(editor, upcoming, png(1), kind="poster").status_code == 201
    assert upload(editor, upcoming, png(2)).status_code == 201
    assert client.get(f"/api/shows/{upcoming}/images").status_code == 404
    for row in con.execute("SELECT file FROM image"):
        assert client.get(f"/images/{row['file']}").status_code == 404
        assert editor.get(f"/images/{row['file']}").status_code == 200
    assert len(editor.get(f"/admin/api/shows/{upcoming}/images").get_json()) == 2


def test_unknown_show_is_404(client, editor):
    assert client.get("/api/shows/9999/images").status_code == 404
    assert editor.get("/admin/api/shows/9999/images").status_code == 404


def test_path_traversal_name_is_404(client, editor, show):
    upload(editor, show, png(1))
    for name in ("..%2Fstubs.db", "../stubs.db", "%2e%2e%2fstubs.db", "nope.png", "stubs.db", ".."):
        assert client.get(f"/images/{name}").status_code == 404, name
        assert editor.get(f"/images/{name}").status_code == 404, name
        assert editor.get(f"/admin/images/{name}").status_code == 404, name


def test_a_file_in_the_folder_without_a_row_is_not_served(client, show):
    settings.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.IMAGE_DIR / "orphan.png").write_bytes(png())
    assert client.get("/images/orphan.png").status_code == 404


# ---------------------------------------------------------------- the module
def test_save_image_directly(con, show):
    iid = images.save_image(con, show, "photo", jpeg(3), caption="Sam", source_url="https://concerts.example.com/p.jpg")
    row = con.execute("SELECT * FROM image WHERE id = ?", (iid,)).fetchone()
    assert row["source_url"] == "https://concerts.example.com/p.jpg" and row["caption"] == "Sam"
    with pytest.raises(ApiError) as e:
        images.save_image(con, show, "photo", jpeg(3), source_url="javascript:alert(1)")
    assert e.value.status == 400
    with pytest.raises(ApiError) as e:
        images.save_image(con, 9999, "photo", jpeg(3))
    assert e.value.status == 404


def test_list_images_orders_poster_first_and_public_filters(con, monkeypatch, show):
    images.save_image(con, show, "photo", png(1))
    images.save_image(con, show, "stub", png(2))
    images.save_image(con, show, "poster", png(3))
    kinds = lambda **kw: [i["kind"] for i in images.list_images(con, show, **kw)]  # noqa: E731
    assert kinds(editor=True) == ["poster", "photo", "stub"]
    assert kinds(editor=False) == ["photo"]
    monkeypatch.setattr(settings, "PUBLIC_POSTERS", True)
    assert kinds(editor=False) == ["poster", "photo"]


def test_update_and_delete_directly(con, show):
    iid = images.save_image(con, show, "photo", png(1))
    images.update_image(con, iid, caption="Sunset", position=4)
    row = con.execute("SELECT caption, position FROM image WHERE id = ?", (iid,)).fetchone()
    assert (row["caption"], row["position"]) == ("Sunset", 4)
    images.delete_image(con, iid)
    assert files() == []
