"""Who is an editor (Cloudflare Access JWT, LAN, write key, read key), the public_read gate, the cross-site refusal
and the headers every response carries."""
import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import app as stubs
import settings
from common import ApiError
from conftest import READ_KEY, WRITE_KEY

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


@stubs.public_read
def _public_view():
    return {"ok": True}


@stubs.editor_required
def _editor_view():
    return {"ok": True}


def call(view, path="/api/x", method="GET", headers=None):
    """Run a decorated view inside a request: its return value, or the ApiError's (status, message)."""
    with stubs.app.test_request_context(path, method=method, headers=headers or {}):
        try:
            return view()
        except ApiError as e:
            return e.status, e.message


# ---------------------------------------------------------------- the session and the public gate
def test_session_anonymous(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    assert r.get_json() == {"editor": False, "public": True, "app_name": "Stubs",
                            "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}


def test_admin_session_is_the_same_payload(client, editor):
    assert client.get("/admin/api/session").get_json()["editor"] is False
    assert editor.get("/admin/api/session").get_json()["editor"] is True


def test_public_view_off_blocks_api(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    r = client.get("/api/session")
    assert r.status_code == 200 and r.get_json()["public"] is False
    assert call(_public_view) == (403, "Sign in to view")
    assert call(_public_view, headers={"X-API-Key": WRITE_KEY}) == {"ok": True}
    assert call(_public_view, headers={"X-API-Key": READ_KEY}) == {"ok": True}
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin")
    assert client.get("/admin").status_code == 200


def test_public_view_on_lets_anyone_read(client):
    assert call(_public_view) == {"ok": True}
    assert client.get("/").status_code == 200


def test_403_body_is_json(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    with stubs.app.test_request_context("/api/x"):
        try:
            _public_view()
        except ApiError as e:
            resp = stubs.app.make_response(stubs.app.handle_user_exception(e))
    assert resp.status_code == 403 and resp.get_json() == {"error": "Sign in to view"}


# ---------------------------------------------------------------- keys
def test_write_key_is_editor_and_read_key_cannot_write(client):
    assert client.get("/api/session", headers={"X-API-Key": WRITE_KEY}).get_json()["editor"] is True
    assert client.get("/api/session", headers={"Authorization": f"Bearer {WRITE_KEY}"}).get_json()["editor"] is True
    assert client.get("/api/session", headers={"X-API-Key": READ_KEY}).get_json()["editor"] is True
    assert client.get("/api/session", headers={"X-API-Key": "wrong"}).get_json()["editor"] is False

    assert call(_editor_view, "/admin/api/x", "POST", {"X-API-Key": WRITE_KEY}) == {"ok": True}
    assert call(_editor_view, "/admin/api/x", "GET", {"X-API-Key": READ_KEY}) == {"ok": True}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert call(_editor_view, "/admin/api/x", method, {"X-API-Key": READ_KEY})[0] == 403, method
    assert call(_editor_view, "/admin/api/x", "POST") == (401, "Sign in to edit")


def test_empty_keys_unlock_nothing(client, monkeypatch):
    monkeypatch.setattr(settings, "STUBS_API_KEY", "")
    monkeypatch.setattr(settings, "STUBS_READ_KEY", "")
    assert client.get("/api/session", headers={"X-API-Key": ""}).get_json()["editor"] is False
    assert client.get("/api/session", headers={"Authorization": "Bearer "}).get_json()["editor"] is False


# ---------------------------------------------------------------- cross-site
def test_cross_site_browser_request_refused(client):
    evil = {"X-API-Key": WRITE_KEY, "Origin": "https://evil.example.com"}
    assert call(_editor_view, "/admin/api/x", "POST", evil) == (403, "Cross-site request refused")
    assert call(_editor_view, "/admin/api/x", "POST", {"X-API-Key": WRITE_KEY, "Sec-Fetch-Site": "cross-site"})[0] == 403
    same = {"X-API-Key": WRITE_KEY, "Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}
    assert call(_editor_view, "/admin/api/x", "POST", same) == {"ok": True}
    # behind the tunnel the app sees http while the browser's Origin says https: only the host is compared
    assert call(_editor_view, "/admin/api/x", "POST", {**same, "Origin": "https://localhost"}) == {"ok": True}


# ---------------------------------------------------------------- LAN
def test_lan_edit_needs_a_lan_host_and_no_cloudflare(client, monkeypatch):
    monkeypatch.setattr(settings, "LAN_EDIT", True)
    monkeypatch.setattr(settings, "LAN_HOSTS", {"192.168.1.100", "localhost"})
    lan = "http://192.168.1.100:8773"
    assert client.get("/api/session", base_url=lan).get_json()["editor"] is True
    assert client.get("/api/session").get_json()["editor"] is True  # Host: localhost
    assert client.get("/api/session", base_url="http://concerts.example.com").get_json()["editor"] is False
    assert client.get("/api/session", base_url=lan, headers={"Cf-Ray": "abc"}).get_json()["editor"] is False
    assert client.get("/api/session", base_url=lan, headers={"Cf-Connecting-Ip": "203.0.113.9"}).get_json()["editor"] is False
    monkeypatch.setattr(settings, "LAN_EDIT", False)
    assert client.get("/api/session", base_url=lan).get_json()["editor"] is False


def test_lan_hosts_setting_parses_hosts():
    assert settings.parse_hosts(" 192.168.1.100:8773, Stubs.LAN ,,[::1]:8773") == {"192.168.1.100", "stubs.lan", "::1"}


# ---------------------------------------------------------------- Cloudflare Access
@pytest.fixture
def access(monkeypatch):
    """CF settings plus a JWKS client that serves a key made here; returns a function that signs a token."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "CF_TEAM_DOMAIN", "team.example.com")
    monkeypatch.setattr(settings, "CF_AUD", "aud-123")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", {"alex@example.com"})
    monkeypatch.setattr(stubs, "_jwks", types.SimpleNamespace(
        get_signing_key_from_jwt=lambda token: types.SimpleNamespace(key=key.public_key())))

    def sign(email, aud="aud-123", iss="https://team.example.com", exp=None):
        claims = {"email": email, "aud": aud, "iss": iss, "exp": exp or int(time.time()) + 600}
        return jwt.encode(claims, key, algorithm="RS256")
    return sign


def test_access_jwt_with_listed_email_is_editor(client, access):
    h = {"Cf-Access-Jwt-Assertion": access("Alex@Example.com"), "Cf-Ray": "abc"}
    assert client.get("/api/session", headers=h).get_json()["editor"] is True


@pytest.mark.parametrize("kw", [{"email": "sam@example.com"}, {"email": "alex@example.com", "aud": "other"},
                                {"email": "alex@example.com", "iss": "https://evil.example.com"},
                                {"email": "alex@example.com", "exp": 1000}])
def test_access_jwt_refused(client, access, kw):
    h = {"Cf-Access-Jwt-Assertion": access(**kw), "Cf-Ray": "abc"}
    assert client.get("/api/session", headers=h).get_json()["editor"] is False


def test_access_needs_admin_emails(client, access, monkeypatch):
    """Spec section 6: the JWT's email must be in ADMIN_EMAILS. An empty list lets nobody in through Access."""
    monkeypatch.setattr(settings, "ADMIN_EMAILS", set())
    h = {"Cf-Access-Jwt-Assertion": access("alex@example.com"), "Cf-Ray": "abc"}
    assert client.get("/api/session", headers=h).get_json()["editor"] is False


def test_a_bad_token_is_not_a_lan_editor(client, access, monkeypatch):
    monkeypatch.setattr(settings, "LAN_EDIT", True)
    assert client.get("/api/session", headers={"Cf-Access-Jwt-Assertion": "garbage"}).get_json()["editor"] is False


def test_team_domain_tolerates_a_pasted_url():
    assert settings.team_domain(" https://team.cloudflareaccess.com/ ") == "team.cloudflareaccess.com"


# ---------------------------------------------------------------- headers and errors
def test_headers_on_every_response(client):
    r = client.get("/healthz")
    assert r.get_json() == {"ok": True}
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "img-src 'self' data: https://tile.openstreetmap.org" in r.headers["Content-Security-Policy"]
    assert r.headers["Content-Security-Policy"] == CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    for path in ("/", "/manifest.json", "/static/style.css", "/nope", "/api/session", "/admin/api/session"):
        r = client.get(path)
        assert r.headers["Content-Security-Policy"] == CSP, path
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow", path
    assert client.get("/api/session").headers["Cache-Control"] == "no-store"
    assert client.get("/admin/api/session").headers["Cache-Control"] == "no-store"
    assert client.get("/").headers.get("Cache-Control") != "no-store"


def test_csp_follows_the_tile_host(client, monkeypatch):
    monkeypatch.setattr(settings, "TILE_URL", "https://{s}.tiles.example.com/{z}/{x}/{y}.png")
    assert "img-src 'self' data: https://*.tiles.example.com;" in client.get("/healthz").headers["Content-Security-Policy"]


def test_errors_are_json(client):
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and r.get_json() == {"error": "Not found"}
    r = client.post("/healthz")
    assert r.status_code == 405 and "error" in r.get_json()
