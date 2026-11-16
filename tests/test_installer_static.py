"""The installer is built, not run: these tests check what can be checked without an Alpine host."""
import base64
import io
import os
import re
import subprocess
import tarfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dist", "setup-stubs.sh")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _build():
    subprocess.run(["sh", "installer/build.sh"], cwd=ROOT, check=True, capture_output=True)
    return OUT


def _payload_names(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    raw = base64.b64decode(text.split("\n__PAYLOAD_BELOW__\n", 1)[1])
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        return set(tar.getnames())


def _settings_keys():
    return set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', _read("settings.py")))


# The stubs-mcp service's settings (post-v1 #46): exported by its own init script, not gunicorn's.
MCP_KEYS = {"STUBS_URL", "STUBS_MCP_WRITES", "MCP_HTTP_BIND", "MCP_HTTP_PORT", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS",
            "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS"}


def test_build_produces_a_valid_installer():
    subprocess.run(["sh", "installer/build.sh"], cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["sh", "-n", "dist/setup-stubs.sh"], cwd=ROOT, check=True)
    assert os.access(OUT, os.X_OK)


def test_every_setting_has_env_example_and_add_setting():
    keys = _settings_keys()
    assert {"PORT", "DATA_DIR", "CF_TEAM_DOMAIN", "CF_AUD", "ADMIN_EMAILS", "LAN_EDIT", "LAN_HOSTS", "PUBLIC_VIEW",
            "PUBLIC_POSTERS", "STUBS_API_KEY", "STUBS_READ_KEY", "SETLISTFM_API_KEY", "GEOCODER_URL", "MUSICBRAINZ_URL",
            "TILE_URL",
            # post-v1 #47: concert watch
            "TICKETMASTER_API_KEY", "LASTFM_API_KEY", "LASTFM_USER", "WATCH_NOTIFY_URL", "WATCH_NOTIFY_FORMAT"} | MCP_KEYS == keys
    env_example, head = _read(".env.example"), _read("installer/setup-head.sh")
    exports = re.search(r"^export (PORT .*)$", head, re.M)
    assert exports, "the init script exports the settings on one `export PORT ...` line"
    mcp_exports = re.search(r"^export (STUBS_URL .*)$", _mcp_init(), re.M)
    assert mcp_exports, "the stubs-mcp init script exports its settings on one `export STUBS_URL ...` line"
    for key in keys:
        assert re.search(rf"^#?\s*{key}=", env_example, re.M), f"{key} missing from .env.example"
        assert re.search(rf'^add_setting "\$CONF" {key} ', head, re.M), f"{key} missing from the installer's settings"
        if key in MCP_KEYS:
            assert key in mcp_exports.group(1).split(), f"{key} not exported to stubs-mcp by its init script"
            assert key not in exports.group(1).split(), f"{key} is stubs-mcp's, not gunicorn's"
        else:
            assert key in exports.group(1).split(), f"{key} not exported to gunicorn by the init script"


def test_installer_binds_loopback_on_8773():
    head = _read("installer/setup-head.sh")
    assert "--bind 127.0.0.1:${PORT}" in head and "0.0.0.0" not in head
    assert 'PORT="${STUBS_PORT:-8773}"' in head and ": ${PORT:=8773}" in head
    import settings
    assert settings.PORT == 8773


def test_installer_layout():
    head = _read("installer/setup-head.sh")
    for needle in ("APP_DIR=/opt/stubs", "DATA_DIR=/var/lib/stubs", "CONF=/etc/conf.d/stubs", "SVC_USER=stubs",
                   '"$DATA_DIR/images"', "/etc/periodic/daily/stubs-backup",
                   "apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite"):
        assert needle in head, needle
    assert "chmod 600 \"$CONF\"" in head  # keys live there: root only
    backup = _read("ops/stubs-backup")
    assert "KEEP_DAYS=${KEEP_DAYS:-14}" in backup and "stubs.db" in backup and "images" in backup


def test_payload_has_every_top_level_py():
    names = _payload_names(_build())
    modules = [f for f in os.listdir(ROOT) if f.endswith(".py")]
    assert {"app.py", "settings.py", "db.py", "common.py"} <= set(modules)
    for module in modules:
        assert f"stubs/{module}" in names, module
    for rel in ("static/index.html", "static/js/main.js", "static/vendor/README.md", "ops/stubs-backup", "README.md",
                ".env.example", "requirements-mcp.txt", "mcp_server.py", "mcp_http.py", "mcp_remote.py"):
        assert f"stubs/{rel}" in names, rel
    assert not any(n.startswith(("stubs/tests", "stubs/data", "stubs/dist")) or "__pycache__" in n for n in names)


def _add_setting_fn():
    head = _read("installer/setup-head.sh")
    start = head.index("add_setting() {")
    return head[start:head.index("\n}\n", start) + 3]


def test_add_setting_only_adds_missing_keys(tmp_path):
    conf = tmp_path / "stubs"
    conf.write_text("PORT='9000'\n")
    script = _add_setting_fn() + (
        f'add_setting "{conf}" PORT 8773 "port"\n'
        f'add_setting "{conf}" TILE_URL "https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png" "tiles"\n'
        f'add_setting "{conf}" TILE_URL "other" "tiles"\n'
        'echo "added:$ADDED"\n')
    out = subprocess.run(["sh", "-c", script], check=True, capture_output=True, text=True).stdout
    body = conf.read_text()
    assert "PORT='9000'" in body and "PORT=8773" not in body and "PORT='8773'" not in body
    assert body.count("TILE_URL=") == 1 and "TILE_URL='https://tile.openstreetmap.org/{z}/{x}/{y}.png'" in body
    assert out.strip() == "added: TILE_URL"
    sourced = subprocess.run(["sh", "-c", f'. "{conf}"; printf %s "$TILE_URL"'], check=True, capture_output=True,
                             text=True).stdout
    assert sourced == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def test_build_copies_by_glob():
    build = _read("installer/build.sh")
    assert "*.py" in build and "README.md" in build and ".env.example" in build and "ops" in build and "static" in build


# ---------------------------------------------------------------- stubs-mcp (post-v1 #46)
def _mcp_init():
    m = re.search(r"cat > \"\$MCP_INITD\" <<'EOF'\n(.*?)\nEOF\n", _read("installer/setup-head.sh"), re.S)
    assert m, "the stubs-mcp init script heredoc is missing"
    return m.group(1)


def _shell_fn(name, text):
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 3]


def test_mcp_init_script():
    s = _mcp_init()
    for needle in ('name="stubs-mcp"', "[ -r /etc/conf.d/stubs ] && . /etc/conf.d/stubs", 'supervisor="supervise-daemon"',
                   'command="/opt/stubs-mcp/venv/bin/python"', 'command_args="/opt/stubs/mcp_http.py"',
                   'command_user="stubs-mcp:stubs-mcp"', 'directory="/opt/stubs"', "/var/log/stubs-mcp.log",
                   "need net stubs", ": ${STUBS_URL:=http://127.0.0.1:${PORT}}"):
        assert needle in s, needle
    export = next(line for line in s.splitlines() if line.startswith("export STUBS_URL "))
    # one key, chosen by the write level below; nothing else secret reaches this service
    for key in ("STUBS_API_KEY", "STUBS_READ_KEY", "SETLISTFM_API_KEY", "CF_AUD", "ADMIN_EMAILS", "DATA_DIR"):
        assert key not in export.split(), key
    assert "CF_TEAM_DOMAIN" in export.split()
    case = s[s.index('case "$(mcp_level'):]
    case = case[:case.index("esac")]
    assert "import) unset STUBS_READ_KEY; export STUBS_API_KEY ;;" in case
    assert "*) unset STUBS_API_KEY; export STUBS_READ_KEY ;;" in case


def test_mcp_level_in_shell_mirrors_mcp_server():
    import mcp_server
    head = _read("installer/setup-head.sh")
    fn = _shell_fn("mcp_level", head)
    assert fn == _shell_fn("mcp_level", _mcp_init())  # the installer and the init script agree
    for raw in ("", "none", "NONE", "import", " Import ", "IMPORT", "all", "apply", "im port", "imports"):
        out = subprocess.run(["sh", "-c", fn + 'mcp_level "$1"', "sh", raw], check=True, capture_output=True,
                             text=True).stdout.strip()
        assert out == mcp_server.write_level({"STUBS_MCP_WRITES": raw}), raw


@pytest.mark.parametrize("key,value", [
    ("STUBS_URL", '""'), ("STUBS_MCP_WRITES", "none"), ("MCP_HTTP_BIND", "127.0.0.1"), ("MCP_HTTP_PORT", "8774"),
    ("MCP_HTTP_PATH", "/stubs/mcp"), ("MCP_PUBLIC_HOSTS", '""'), ("CF_ACCESS_AUD", '""'), ("MCP_ALLOWED_EMAILS", '""'),
])
def test_mcp_settings_are_added_with_defaults(key, value):
    assert re.search(rf'^add_setting "\$CONF" {key} {re.escape(value)} ".+"$', _read("installer/setup-head.sh"), re.M)


def test_mcp_settings_defaults_match_the_code():
    import mcp_server
    env_example = _read(".env.example")
    for key, value in (("STUBS_MCP_WRITES", "none"), ("MCP_HTTP_BIND", "127.0.0.1"), ("MCP_HTTP_PORT", "8774"),
                       ("MCP_HTTP_PATH", "/stubs/mcp"), ("STUBS_URL", "")):
        assert re.search(rf"^{key}={re.escape(value)}$", env_example, re.M), key
    assert mcp_server.stubs_url({}) == "http://127.0.0.1:8773"
    import settings  # the app lists the same defaults
    assert (settings.STUBS_URL, settings.STUBS_MCP_WRITES, settings.MCP_HTTP_BIND, settings.MCP_HTTP_PORT,
            settings.MCP_HTTP_PATH) == ("http://127.0.0.1:8773", "none", "127.0.0.1", 8774, "/stubs/mcp")
    assert "CF_ACCESS_TEAM_DOMAIN" not in _read("installer/setup-head.sh")  # stubs-mcp reuses CF_TEAM_DOMAIN


def test_mcp_user_and_venv():
    head = _read("installer/setup-head.sh")
    for needle in ("MCP_USER=stubs-mcp", "MCP_VENV=/opt/stubs-mcp/venv", "MCP_INITD=/etc/init.d/stubs-mcp"):
        assert needle in head, needle
    # its own group, no home, no shell, and not in the stubs group: /var/lib/stubs (0750 stubs:stubs) is closed to it
    assert 'adduser -S -D -H -h /nonexistent -s /sbin/nologin -G "$MCP_USER" "$MCP_USER"' in head
    assert 'addgroup "$MCP_USER" "$SVC_USER"' not in head and 'chmod 750 "$DATA_DIR"' in head
    pip = next(line for line in head.splitlines() if 'pip" install' in line and not line.lstrip().startswith("#"))
    assert '-r "$APP_DIR/requirements-mcp.txt"' in pip and 'python3 -m venv "$MCP_VENV"' in pip
    assert "||" in pip and "exit" not in pip  # a pip failure skips stubs-mcp, never the app


def test_mcp_starts_only_when_configured_and_is_health_checked():
    head = _read("installer/setup-head.sh")
    lines = head.splitlines()
    idx = [i for i, line in enumerate(lines) if line.strip().startswith("rc-service stubs-mcp start")]
    assert len(idx) == 1
    conds, indent = [], len(lines[idx[0]]) - len(lines[idx[0]].lstrip())
    for line in reversed(lines[:idx[0]]):  # every `if` the start is nested in
        depth = len(line) - len(line.lstrip())
        if line.lstrip().startswith("if ") and depth < indent:
            conds.append(line)
            indent = depth
    cond = " ".join(conds)
    assert "netstat" in cond  # a port someone else holds is reported, not fought over
    for needle in ("MCP_OK", "CF_ACCESS_AUD", "MCP_PUBLIC_HOSTS", '"$(mcp_key_name)"'):
        assert needle in cond, needle
    line = 'add_health_check "Stubs MCP|http://$MCP_BIND:$MCP_PORT/healthz|stubs-mcp" stubs-mcp'
    assert line in head and head.index(line) > head.index("rc-service stubs-mcp start")  # only once it answered


def test_mcp_uninstall():
    head = _read("installer/setup-head.sh")
    block = head[head.index('if [ -n "$UNINSTALL" ]'):head.index("exit 0")]
    for needle in ("rc-service stubs-mcp stop", "rc-update del stubs-mcp", 'rm -f "$MCP_INITD"', "rm -rf /opt/stubs-mcp"):
        assert needle in block, needle


def test_requirements_mcp_pins():
    text = _read("requirements-mcp.txt")
    for pin in ("mcp>=2.3,<3", "uvicorn>=0.31", "pyjwt[crypto]>=2.10"):
        assert pin in text
    assert not re.search(r"^\s*flask", text, re.I | re.M)  # the app's own dependencies stay apk-only


def test_mcp_setup_doc():
    text = _read("docs/mcp-setup.md")
    for needle in ("https://mcp.example.com/stubs/mcp", "^/stubs/", "http://127.0.0.1:8774", "STUBS_MCP_WRITES",
                   "rc-service stubs-mcp restart", "vim /etc/conf.d/stubs", "concerts.example.com", "import_shows"):
        assert needle in text, needle
    assert "systemctl" not in text
