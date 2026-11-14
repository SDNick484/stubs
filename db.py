"""The SQLite schema (spec section 5, schema v1), connections, transactions and migrate(), plus the per-request
connection db() and init_db().

Connections run in autocommit mode (isolation_level=None): a single statement commits on its own, and anything that
must be atomic runs inside `with transaction(con):`, which nests (an inner block becomes a savepoint). Every schema
change adds a step to MIGRATIONS and bumps SCHEMA_VERSION; migrate() is idempotent and runs at every start, so the
host never needs a manual DB step. Dates are ISO strings; created_at/updated_at are SQLite CURRENT_TIMESTAMP text."""
import itertools
import os
import sqlite3
from contextlib import contextmanager

from flask import g

import settings

SCHEMA_VERSION = "1"

# Version 1. Statements are separated by ";" and contain none themselves (no triggers): see _statements().
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artist (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  norm TEXT NOT NULL CHECK (norm <> ''),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (norm));
CREATE TABLE IF NOT EXISTS artist_alias (
  norm TEXT PRIMARY KEY CHECK (norm <> ''),
  artist_id INTEGER NOT NULL REFERENCES artist(id));
CREATE INDEX IF NOT EXISTS artist_alias_artist ON artist_alias(artist_id);
CREATE TABLE IF NOT EXISTS venue (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  city TEXT NOT NULL,
  region TEXT,
  country TEXT,
  lat REAL CHECK (lat IS NULL OR lat BETWEEN -90 AND 90),
  lon REAL CHECK (lon IS NULL OR lon BETWEEN -180 AND 180),
  geocode_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS venue_name (
  venue_id INTEGER NOT NULL REFERENCES venue(id),
  name TEXT NOT NULL CHECK (name <> ''),
  norm TEXT NOT NULL,
  UNIQUE (norm, venue_id));
CREATE INDEX IF NOT EXISTS venue_name_venue ON venue_name(venue_id);
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS show (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('attended','upcoming')),
  date TEXT NOT NULL,
  date_precision TEXT NOT NULL CHECK (date_precision IN ('year','month','day')),
  sort_date TEXT NOT NULL,
  billing TEXT NOT NULL CHECK (billing <> ''),
  venue_id INTEGER NOT NULL REFERENCES venue(id),
  venue_label TEXT NOT NULL,
  run_id INTEGER REFERENCES run(id),
  tour TEXT,
  notes TEXT,
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  confidence TEXT NOT NULL DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','likely')),
  confidence_note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS show_sort ON show(sort_date, id);
CREATE INDEX IF NOT EXISTS show_venue ON show(venue_id);
CREATE INDEX IF NOT EXISTS show_run ON show(run_id);
CREATE TABLE IF NOT EXISTS lineup (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES show(id) ON DELETE CASCADE,
  artist_id INTEGER NOT NULL REFERENCES artist(id),
  role TEXT NOT NULL CHECK (role IN ('headliner','coheadliner','opener','performer')),
  position INTEGER NOT NULL DEFAULT 0,
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  confidence TEXT NOT NULL DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','likely')),
  confidence_note TEXT,
  UNIQUE (show_id, artist_id));
CREATE INDEX IF NOT EXISTS lineup_artist ON lineup(artist_id);
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES show(id) ON DELETE CASCADE,
  lineup_id INTEGER REFERENCES lineup(id) ON DELETE CASCADE,
  label TEXT NOT NULL CHECK (label <> ''),
  url TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS source_show ON source(show_id);
CREATE INDEX IF NOT EXISTS source_lineup ON source(lineup_id);
CREATE TABLE IF NOT EXISTS person (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS show_person (
  show_id INTEGER NOT NULL REFERENCES show(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES person(id),
  PRIMARY KEY (show_id, person_id));
CREATE INDEX IF NOT EXISTS show_person_person ON show_person(person_id);
CREATE TABLE IF NOT EXISTS image (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES show(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('poster','photo','stub')),
  file TEXT NOT NULL,
  caption TEXT,
  source_url TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS image_show ON image(show_id);
CREATE TABLE IF NOT EXISTS setlist_link (
  show_id INTEGER PRIMARY KEY REFERENCES show(id) ON DELETE CASCADE,
  setlistfm_id TEXT NOT NULL,
  url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_batch (
  id INTEGER PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('csv','api')),
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','applied','discarded')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_at TEXT);
CREATE TABLE IF NOT EXISTS import_row (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  row_no INTEGER NOT NULL,
  raw TEXT NOT NULL,
  parsed TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('new','have','maybe','skip')),
  match_show_id INTEGER REFERENCES show(id) ON DELETE SET NULL,
  reason TEXT,
  include INTEGER NOT NULL DEFAULT 0 CHECK (include IN (0, 1)));
CREATE INDEX IF NOT EXISTS import_row_batch ON import_row(batch_id, row_no);
CREATE INDEX IF NOT EXISTS import_row_match ON import_row(match_show_id)
"""


def _statements(script):
    # Not executescript(): it commits any open transaction first, which would split a migration in two.
    return [s.strip() for s in script.split(";") if s.strip()]


def connect(path) -> sqlite3.Connection:
    """A connection in autocommit mode with Row rows, foreign keys on and WAL."""
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA journal_mode = WAL")
    return con


_savepoints = itertools.count(1)


@contextmanager
def transaction(con, mode="IMMEDIATE"):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if the block raises. Inside another transaction it is a SAVEPOINT, so
    a writer that wraps itself can still be called from a caller's larger transaction."""
    if con.in_transaction:
        name = f"sp{next(_savepoints)}"
        con.execute(f"SAVEPOINT {name}")
        try:
            yield con
        except BaseException:
            con.execute(f"ROLLBACK TO {name}")
            con.execute(f"RELEASE {name}")
            raise
        con.execute(f"RELEASE {name}")
        return
    con.execute(f"BEGIN {mode}")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _version(con) -> int:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return 0
    row = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _to_v1(con):
    for stmt in _statements(SCHEMA):
        con.execute(stmt)


MIGRATIONS = {1: _to_v1}  # version -> the step that brings version-1 up to it


def migrate(con) -> None:
    """Bring the database up to SCHEMA_VERSION, one atomic step at a time. Safe to run at every start."""
    for target in range(_version(con) + 1, int(SCHEMA_VERSION) + 1):
        with transaction(con):
            MIGRATIONS[target](con)
            con.execute("INSERT INTO meta (k, v) VALUES ('schema_version', ?) "
                        "ON CONFLICT(k) DO UPDATE SET v = excluded.v", (str(target),))


def db() -> sqlite3.Connection:
    """This request's connection, opened on first use and closed at teardown (app.py registers close_db)."""
    if "db" not in g:
        g.db = connect(settings.DB_PATH)
    return g.db


def close_db(_exc=None) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_db() -> None:
    """Create DATA_DIR and IMAGE_DIR and bring the database up to the current schema. app.py runs it at import, so
    at every start."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(settings.IMAGE_DIR, exist_ok=True)
    con = connect(settings.DB_PATH)
    try:
        migrate(con)
    finally:
        con.close()
