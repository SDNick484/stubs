"""stubs-mcp (post-v1 #46): mcp_http.py, the Streamable HTTP service behind Cloudflare Access, and mcp_remote.py, the
verbatim copy of mcp-server-shared's remote.py it is built on.

Requests go through Starlette's TestClient with Access assertions signed by a key made here. The tools reach the real
app: urllib.request.urlopen is patched to run each request through app.test_client() (and to fail like a stopped
service when the test says Stubs is down), so nothing leaves the process.

Skips when the MCP stack is missing (pip install -r requirements-mcp.txt httpx), unless STUBS_MCP_REQUIRED=1 (the CI
MCP job), where that is a failure."""
import asyncio
import hashlib
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REQUIRED = os.environ.get("STUBS_MCP_REQUIRED") == "1"
try:
    import httpx  # noqa: F401  (Starlette's TestClient)
    import jwt
    import mcp.server.mcpserver  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:  # pragma: no cover - depends on the machine
    if REQUIRED:
        raise
    pytest.skip("the MCP stack is not installed (pip install -r requirements-mcp.txt httpx)", allow_module_level=True)

import app as stubs  # noqa: E402
import mcp_http  # noqa: E402
import mcp_remote  # noqa: E402
import mcp_server  # noqa: E402
from conftest import READ_KEY, WRITE_KEY, mk_show, mk_venue  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_COMMIT = "fc64077c165c5b182f30c59b4ab91a3492831fed"
UPSTREAM_SHA256 = "bfb7780bd3e1cf5d6173d5ec391309ed563e18ed8632b75f69afe0114f1beecf"

AUD = "aud-tag-stubs-mcp"
TEAM = "example.cloudflareaccess.com"
EMAIL = "alex@example.com"
HOST = "mcp.example.com"
PATH = "/stubs/mcp"
READ_TOOLS = {"find_artist", "times_seen", "search_shows", "get_show", "get_stats", "upcoming_shows", "to_confirm",
              "songs_heard", "song_count", "never_heard"}
IMPORT_TOOLS = {"import_shows", "list_imports"}
SHOW = {"date": "2012-08-18", "status": "attended", "billing": "Marigold Static", "venue": "Granite Hall", "city": "Oakland",
        "sources": ["Ticket email"]}


def test_mcp_remote_copy_is_unmodified():
    lines = (ROOT / "mcp_remote.py").read_text(encoding="utf-8").split("\n")
    assert lines[0] == f"# Verbatim copy of SDNick484/mcp-server-shared src/mcp_shared/remote.py @ {UPSTREAM_COMMIT}"
    assert lines[1] == f"# upstream-sha256: {UPSTREAM_SHA256}"
    assert lines[2].startswith("# Hand copy, no sync tooling.")
    body = list(lines)
    while body and body[0].startswith("#"):
        body.pop(0)
    assert hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() == UPSTREAM_SHA256


# ------------------------------------------------------------------ harness
class FakeLoopback:
    """urllib.request.urlopen for the tools and the health check: the Flask app through its test client."""

    def __init__(self):
        self.requests = []  # (method, path, Authorization)
        self.down = False

    def __call__(self, req, timeout=None):
        if isinstance(req, str):
            req = urllib.request.Request(req)
        url = urlsplit(req.full_url)
        if self.down or url.netloc != "127.0.0.1:8773":
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        path = url.path + (f"?{url.query}" if url.query else "")
        headers = dict(req.header_items())
        if path != "/healthz":
            self.requests.append((req.get_method(), path, headers.get("Authorization")))
        r = stubs.app.test_client().open(path, method=req.get_method(), headers=headers, data=req.data)
        if r.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, r.status_code, r.status, {}, io.BytesIO(r.data))
        resp = io.BytesIO(r.data)
        resp.status = r.status_code
        return resp


@pytest.fixture
def loopback(monkeypatch):
    fake = FakeLoopback()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(signing_key):
    def make(key=None, **over):
        now = int(time.time())
        claims = {"iss": f"https://{TEAM}", "aud": [AUD], "iat": now, "exp": now + 300, "email": EMAIL, "sub": "u1"}
        claims.update(over)
        for k in [k for k, v in claims.items() if v is None]:
            del claims[k]
        return jwt.encode(claims, key or signing_key, algorithm="RS256", headers={"kid": "k1"})
    return make


@pytest.fixture
def env():
    """The read-only service's environment, as its init script exports it."""
    return {"STUBS_READ_KEY": READ_KEY, "STUBS_URL": "http://127.0.0.1:8773", "MCP_HTTP_BIND": "127.0.0.1",
            "MCP_HTTP_PORT": "8774", "MCP_HTTP_PATH": PATH, "MCP_PUBLIC_HOSTS": HOST, "CF_TEAM_DOMAIN": TEAM,
            "CF_ACCESS_AUD": AUD, "MCP_ALLOWED_EMAILS": EMAIL}


def import_env(env):
    env = {k: v for k, v in env.items() if k != "STUBS_READ_KEY"}
    env.update(STUBS_MCP_WRITES="import", STUBS_API_KEY=WRITE_KEY)
    return env


@pytest.fixture
def history(con):
    hall = mk_venue(con, "Granite Hall")
    return {"hall": hall, "show": mk_show(con, "The Paper Lanterns", "2009-05-04", hall)}


def make_client(env, signing_key, monkeypatch):
    """The ASGI app for these settings, with the tools on the client main() would build."""
    from starlette.testclient import TestClient

    monkeypatch.setattr(mcp_server, "_default_client", mcp_server.client_for(env))

    async def resolver(_tok):
        return signing_key.public_key()

    policy = mcp_remote.AccessPolicy(team_domain=TEAM, audiences=(AUD,),
                                     emails=frozenset(e for e in env.get("MCP_ALLOWED_EMAILS", "").split(",") if e))
    app = mcp_http.build_http_app(env, verifier=mcp_remote.AccessVerifier(policy, key_resolver=resolver))
    return TestClient(app, base_url=f"http://{HOST}")


@pytest.fixture
def client(env, signing_key, loopback, history, monkeypatch):
    with make_client(env, signing_key, monkeypatch) as c:
        yield c


def rpc(client, method, params=None, *, token=None, sid=None, path=PATH, host=None, id_=1):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Cf-Access-Jwt-Assertion"] = token
    if sid:
        headers["Mcp-Session-Id"] = sid
    if host:
        headers["Host"] = host
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return client.post(path, headers=headers, json=body)


def payload(resp):
    """The JSON-RPC message from a plain JSON or an SSE body."""
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return resp.json()


def open_session(client, tok):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "claude-android", "version": "1.0"}}, token=tok)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    assert rpc(client, "notifications/initialized", token=tok, sid=sid, id_=None).status_code == 202
    return sid


def tool_names(client, tok, sid):
    return {t["name"] for t in payload(rpc(client, "tools/list", {}, token=tok, sid=sid, id_=2))["result"]["tools"]}


def call_tool(client, tok, sid, name, args, id_=3):
    return payload(rpc(client, "tools/call", {"name": name, "arguments": args}, token=tok, sid=sid, id_=id_))


# ------------------------------------------------------------------ Access, host, path
REJECTS = {
    "no_header": lambda tok, other: None,
    "garbage": lambda tok, other: "not-a-jwt",
    "wrong_aud": lambda tok, other: tok(aud=["some-other-app"]),
    "expired": lambda tok, other: tok(exp=int(time.time()) - 600, iat=int(time.time()) - 900),
    "other_key": lambda tok, other: tok(key=other),
    "email_not_allowed": lambda tok, other: tok(email="sam@example.com"),
    "no_email": lambda tok, other: tok(email=None),
}


@pytest.mark.parametrize("case", REJECTS)
def test_reject_matrix(client, token, other_key, loopback, case):
    r = rpc(client, "tools/call", {"name": "times_seen", "arguments": {"artist": "The Paper Lanterns"}},
            token=REJECTS[case](token, other_key))
    assert (r.status_code, r.json()) == (403, {"error": "forbidden"})
    assert loopback.requests == []


def test_a_signed_in_call_reads_stubs_with_the_read_key(client, token, loopback, history):
    tok = token()
    sid = open_session(client, tok)
    msg = call_tool(client, tok, sid, "times_seen", {"artist": "The Paper Lanterns"})
    assert msg["result"]["isError"] is False
    out = msg["result"]["structuredContent"]["result"]
    assert out["times"] == 1 and out["shows"][0]["venue"] == "Granite Hall"
    assert loopback.requests and all(auth == f"Bearer {READ_KEY}" for _, _, auth in loopback.requests)


def test_default_is_read_only(client, token):
    tok = token()
    assert tool_names(client, tok, open_session(client, tok)) == READ_TOOLS


@pytest.mark.parametrize("writes,expected", [("none", READ_TOOLS), ("bogus", READ_TOOLS),
                                             ("import", READ_TOOLS | IMPORT_TOOLS)])
def test_tools_list_by_level(env, signing_key, loopback, history, token, monkeypatch, writes, expected):
    env = import_env(env) if writes == "import" else {**env, "STUBS_MCP_WRITES": writes}
    with make_client(env, signing_key, monkeypatch) as c:
        tok = token()
        assert tool_names(c, tok, open_session(c, tok)) == expected


def test_read_only_call_to_import_shows_is_unknown(client, token, loopback, con):
    tok = token()
    sid = open_session(client, tok)
    msg = call_tool(client, tok, sid, "import_shows", {"label": "Ticket emails", "shows": [SHOW]})
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == "Unknown tool: import_shows"
    assert loopback.requests == [] and con.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0


def test_import_level_sends_a_batch_to_review(env, signing_key, loopback, history, token, monkeypatch, con):
    with make_client(import_env(env), signing_key, monkeypatch) as c:
        tok = token()
        sid = open_session(c, tok)
        msg = call_tool(c, tok, sid, "import_shows", {"label": "Ticket emails", "shows": [SHOW]})
    assert msg["result"]["isError"] is False
    out = msg["result"]["structuredContent"]["result"]
    assert out["counts"]["new"] == 1 and out["review_link"] == f"/admin#/admin/import/{out['batch_id']}"
    assert con.execute("SELECT status FROM import_batch").fetchone()[0] == "pending"
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 1
    assert [(m, p, a) for m, p, a in loopback.requests] == [("POST", "/admin/api/imports", f"Bearer {WRITE_KEY}")]


def test_tools_list_carries_annotations_on_the_wire(env, signing_key, loopback, history, token, monkeypatch):
    with make_client(import_env(env), signing_key, monkeypatch) as c:
        tok = token()
        tools = payload(rpc(c, "tools/list", {}, token=tok, sid=open_session(c, tok), id_=2))["result"]["tools"]
    ann = {t["name"]: t["annotations"] for t in tools}
    assert ann["times_seen"]["readOnlyHint"] is True and ann["list_imports"]["readOnlyHint"] is True
    assert ann["import_shows"]["readOnlyHint"] is False and ann["import_shows"]["destructiveHint"] is False


def test_path_prefix(client, token, env, signing_key, loopback, history, monkeypatch):
    tok = token()
    open_session(client, tok)
    r = rpc(client, "tools/list", {}, token=tok, path="/mcp")
    assert (r.status_code, r.text) == (404, "Not Found")
    assert client.get("/stubs/healthz", headers={"Cf-Access-Jwt-Assertion": tok}).status_code == 404
    with make_client({**env, "MCP_HTTP_PATH": "/x/mcp"}, signing_key, monkeypatch) as moved:
        assert rpc(moved, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}},
                   token=tok, path="/x/mcp").status_code == 200
        assert rpc(moved, "tools/list", {}, token=tok, path=PATH).status_code == 404


def test_wrong_host_is_421(client, token):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}}, token=token(), host="evil.example.com")
    assert (r.status_code, r.text) == (421, "Invalid Host header")


def test_stale_session_id_is_404(client, token, loopback):
    r = rpc(client, "tools/call", {"name": "get_stats", "arguments": {}}, token=token(), sid="deadbeef" * 4)
    assert r.status_code == 404 and loopback.requests == []


# ------------------------------------------------------------------ health and errors
def test_health_ok_without_auth(client):
    r = client.get("/healthz")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


def test_health_503_when_stubs_is_down(client, loopback):
    loopback.down = True
    r = client.get("/healthz")
    assert (r.status_code, r.json()) == (503, {"status": "degraded", "error": "health check returned False"})


def test_stubs_unreachable_tool_message(client, token, loopback):
    tok = token()
    sid = open_session(client, tok)
    loopback.down = True
    msg = call_tool(client, tok, sid, "get_stats", {})
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == \
        "Error executing tool get_stats: Stubs is unreachable right now. Try again in a minute."


def test_a_rejected_key_names_the_setting_not_the_key(env, signing_key, loopback, history, token, monkeypatch):
    with make_client({**env, "STUBS_READ_KEY": "not-the-read-key"}, signing_key, monkeypatch) as c:
        tok = token()
        sid = open_session(c, tok)
        text = call_tool(c, tok, sid, "get_show", {"show_id": history["show"]})["result"]["content"][0]["text"]
    assert text == ("Error executing tool get_show: Stubs rejected the service's key (HTTP 401). "
                    "Check STUBS_READ_KEY on the Stubs host.")


def test_secrets_never_logged(client, token, other_key, loopback, caplog):
    caplog.set_level(logging.DEBUG)
    good, bad = token(), token(key=other_key)
    bodies = [rpc(client, "tools/list", {}, token=bad).text, rpc(client, "tools/list", {}).text]
    sid = open_session(client, good)
    bodies.append(str(call_tool(client, good, sid, "get_stats", {})))
    loopback.down = True
    bodies.append(str(call_tool(client, good, sid, "get_stats", {}, id_=4)))
    blob = caplog.text + "\n".join(bodies)
    for secret in (READ_KEY, WRITE_KEY, good, bad, "Bearer"):
        assert secret not in blob


# ------------------------------------------------------------------ settings and main()
@pytest.mark.parametrize("team", [TEAM, f"https://{TEAM}/"])
def test_team_domain_comes_from_cf_team_domain(env, team):
    env["CF_TEAM_DOMAIN"] = team
    assert mcp_http._http_config(env).access.team_domain == TEAM
    env["CF_ACCESS_TEAM_DOMAIN"] = ""
    assert mcp_http._http_config(env).access.team_domain == TEAM


def test_defaults_port_8774_and_path(env):
    cfg = mcp_http._http_config({k: v for k, v in env.items() if k not in ("MCP_HTTP_PORT", "MCP_HTTP_PATH")})
    assert (cfg.bind, cfg.port, cfg.path, cfg.public_hosts) == ("127.0.0.1", 8774, "/stubs/mcp", (HOST,))


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_remote, "serve_http", lambda server, cfg, **kw: calls.append((server, cfg, kw)))
    monkeypatch.setattr(mcp_server, "_default_client", None)
    return calls


def tools_of(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_main_serves_read_only_with_the_read_key(served, env):
    assert mcp_http.main([], env) == 0
    server, cfg, kw = served[0]
    assert (cfg.port, cfg.path) == (8774, PATH) and callable(kw["health_check"])
    assert tools_of(server) == READ_TOOLS
    client = mcp_server._default_client
    assert (client.api_url, client.api_key, client.key_name) == ("http://127.0.0.1:8773", READ_KEY, "STUBS_READ_KEY")


@pytest.mark.parametrize("writes", [None, "none", " NONE ", "bogus"])
def test_main_refuses_the_write_key_at_none(served, env, capsys, writes):
    env["STUBS_API_KEY"] = WRITE_KEY
    if writes is not None:
        env["STUBS_MCP_WRITES"] = writes
    assert mcp_http.main([], env) == 2
    err = capsys.readouterr().err
    assert "STUBS_API_KEY must not be in" in err and WRITE_KEY not in err
    assert served == []


@pytest.mark.parametrize("value", [None, "", "  "])
def test_main_refuses_an_empty_read_key(served, env, capsys, value):
    if value is None:
        del env["STUBS_READ_KEY"]
    else:
        env["STUBS_READ_KEY"] = value
    assert mcp_http.main([], env) == 2
    assert "STUBS_READ_KEY is empty" in capsys.readouterr().err and served == []


def test_main_import_level_uses_the_write_key(served, env, capsys):
    assert mcp_http.main([], import_env(env)) == 0
    server, _, _ = served[0]
    assert tools_of(server) == READ_TOOLS | IMPORT_TOOLS
    assert (mcp_server._default_client.api_key, mcp_server._default_client.key_name) == (WRITE_KEY, "STUBS_API_KEY")
    assert "writes: import" in capsys.readouterr().err


def test_main_import_level_needs_the_write_key(served, env, capsys):
    env["STUBS_MCP_WRITES"] = "import"  # holds only the read key, which can't stand in for it
    assert mcp_http.main([], env) == 2
    assert "STUBS_API_KEY is empty" in capsys.readouterr().err and served == []


def test_main_warns_about_an_unknown_level(served, env, capsys):
    env["STUBS_MCP_WRITES"] = "all"
    assert mcp_http.main([], env) == 0
    assert tools_of(served[0][0]) == READ_TOOLS
    assert "unknown STUBS_MCP_WRITES 'all'; using none" in capsys.readouterr().err


def test_main_derives_stubs_url_from_port(served, env):
    env["STUBS_URL"] = ""
    env["PORT"] = "8800"
    assert mcp_http.main([], env) == 0
    assert mcp_server._default_client.api_url == "http://127.0.0.1:8800"


def test_main_refuses_a_public_bind_without_access(served, capsys):
    assert mcp_http.main([], {"STUBS_READ_KEY": READ_KEY, "MCP_HTTP_BIND": "192.168.1.100"}) == 2
    assert "Cloudflare Access" in capsys.readouterr().err and served == []


def test_main_refuses_a_public_host_without_access(served, capsys):
    assert mcp_http.main([], {"STUBS_READ_KEY": READ_KEY, "MCP_PUBLIC_HOSTS": HOST}) == 2
    assert "Cloudflare Access" in capsys.readouterr().err and served == []


def test_main_refuses_access_without_an_aud(served, env, capsys):
    env["CF_ACCESS_AUD"] = ""
    assert mcp_http.main([], env) == 2
    assert "CF_ACCESS_AUD is empty" in capsys.readouterr().err and served == []


def test_stubs_healthy(loopback):
    assert mcp_http.stubs_healthy("http://127.0.0.1:8773") is True
    loopback.down = True
    assert mcp_http.stubs_healthy("http://127.0.0.1:8773") is False


def test_mcp_http_never_imports_the_app():
    source = (ROOT / "mcp_http.py").read_text(encoding="utf-8")
    for module in ("app", "settings", "common", "db", "flask", "sqlite3"):
        assert f"import {module}\n" not in source and f"from {module} " not in source, module
