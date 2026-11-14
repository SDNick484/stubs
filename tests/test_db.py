"""Schema v1 (spec section 5), migrate() and the connection settings."""
import sqlite3

import pytest

import db
import settings
from conftest import mk_artist, mk_show, mk_venue

TABLES = {"meta", "artist", "artist_alias", "venue", "venue_name", "run", "show", "lineup", "source", "person",
          "show_person", "image", "setlist_link", "import_batch", "import_row"}


def tables(con):
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrate_creates_every_table(con):
    names = tables(con)
    assert TABLES <= names
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "1"
    assert db.SCHEMA_VERSION == "1"


def test_migrate_is_idempotent(con):
    before = tables(con)
    db.migrate(con)
    db.migrate(con)
    assert tables(con) == before
    assert con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "1"


def test_migrate_keeps_data(con):
    vid = mk_venue(con, "The Bellwether")
    db.migrate(con)
    assert con.execute("SELECT name FROM venue WHERE id = ?", (vid,)).fetchone()[0] == "The Bellwether"


def test_connection_settings(con):
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(con.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


def test_show_checks(con):
    v = mk_venue(con, "The Bellwether")
    with pytest.raises(sqlite3.IntegrityError):
        mk_show(con, "Cold Harbor", "2016-12-01", v, status="missed")
    with pytest.raises(sqlite3.IntegrityError):
        mk_show(con, "Cold Harbor", "2016-12-01", v, date_precision="week")
    with pytest.raises(sqlite3.IntegrityError):
        mk_show(con, "Cold Harbor", "2016-12-01", v, rating=6)
    with pytest.raises(sqlite3.IntegrityError):
        mk_show(con, "Cold Harbor", "2016-12-01", v, confidence="maybe")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v, rating=5, confidence="likely")
    assert con.execute("SELECT version, confidence FROM show WHERE id = ?", (s,)).fetchone()[:] == (1, "likely")


def test_lineup_checks(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v)
    a = mk_artist(con, "Ninefold")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO lineup (show_id, artist_id, role) VALUES (?, ?, 'support')", (s, a))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO lineup (show_id, artist_id, role, rating) VALUES (?, ?, 'opener', 0)", (s, a))


def test_lineup_unique_per_show(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v)
    a = mk_artist(con, "Cold Harbor")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO lineup (show_id, artist_id, role) VALUES (?, ?, 'opener')", (s, a))


def test_artist_norm_unique(con):
    mk_artist(con, "Ruth's Garden")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO artist (name, norm) VALUES ('Ruth’s Garden', 'ruth''s garden')")


def test_deleting_a_show_cascades(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v, lineup=(("Cold Harbor", "headliner"), ("Ninefold", "opener")))
    other = mk_show(con, "Cold Harbor", "2016-12-02", v)
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ? AND role = 'opener'", (s,)).fetchone()[0]
    con.execute("INSERT INTO source (show_id, label) VALUES (?, 'Ticketmaster')", (s,))
    con.execute("INSERT INTO source (show_id, lineup_id, label) VALUES (?, ?, 'setlist.fm')", (s, lid))
    p = con.execute("INSERT INTO person (name) VALUES ('Alex')").lastrowid
    con.execute("INSERT INTO show_person (show_id, person_id) VALUES (?, ?)", (s, p))
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'poster', 'p.jpg')", (s,))
    con.execute("INSERT INTO setlist_link (show_id, setlistfm_id, url) VALUES (?, 'abc', 'https://www.setlist.fm/x')", (s,))
    b = con.execute("INSERT INTO import_batch (origin, label) VALUES ('csv', 't')").lastrowid
    con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict, match_show_id) "
                "VALUES (?, 1, '{}', '{}', 'have', ?)", (b, s))

    con.execute("DELETE FROM show WHERE id = ?", (s,))
    for table in ("lineup", "source", "show_person", "image", "setlist_link"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE show_id = ?", (s,)).fetchone()[0] == 0, table
    assert con.execute("SELECT COUNT(*) FROM lineup WHERE show_id = ?", (other,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 1  # the companion stays, untagged
    assert con.execute("SELECT match_show_id FROM import_row").fetchone()[0] is None


def test_deleting_a_lineup_entry_drops_its_sources(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v)
    lid = con.execute("SELECT id FROM lineup WHERE show_id = ?", (s,)).fetchone()[0]
    con.execute("INSERT INTO source (show_id, label) VALUES (?, 'Ticketmaster')", (s,))
    con.execute("INSERT INTO source (show_id, lineup_id, label) VALUES (?, ?, 'setlist.fm')", (s, lid))
    con.execute("DELETE FROM lineup WHERE id = ?", (lid,))
    assert [r[0] for r in con.execute("SELECT label FROM source")] == ["Ticketmaster"]


def test_deleting_a_batch_drops_its_rows(con):
    b = con.execute("INSERT INTO import_batch (origin, label) VALUES ('api', 't')").lastrowid
    con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict) VALUES (?, 1, '{}', '{}', 'new')", (b,))
    con.execute("DELETE FROM import_batch WHERE id = ?", (b,))
    assert con.execute("SELECT COUNT(*) FROM import_row").fetchone()[0] == 0


def test_batch_and_row_checks(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO import_batch (origin, label) VALUES ('email', 't')")
    b = con.execute("INSERT INTO import_batch (origin, label) VALUES ('csv', 't')").lastrowid
    assert con.execute("SELECT status FROM import_batch WHERE id = ?", (b,)).fetchone()[0] == "pending"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict) VALUES (?, 1, '{}', '{}', 'dup')", (b,))
    s = mk_show(con, "Cold Harbor", "2016-12-01", mk_venue(con, "The Bellwether"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'flyer', 'x.jpg')", (s,))
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'stub', 'x.jpg')", (s,))


def test_a_venue_in_use_cannot_be_deleted(con):
    v = mk_venue(con, "The Bellwether")
    mk_show(con, "Cold Harbor", "2016-12-01", v)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM venue WHERE id = ?", (v,))


def test_transaction_rolls_back_and_nests(con):
    with pytest.raises(RuntimeError):
        with db.transaction(con):
            mk_venue(con, "Granite Hall", "Vancouver", "BC", "CA")
            raise RuntimeError("boom")
    assert con.execute("SELECT COUNT(*) FROM venue").fetchone()[0] == 0
    with db.transaction(con):
        mk_venue(con, "Granite Hall", "Vancouver", "BC", "CA")
        with pytest.raises(RuntimeError):
            with db.transaction(con):  # a savepoint: only the inner block is undone
                mk_venue(con, "Old Mill Theatre", "Portland", "OR", "US")
                raise RuntimeError("inner")
    assert [r[0] for r in con.execute("SELECT name FROM venue")] == ["Granite Hall"]


def test_init_db_creates_the_image_dir():
    assert settings.IMAGE_DIR.is_dir()
    assert settings.DB_PATH.is_file() and settings.DB_PATH.name == "stubs.db"
