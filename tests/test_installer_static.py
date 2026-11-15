"""The installer is built, not run: these tests check what can be checked without an Alpine host."""
import base64
import io
import os
import re
import subprocess
import tarfile

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


def test_build_produces_a_valid_installer():
    subprocess.run(["sh", "installer/build.sh"], cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["sh", "-n", "dist/setup-stubs.sh"], cwd=ROOT, check=True)
    assert os.access(OUT, os.X_OK)


def test_every_setting_has_env_example_and_add_setting():
    keys = _settings_keys()
    assert {"PORT", "DATA_DIR", "CF_TEAM_DOMAIN", "CF_AUD", "ADMIN_EMAILS", "LAN_EDIT", "LAN_HOSTS", "PUBLIC_VIEW",
            "PUBLIC_POSTERS", "STUBS_API_KEY", "STUBS_READ_KEY", "SETLISTFM_API_KEY", "GEOCODER_URL", "MUSICBRAINZ_URL",
            "TILE_URL"} == keys
    env_example, head = _read(".env.example"), _read("installer/setup-head.sh")
    exports = re.search(r"^export (PORT .*)$", head, re.M)
    assert exports, "the init script exports the settings on one `export PORT ...` line"
    for key in keys:
        assert re.search(rf"^#?\s*{key}=", env_example, re.M), f"{key} missing from .env.example"
        assert re.search(rf'^add_setting "\$CONF" {key} ', head, re.M), f"{key} missing from the installer's settings"
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
    assert "pip" not in head and "venv" not in head  # no MCP user or venv in v1
    backup = _read("ops/stubs-backup")
    assert "KEEP_DAYS=${KEEP_DAYS:-14}" in backup and "stubs.db" in backup and "images" in backup


def test_payload_has_every_top_level_py():
    names = _payload_names(_build())
    modules = [f for f in os.listdir(ROOT) if f.endswith(".py")]
    assert {"app.py", "settings.py", "db.py", "common.py"} <= set(modules)
    for module in modules:
        assert f"stubs/{module}" in names, module
    for rel in ("static/index.html", "static/js/main.js", "static/vendor/README.md", "ops/stubs-backup", "README.md",
                ".env.example"):
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
