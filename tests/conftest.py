"""Test setup. settings.py reads the environment once at import (after /etc/conf.d/stubs and .env, which only fill in
keys the environment lacks), so every setting is put in the environment here, before the app is imported: nothing
from the host's config or a developer's .env leaks into a test run. Every test gets a fresh database.

Fixtures: client (anonymous; LAN_EDIT off, PUBLIC_VIEW on), editor (a client whose requests carry the write key),
con, clock (freezes common.today(); clock.set("2026-11-14")). Seeders that need no feature module: mk_artist,
mk_venue, mk_show. Add helpers here only when two or more test files use them, at the end of this file."""
import os
import re
import shutil
import sys
import tempfile
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = tempfile.mkdtemp(prefix="stubs-test-")
WRITE_KEY = "test-write-key"
READ_KEY = "test-read-key"


def _setting_names():
    with open(os.path.join(ROOT, "settings.py"), encoding="utf-8") as f:
        return set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', f.read()))


for _name in _setting_names():
    os.environ[_name] = ""  # empty means "the default" in settings.py, and stops _load_env filling it in
os.environ.update(DATA_DIR=DATA, LAN_EDIT="0", PUBLIC_VIEW="1", PUBLIC_POSTERS="0",
                  STUBS_API_KEY=WRITE_KEY, STUBS_READ_KEY=READ_KEY)
sys.path.insert(0, ROOT)

import app as stubs  # noqa: E402
import common  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts from an empty, migrated database and an empty DATA_DIR."""
    for name in os.listdir(DATA):
        path = os.path.join(DATA, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    db.init_db()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "LAN_EDIT", False)
    monkeypatch.setattr(settings, "PUBLIC_VIEW", True)
    return stubs.app.test_client()


@pytest.fixture
def editor(client):
    """A second test client whose every request carries X-API-Key: test-write-key."""
    c = stubs.app.test_client()
    c.environ_base.update(HTTP_X_API_KEY=WRITE_KEY)
    return c


@pytest.fixture
def con():
    c = db.connect(settings.DB_PATH)
    yield c
    c.close()


class Clock:
    """common.today() frozen at a day this test chooses. Starts at the real today."""

    def __init__(self, monkeypatch):
        self._mp = monkeypatch
        self.set(date.today().isoformat())

    def set(self, day):
        if common.parse_date(day)[1] != "day":
            raise ValueError(f"clock.set needs YYYY-MM-DD, not {day!r}")
        self._mp.setattr(common, "_frozen_today", day)


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


# ---------------------------------------------------------------- seeders (direct SQL, no feature module)
def mk_artist(con, name):
    """The artist with this name (matched by common.norm), created when missing. Returns its id."""
    n = common.norm(name)
    row = con.execute("SELECT id FROM artist WHERE norm = ?", (n,)).fetchone()
    if row:
        return row["id"]
    return con.execute("INSERT INTO artist (name, norm) VALUES (?, ?)", (name, n)).lastrowid


def mk_venue(con, name, city="Oakland", region="CA", country="US", lat=None, lon=None):
    """A new venue with one venue_name row for its name. Returns its id."""
    vid = con.execute("INSERT INTO venue (name, city, region, country, lat, lon) VALUES (?, ?, ?, ?, ?, ?)",
                      (name, city, region, country, lat, lon)).lastrowid
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)", (vid, name, common.venue_norm(name)))
    return vid


def mk_show(con, billing, date, venue_id, lineup=None, status="attended", **cols):
    """A show at venue_id. lineup is ((name, role), ...) in order; the default is the billing as headliner.
    venue_label defaults to the venue's current name. Any other show column can be passed in cols (notes, tour,
    rating, confidence, confidence_note, run_id, venue_label...). Returns the show id."""
    d, precision, sort_date = common.parse_date(date)
    if "venue_label" not in cols:
        cols["venue_label"] = con.execute("SELECT name FROM venue WHERE id = ?", (venue_id,)).fetchone()["name"]
    fields = {"status": status, "date": d, "date_precision": precision, "sort_date": sort_date, "billing": billing,
              "venue_id": venue_id, **cols}
    sid = con.execute(f"INSERT INTO show ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                      tuple(fields.values())).lastrowid
    for position, (name, role) in enumerate(lineup if lineup is not None else ((billing, "headliner"),), start=1):
        con.execute("INSERT INTO lineup (show_id, artist_id, role, position) VALUES (?, ?, ?, ?)",
                    (sid, mk_artist(con, name), role, position))
    return sid
