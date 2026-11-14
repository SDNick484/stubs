"""Posters, photos and stubs on a show (spec 7.9). Files sit flat in settings.IMAGE_DIR under a content-hashed name,
`<show_id>-<kind>-<sha1[:12]><ext>`; the `image` table says which show a file belongs to and who may see it. Types
are sniffed from the first bytes (no Pillow); nothing is resized. Takes a sqlite3.Connection, never imports app.

Who sees what: editors see everything. Everyone else sees photos, and posters only with PUBLIC_POSTERS=1; stubs are
never public, and nothing of an upcoming show is (a public request for one is 404, never 403)."""
import hashlib
import os

import settings
from common import ApiError
from db import transaction

MAX_IMAGE_BYTES = 8 * 1024 * 1024
KINDS = ("poster", "photo", "stub")
MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_CAPTION = 200
MAX_URL = 2000

_ORDER = "CASE kind WHEN 'poster' THEN 0 WHEN 'photo' THEN 1 ELSE 2 END, position, id"


def sniff_image(data):
    """".jpg", ".png" or ".webp" from the file's first bytes, else None."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _item(row, editor):
    """The public shape of an image. Editors are given the /admin path, which is what carries their sign-in when
    Cloudflare Access guards only /admin."""
    prefix = "/admin/images/" if editor else "/images/"
    return {"id": row["id"], "kind": row["kind"], "url": prefix + row["file"], "caption": row["caption"]}


def _caption(value):
    if value is not None and not isinstance(value, str):
        raise ApiError(400, "caption must be text.")
    value = (value or "").strip()
    if len(value) > MAX_CAPTION:
        raise ApiError(400, f"caption must be at most {MAX_CAPTION} characters.")
    return value or None


def _source_url(value):
    if value is None:
        return None
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        return None
    if len(value) > MAX_URL or not value.lower().startswith(("http://", "https://")):
        raise ApiError(400, "source_url must be an http or https address.")
    return value


def _unlink_if_unused(con, file):
    """Delete the file unless another row still names it (the same bytes uploaded twice share one name)."""
    if con.execute("SELECT 1 FROM image WHERE file = ?", (file,)).fetchone():
        return
    try:
        os.remove(settings.IMAGE_DIR / file)
    except FileNotFoundError:
        pass


def save_image(con, show_id, kind, data, caption=None, source_url=None):
    """Store an uploaded image for a show and return the new row's id. A new poster replaces the old row and file
    (a show has one). Photos and stubs go last in their order. 400 when the bytes aren't a JPEG, PNG or WebP (or the
    kind, caption or source is bad), 413 over MAX_IMAGE_BYTES, 404 for an unknown show."""
    if kind not in KINDS:
        raise ApiError(400, "kind must be poster, photo or stub.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "That file is over 8 MB.")
    ext = sniff_image(data)
    if ext is None:
        raise ApiError(400, "That isn't a JPEG, PNG or WebP image.")
    caption, source_url = _caption(caption), _source_url(source_url)
    if not con.execute("SELECT 1 FROM show WHERE id = ?", (show_id,)).fetchone():
        raise ApiError(404, "Not found")
    name = f"{show_id}-{kind}-{hashlib.sha1(data).hexdigest()[:12]}{ext}"
    settings.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = settings.IMAGE_DIR / f".{name}.part"
    tmp.write_bytes(data)
    os.replace(tmp, settings.IMAGE_DIR / name)
    try:
        with transaction(con):
            old = []
            if kind == "poster":
                old = [r["file"] for r in con.execute("SELECT file FROM image WHERE show_id = ? AND kind = 'poster'",
                                                      (show_id,))]
                con.execute("DELETE FROM image WHERE show_id = ? AND kind = 'poster'", (show_id,))
                position = 0
            else:
                position = con.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM image WHERE show_id = ? AND kind = ?",
                                       (show_id, kind)).fetchone()[0]
            iid = con.execute("INSERT INTO image (show_id, kind, file, caption, source_url, position) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (show_id, kind, name, caption, source_url, position)).lastrowid
    except BaseException:
        _unlink_if_unused(con, name)
        raise
    for file in old:
        _unlink_if_unused(con, file)
    return iid


def list_images(con, show_id, *, editor):
    """[{"id", "kind", "url", "caption"}]: poster, photos, stubs, each in order. Public: photos only, plus the poster
    with PUBLIC_POSTERS; an upcoming show has none. 404 when the show doesn't exist, or (public) is upcoming."""
    show = con.execute("SELECT status FROM show WHERE id = ?", (show_id,)).fetchone()
    if not show or (not editor and show["status"] == "upcoming"):
        raise ApiError(404, "Not found")
    rows = con.execute(f"SELECT * FROM image WHERE show_id = ? ORDER BY {_ORDER}", (show_id,)).fetchall()
    if not editor:
        shown = ("photo", "poster") if settings.PUBLIC_POSTERS else ("photo",)
        rows = [r for r in rows if r["kind"] in shown]
    return [_item(r, editor) for r in rows]


def visible_file(con, name, *, editor):
    """The file name to serve for this request, or None when no image row of that name may be seen by it."""
    if not name or os.path.basename(name) != name or name.startswith("."):
        return None
    rows = con.execute("SELECT i.kind, s.status FROM image i JOIN show s ON s.id = i.show_id WHERE i.file = ?", (name,))
    for row in rows:
        if editor or (row["status"] != "upcoming"
                      and (row["kind"] == "photo" or (row["kind"] == "poster" and settings.PUBLIC_POSTERS))):
            return name
    return None


def get_image(con, image_id, *, editor=True):
    row = con.execute("SELECT * FROM image WHERE id = ?", (image_id,)).fetchone()
    if not row:
        raise ApiError(404, "Not found")
    return _item(row, editor)


def update_image(con, image_id, caption=None, position=None):
    """Change an image's caption (an empty one clears it) and/or its place in the order; None leaves a field alone."""
    if not con.execute("SELECT 1 FROM image WHERE id = ?", (image_id,)).fetchone():
        raise ApiError(404, "Not found")
    if position is not None and (isinstance(position, bool) or not isinstance(position, int) or position < 0):
        raise ApiError(400, "position must be a whole number, 0 or more.")
    new_caption = _caption(caption) if caption is not None else None
    with transaction(con):
        if caption is not None:
            con.execute("UPDATE image SET caption = ? WHERE id = ?", (new_caption, image_id))
        if position is not None:
            con.execute("UPDATE image SET position = ? WHERE id = ?", (position, image_id))


def delete_image(con, image_id):
    """Delete the row and its file."""
    row = con.execute("SELECT file FROM image WHERE id = ?", (image_id,)).fetchone()
    if not row:
        raise ApiError(404, "Not found")
    con.execute("DELETE FROM image WHERE id = ?", (image_id,))
    _unlink_if_unused(con, row["file"])
