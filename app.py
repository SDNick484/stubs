"""Stubs: a personal concert history. The Flask object, access control, hooks and every route. Route-free logic lives
in sibling modules that take a sqlite3.Connection and never import this one.

Public:   /, /api/...            read-only, when PUBLIC_VIEW=1 (403 {"error": "Sign in to view"} otherwise)
Editors:  /admin, /admin/api/... the same shell, plus every write; put /admin behind Cloudflare Access
Health:   /healthz               {"ok": true}

Each later task adds its imports and routes under its own `# ==== Task N` marker below, so parallel changes touch
different parts of this file."""
import hmac
import html
import json
import os
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, redirect, request
from werkzeug.exceptions import HTTPException

import settings
from common import ApiError, json_body, text
from db import close_db, db, init_db

app = Flask(__name__, static_folder=str(settings.APP_DIR / "static"), static_url_path="/static")
app.teardown_appcontext(close_db)

READ_METHODS = ("GET", "HEAD", "OPTIONS")


# ==== Task 1: Scaffold — access, headers, errors, the shell
# ---------------------------------------------------------------- who is an editor
_jwks = None  # PyJWKClient for the team's Access certificates, made on first use


def _key_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def _access_email(token):
    """The email of a valid Cloudflare Access JWT for this app, or None."""
    global _jwks
    import jwt
    try:
        if _jwks is None:
            _jwks = jwt.PyJWKClient(f"https://{settings.CF_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True)
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.CF_AUD,
                            issuer=f"https://{settings.CF_TEAM_DOMAIN}")
    except Exception as e:  # noqa: BLE001 - any failure means "not an editor"
        app.logger.warning("Access JWT rejected: %s", e)
        return None
    return (claims.get("email") or "").strip().lower() or None


def lan_host():
    """True when the request's Host (port ignored) is one of LAN_HOSTS."""
    host = urlsplit("//" + request.host).hostname
    return bool(host) and host.lower() in settings.LAN_HOSTS


def access_identity():
    """Who this request is, as an editor: "api-key" (the write key), "read-key", the Access email, or "lan". None for
    everyone else. Worked out once per request."""
    if "identity" in g:
        return g.identity
    who = None
    key = _key_from_request()
    if key and settings.STUBS_API_KEY and hmac.compare_digest(key.encode(), settings.STUBS_API_KEY.encode()):
        who = "api-key"
    elif key and settings.STUBS_READ_KEY and hmac.compare_digest(key.encode(), settings.STUBS_READ_KEY.encode()):
        who = "read-key"
    else:
        token = request.headers.get("Cf-Access-Jwt-Assertion")
        if token:
            if settings.CF_TEAM_DOMAIN and settings.CF_AUD:
                email = _access_email(token)
                who = email if email and email in settings.ADMIN_EMAILS else None
        else:
            via_cloudflare = request.headers.get("Cf-Ray") or request.headers.get("Cf-Connecting-Ip")
            if settings.LAN_EDIT and not via_cloudflare and lan_host():
                who = "lan"
    g.identity = who
    return who


def is_editor() -> bool:
    return access_identity() is not None


def cross_site():
    """A browser request from another site: its Origin names another host, or Sec-Fetch-Site says cross-site. Only
    the host is compared: behind the tunnel the app sees http while the browser's Origin says https."""
    origin = (request.headers.get("Origin") or "").strip()
    if origin and urlsplit(origin).netloc.lower() != request.host.lower():
        return True
    return (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"


def editor_required(fn):
    """Every /admin/api/ route. Refuses cross-site browser requests (403), anyone not an editor (401), and the read
    key on anything but a read (403). g.editor holds who it was."""
    @wraps(fn)
    def inner(*a, **kw):
        if cross_site():
            raise ApiError(403, "Cross-site request refused")
        who = access_identity()
        if not who:
            raise ApiError(401, "Sign in to edit")
        if who == "read-key" and request.method not in READ_METHODS:
            raise ApiError(403, "This key can only read.")
        g.editor = who
        return fn(*a, **kw)
    return inner


def public_read(fn):
    """Every public /api/ read: open to anyone while PUBLIC_VIEW=1, else editors only."""
    @wraps(fn)
    def inner(*a, **kw):
        if not settings.PUBLIC_VIEW and not is_editor():
            raise ApiError(403, "Sign in to view")
        return fn(*a, **kw)
    return inner


# ---------------------------------------------------------------- every response
def csp():
    """The Content-Security-Policy, with TILE_URL's scheme and host allowed as images ({s} subdomains as *)."""
    tiles = urlsplit(settings.TILE_URL.replace("{s}", "*"))
    tile_src = f" {tiles.scheme}://{tiles.netloc}" if tiles.scheme in ("http", "https") and tiles.netloc else ""
    return (f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:{tile_src}; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = csp()
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith(("/api/", "/admin/api/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(ApiError)
def api_error(e):
    return jsonify(error=e.message), e.status


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error="Not found" if e.code == 404 else e.name), e.code


# ---------------------------------------------------------------- the shell
def _shell():
    """static/index.html with APP_NAME (escaped) as its title. Everything after this is hash-routed by main.js."""
    with open(settings.APP_DIR / "static" / "index.html", encoding="utf-8") as f:
        page = f.read().replace("__APP_NAME__", html.escape(settings.APP_NAME, quote=True))
    return app.response_class(page, mimetype="text/html")


@app.get("/")
def index():
    if not settings.PUBLIC_VIEW and not is_editor():
        return redirect("/admin")
    return _shell()


@app.get("/admin")
@app.get("/admin/")
def admin_index():
    """The same shell. Cloudflare Access guards this path, so pages opened here call /admin/api/session."""
    return _shell()


@app.get("/manifest.json")
def manifest():
    """static/manifest.json with name and short_name set to APP_NAME."""
    with open(settings.APP_DIR / "static" / "manifest.json", encoding="utf-8") as f:
        data = {**json.load(f), "name": settings.APP_NAME, "short_name": settings.APP_NAME}
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.get("/robots.txt")
def robots():
    return app.send_static_file("robots.txt")


@app.get("/healthz")
def healthz():
    db().execute("SELECT 1")
    return jsonify(ok=True)


def _session():
    return jsonify(editor=is_editor(), public=settings.PUBLIC_VIEW, app_name=settings.APP_NAME,
                   tile_url=settings.TILE_URL)


@app.get("/api/session")
def session_info():
    """Never gated, so the shell can always tell a visitor to sign in."""
    return _session()


@app.get("/admin/api/session")
def admin_session_info():
    """The same answer for pages under /admin, whose requests carry the Access JWT (not editor_required: a visitor
    gets editor false, not 401)."""
    return _session()


# ==== Task 2: Records


# ==== Task 3: CSV parser


# ==== Task 4: Import batches — match, review, apply, import API


# ==== Task 5: Import screens


# ==== Task 6: Browse API — shows, artists, venues, years, cities
# Each read twice: /api/ is the public view whoever asks (editor=False), /admin/api/ the editor's (editor=True).
import browse  # noqa: E402


def _int_arg(name):
    """A whole-number query parameter, None when absent or empty, 400 otherwise."""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ApiError(400, f"{name} must be a whole number.") from None


def _found(data):
    if data is None:
        raise ApiError(404, "Not found")
    return jsonify(data)


def _shows(editor):
    return jsonify(browse.list_shows(db(), editor=editor, year=_int_arg("year"), artist_id=_int_arg("artist_id"),
                                     venue_id=_int_arg("venue_id"), city=(request.args.get("city") or "").strip() or None))


@app.get("/api/shows")
@public_read
def api_shows():
    return _shows(False)


@app.get("/admin/api/shows")
@editor_required
def admin_api_shows():
    return _shows(True)


@app.get("/api/shows/<int:show_id>")
@public_read
def api_show(show_id):
    return _found(browse.show_detail(db(), show_id, editor=False))


@app.get("/admin/api/shows/<int:show_id>")
@editor_required
def admin_api_show(show_id):
    return _found(browse.show_detail(db(), show_id, editor=True))


@app.get("/api/artists/<int:artist_id>")
@public_read
def api_artist(artist_id):
    return _found(browse.artist_detail(db(), artist_id, editor=False))


@app.get("/admin/api/artists/<int:artist_id>")
@editor_required
def admin_api_artist(artist_id):
    return _found(browse.artist_detail(db(), artist_id, editor=True))


@app.get("/api/venues/<int:venue_id>")
@public_read
def api_venue(venue_id):
    return _found(browse.venue_detail(db(), venue_id, editor=False))


@app.get("/admin/api/venues/<int:venue_id>")
@editor_required
def admin_api_venue(venue_id):
    return _found(browse.venue_detail(db(), venue_id, editor=True))


@app.get("/api/years")
@public_read
def api_years():
    return jsonify(browse.years(db(), editor=False))


@app.get("/admin/api/years")
@editor_required
def admin_api_years():
    return jsonify(browse.years(db(), editor=True))


@app.get("/api/cities/<country>/<region>/<city>")
@public_read
def api_city(country, region, city):
    """A missing country or region is "-" in the path: /api/cities/FR/-/Lyon."""
    return _found(browse.city_detail(db(), country, region, city, editor=False))


@app.get("/admin/api/cities/<country>/<region>/<city>")
@editor_required
def admin_api_city(country, region, city):
    return _found(browse.city_detail(db(), country, region, city, editor=True))


# ==== Task 7: Show page


# ==== Task 8: Artist, venue, year and city pages


# ==== Task 9: Home — map, year strip and list


# ==== Task 10: Stats
from stats import stats  # noqa: E402


@app.get("/api/stats")
@public_read
def api_stats():
    return jsonify(stats(db()))


# ==== Task 11: Show editing


# ==== Task 12: Companions
import people  # noqa: E402


def _need_show(con, show_id):
    if not con.execute("SELECT 1 FROM show WHERE id = ?", (show_id,)).fetchone():
        raise ApiError(404, "Not found")


@app.get("/admin/api/people")
@editor_required
def people_list():
    return jsonify(people=people.list_people(db()))


@app.post("/admin/api/people")
@editor_required
def people_create():
    con = db()
    name = json_body().get("name")
    known = {p["id"] for p in people.list_people(con)}
    pid = people.create_person(con, name)
    return jsonify(id=pid, name=next(p["name"] for p in people.list_people(con) if p["id"] == pid)), (
        200 if pid in known else 201)


@app.get("/admin/api/shows/<int:show_id>/people")
@editor_required
def show_people(show_id):
    con = db()
    _need_show(con, show_id)
    return jsonify(people=people.companions(con, show_id))


@app.put("/admin/api/shows/<int:show_id>/people")
@editor_required
def show_people_set(show_id):
    con = db()
    _need_show(con, show_id)
    ids = json_body().get("person_ids")
    if not isinstance(ids, list) or any(type(i) is not int for i in ids):
        raise ApiError(400, "person_ids must be a list of ids.")
    people.set_companions(con, show_id, ids)
    return jsonify(people=people.companions(con, show_id))


@app.get("/admin/api/people/<int:person_id>/shows")
@editor_required
def person_show_list(person_id):
    con = db()
    if not con.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone():
        raise ApiError(404, "Not found")
    return jsonify(shows=people.person_shows(con, person_id))


# ==== Task 13: To-confirm list


# ==== Task 14: Upcoming and "Did you go?"


# ==== Task 15: Merge artists and venues


# ==== Task 16: Venue coordinates (Nominatim)
import geocode  # noqa: E402


@app.get("/admin/api/venues/missing-coords")
@editor_required
def admin_venues_missing_coords():
    return jsonify(venues=geocode.missing_coords(db()))


@app.post("/admin/api/venues/<int:venue_id>/locate")
@editor_required
def admin_venue_locate(venue_id):
    return jsonify(geocode.locate_venue(db(), venue_id))


@app.put("/admin/api/venues/<int:venue_id>/coords")
@editor_required
def admin_venue_coords(venue_id):
    data = json_body()
    return jsonify(geocode.set_coords(db(), venue_id, data.get("lat"), data.get("lon")))


# ==== Task 17: Images — posters, photos, stubs
import images  # noqa: E402
from flask import send_from_directory  # noqa: E402


@app.post("/admin/api/shows/<int:show_id>/images")
@editor_required
def upload_image(show_id):
    """multipart: file, kind (poster|photo|stub), caption (optional), source_url (optional)."""
    if request.content_length and request.content_length > images.MAX_IMAGE_BYTES + 64 * 1024:
        raise ApiError(413, "That file is over 8 MB.")
    upload = request.files.get("file")
    if upload is None:
        raise ApiError(400, "Choose an image file.")
    data = upload.stream.read(images.MAX_IMAGE_BYTES + 1)
    con = db()
    iid = images.save_image(con, show_id, request.form.get("kind", ""), data, caption=request.form.get("caption"),
                            source_url=request.form.get("source_url"))
    return jsonify(images.get_image(con, iid)), 201


@app.get("/api/shows/<int:show_id>/images")
@public_read
def public_images(show_id):
    return jsonify(images.list_images(db(), show_id, editor=is_editor()))


@app.get("/admin/api/shows/<int:show_id>/images")
@editor_required
def admin_images(show_id):
    return jsonify(images.list_images(db(), show_id, editor=True))


@app.patch("/admin/api/images/<int:image_id>")
@editor_required
def patch_image(image_id):
    data = json_body()
    con = db()
    caption = (text(data, "caption", images.MAX_CAPTION) or "") if data.get("caption") is not None else None
    images.update_image(con, image_id, caption=caption, position=data.get("position"))
    return jsonify(images.get_image(con, image_id))


@app.delete("/admin/api/images/<int:image_id>")
@editor_required
def delete_image(image_id):
    images.delete_image(db(), image_id)
    return jsonify(ok=True)


def _serve_image(name, editor):
    if not editor and not settings.PUBLIC_VIEW:
        raise ApiError(404, "Not found")
    if not images.visible_file(db(), name, editor=editor):
        raise ApiError(404, "Not found")
    resp = send_from_directory(settings.IMAGE_DIR, name, mimetype=images.MIME.get(os.path.splitext(name)[1].lower()))
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/images/<name>")
def image_file(name):
    """A file only if this requester may see its row (see images.py); 404 for anything else, a guessed path included."""
    return _serve_image(name, is_editor())


@app.get("/admin/images/<name>")
@editor_required
def admin_image_file(name):
    """The same files for editors under /admin, where Cloudflare Access puts the sign-in."""
    return _serve_image(name, True)



# ==== Task 18: setlist.fm panel
import setlistfm  # noqa: E402


def _setlist_show(show_id, *, public):
    row = db().execute("SELECT id, status, date, date_precision, billing FROM show WHERE id = ?", (show_id,)).fetchone()
    if not row or (public and row["status"] == "upcoming"):
        raise ApiError(404, "Not found")
    return row


@app.get("/api/shows/<int:show_id>/setlist")
@public_read
def show_setlist(show_id):
    """The show's setlist, fetched live (never stored). A friend following a link to an upcoming show gets 404."""
    _setlist_show(show_id, public=True)
    return jsonify(setlistfm.panel(db(), show_id))


@app.get("/admin/api/shows/<int:show_id>/setlist")
@editor_required
def admin_show_setlist(show_id):
    """The same, for pages under /admin (whose requests carry the editor's login), upcoming shows included."""
    _setlist_show(show_id, public=False)
    return jsonify(setlistfm.panel(db(), show_id))


@app.get("/admin/api/shows/<int:show_id>/setlistfm/search")
@editor_required
def admin_setlistfm_search(show_id):
    """setlist.fm matches for the show's headliner on its date. {"available": false} on no key or an upstream error."""
    show = _setlist_show(show_id, public=False)
    if show["date_precision"] != "day":
        raise ApiError(400, "Searching setlist.fm needs a show with a full date.")
    row = db().execute("SELECT a.name FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? "
                       "ORDER BY CASE l.role WHEN 'headliner' THEN 0 WHEN 'coheadliner' THEN 1 ELSE 2 END, "
                       "l.position, l.id LIMIT 1", (show_id,)).fetchone()
    try:
        results = setlistfm.search(row["name"] if row else show["billing"], show["date"])
    except setlistfm.Unavailable as e:
        app.logger.warning("setlist.fm search failed: %s", e)
        return jsonify(available=False, results=[])
    return jsonify(available=True, results=results)


@app.put("/admin/api/shows/<int:show_id>/setlistfm")
@editor_required
def admin_setlistfm_link(show_id):
    _setlist_show(show_id, public=False)
    data = json_body()
    sid, url = text(data, "id", 24, required=True), text(data, "url", 500, required=True)
    if not setlistfm.valid_id(sid):
        raise ApiError(400, "id is not a setlist.fm id.")
    if not setlistfm.valid_url(url):
        raise ApiError(400, "url must be a setlist.fm page.")
    setlistfm.link(db(), show_id, sid, url)
    return jsonify(ok=True)


@app.delete("/admin/api/shows/<int:show_id>/setlistfm")
@editor_required
def admin_setlistfm_unlink(show_id):
    _setlist_show(show_id, public=False)
    setlistfm.unlink(db(), show_id)
    return jsonify(ok=True)


# ==== Task 19: Fetch from a URL


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
