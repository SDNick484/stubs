"""Configuration, read once from the environment at import. Before reading, /etc/conf.d/stubs and then ./.env fill
in any key the environment does not already have (as Matinee). An empty value means the default.

  PORT               port for `python3 app.py` and the installer's gunicorn bind (default 8773)
  DATA_DIR           where stubs.db and images/ live (default ./data; the installer uses /var/lib/stubs)
  CF_TEAM_DOMAIN     Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com (a pasted https:// is fine)
  CF_AUD             AUD tag of the Access application on /admin
  ADMIN_EMAILS       comma-separated emails that may edit through Access; empty means nobody through Access
  LAN_EDIT           1 (default): a request that did not come through Cloudflare, to a host in LAN_HOSTS, may edit
  LAN_HOSTS          comma-separated host names or addresses (port ignored) that count as the LAN (default
                     localhost,127.0.0.1)
  PUBLIC_VIEW        1 (default): anyone may read / and /api/; 0: everything needs an editor
  PUBLIC_POSTERS     1: posters are public too (default 0, editors only)
  STUBS_API_KEY      write key for scripts (X-API-Key or Authorization: Bearer); empty means none
  STUBS_READ_KEY     read-only key for scripts: reads what an editor reads, writes nothing; empty means none
  SETLISTFM_API_KEY  setlist.fm API key for the show page's setlist panel; empty turns the panel's lookups off
  GEOCODER_URL       Nominatim search endpoint (default https://nominatim.openstreetmap.org/search)
  TILE_URL           map tile URL template (default https://tile.openstreetmap.org/{z}/{x}/{y}.png); its host is
                     allowed in the Content-Security-Policy's img-src
"""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

APP_NAME = "Stubs"
APP_DIR = Path(__file__).resolve().parent
UA = "stubs/1.0 (concert history; https://github.com/SDNick484/stubs)"


def _load_env(files=(Path("/etc/conf.d/stubs"), APP_DIR / ".env")):
    """KEY=value lines (optionally `export KEY=value`, values optionally quoted) from each readable file, for keys the
    environment does not have yet. The installed config is root-only, so the service user skips it: OpenRC exports
    its values to gunicorn instead."""
    for conf in files:
        try:
            with open(conf, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            k, v = (s.strip() for s in line.split("=", 1))
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v


def _flag(raw, default):
    raw = (raw or "").strip()
    return default if not raw else raw == "1"


def team_domain(raw):
    """A bare host: tolerates a pasted "https://team.cloudflareaccess.com/"."""
    return re.sub(r"^https?://", "", (raw or "").strip().strip("'\"")).split("/")[0].lower()


def parse_hosts(raw):
    """Comma-separated hosts, lowercased, ports and IPv6 brackets dropped."""
    hosts = set()
    for item in (raw or "").split(","):
        item = item.strip().lower()
        if item:
            hosts.add(urlsplit("//" + item).hostname or item)
    return hosts


_load_env()

PORT = int((os.environ.get("PORT") or "").strip() or "8773")
DATA_DIR = Path((os.environ.get("DATA_DIR") or "").strip() or APP_DIR / "data")
DB_PATH = DATA_DIR / "stubs.db"
IMAGE_DIR = DATA_DIR / "images"
CF_TEAM_DOMAIN = team_domain(os.environ.get("CF_TEAM_DOMAIN"))
CF_AUD = (os.environ.get("CF_AUD") or "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in (os.environ.get("ADMIN_EMAILS") or "").split(",") if e.strip()}
LAN_EDIT = _flag(os.environ.get("LAN_EDIT"), True)
LAN_HOSTS = parse_hosts((os.environ.get("LAN_HOSTS") or "").strip() or "localhost,127.0.0.1")
PUBLIC_VIEW = _flag(os.environ.get("PUBLIC_VIEW"), True)
PUBLIC_POSTERS = _flag(os.environ.get("PUBLIC_POSTERS"), False)
STUBS_API_KEY = (os.environ.get("STUBS_API_KEY") or "").strip()
STUBS_READ_KEY = (os.environ.get("STUBS_READ_KEY") or "").strip()
SETLISTFM_API_KEY = (os.environ.get("SETLISTFM_API_KEY") or "").strip()
GEOCODER_URL = (os.environ.get("GEOCODER_URL") or "").strip() or "https://nominatim.openstreetmap.org/search"
TILE_URL = (os.environ.get("TILE_URL") or "").strip() or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
