"""The schema (spec section 5, now v2), migrate() and its v1 -> v2 step, and the connection settings."""
import logging
import sqlite3
import types

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
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "2"
    assert db.SCHEMA_VERSION == "2"


def test_migrate_is_idempotent(con):
    before = tables(con)
    db.migrate(con)
    db.migrate(con)
    assert tables(con) == before
    assert con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "2"


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
    con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (?, 'abc', 'https://www.setlist.fm/x')",
                (lid,))
    b = con.execute("INSERT INTO import_batch (origin, label) VALUES ('csv', 't')").lastrowid
    con.execute("INSERT INTO import_row (batch_id, row_no, raw, parsed, verdict, match_show_id) "
                "VALUES (?, 1, '{}', '{}', 'have', ?)", (b, s))

    con.execute("DELETE FROM show WHERE id = ?", (s,))
    for table in ("lineup", "source", "show_person", "image"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE show_id = ?", (s,)).fetchone()[0] == 0, table
    assert con.execute("SELECT COUNT(*) FROM setlist_link").fetchone()[0] == 0
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


def test_deleting_a_lineup_entry_drops_its_setlist_link(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v, lineup=(("Cold Harbor", "headliner"), ("Ninefold", "opener")))
    head, opener = [r[0] for r in con.execute("SELECT id FROM lineup WHERE show_id = ? ORDER BY position", (s,))]
    for lid, sid in ((head, "abc"), (opener, "def")):
        con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (?, ?, 'https://www.setlist.fm/x')",
                    (lid, sid))
    con.execute("DELETE FROM lineup WHERE id = ?", (opener,))
    assert [tuple(r) for r in con.execute("SELECT lineup_id, setlistfm_id FROM setlist_link")] == [(head, "abc")]
    with pytest.raises(sqlite3.IntegrityError):  # one link per entry, and only for an entry that exists
        con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (?, 'ghi', 'u')", (head,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (99999, 'ghi', 'u')")


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


# ---------------------------------------------------------------- the v1 -> v2 step (setlist_link per lineup entry)
V1_SETLIST_LINK = """CREATE TABLE IF NOT EXISTS setlist_link (
  show_id INTEGER PRIMARY KEY REFERENCES show(id) ON DELETE CASCADE,
  setlistfm_id TEXT NOT NULL,
  url TEXT NOT NULL)"""


def v1_database(path):
    """A database as schema v1 left it: v1's SCHEMA (today's, with v1's setlist_link, the one table v2 changed) and
    schema_version 1."""
    con = db.connect(path)
    stmts = db._statements(db.SCHEMA)
    at = [i for i, s in enumerate(stmts) if s.startswith("CREATE TABLE IF NOT EXISTS setlist_link")]
    assert len(at) == 1
    stmts[at[0]] = V1_SETLIST_LINK
    for stmt in stmts:
        con.execute(stmt)
    con.execute("INSERT INTO meta (k, v) VALUES ('schema_version', '1')")
    return con


def v1_link(con, show_id, setlist_id):
    con.execute("INSERT INTO setlist_link (show_id, setlistfm_id, url) VALUES (?, ?, ?)",
                (show_id, setlist_id, f"https://www.setlist.fm/setlist/x/2025/y-{setlist_id}.html"))


def entry_id(con, show_id, name):
    return con.execute("SELECT l.id FROM lineup l JOIN artist a ON a.id = l.artist_id WHERE l.show_id = ? AND a.name = ?",
                       (show_id, name)).fetchone()[0]


def links(con):
    return {r["lineup_id"]: r["setlistfm_id"] for r in con.execute("SELECT * FROM setlist_link")}


@pytest.fixture
def v1(tmp_path):
    con = v1_database(tmp_path / "v1.db")
    v = mk_venue(con, "Granite Hall")
    shows = types.SimpleNamespace(
        headliner=mk_show(con, "The Paper Lanterns", "2025-07-19", v,
                          lineup=(("Marigold Static", "opener"), ("Ninefold", "headliner"),
                                  ("The Paper Lanterns", "headliner"))),
        coheadliners=mk_show(con, "Cold Harbor & Velvet Arcade", "2009-06-20", v,
                             lineup=(("Ruth's Garden", "opener"), ("Velvet Arcade", "coheadliner"),
                                     ("Cold Harbor", "coheadliner"))),
        openers=mk_show(con, "Ruth's Garden", "2010-01-02", v,
                        lineup=(("Ruth's Garden", "opener"), ("Ninefold", "performer"))),
        empty=mk_show(con, "Marigold Static", "2011-03-04", v, lineup=()),
        unlinked=mk_show(con, "Ninefold", "2012-05-06", v))
    # The headliner with the lowest position, not the first row: Ninefold is position 2, The Paper Lanterns 3.
    con.execute("UPDATE lineup SET position = 9 WHERE id = ?", (entry_id(con, shows.headliner, "Ninefold"),))
    v1_link(con, shows.headliner, "aaa111")
    v1_link(con, shows.coheadliners, "bbb222")
    v1_link(con, shows.openers, "ccc333")
    v1_link(con, shows.empty, "ddd444")
    yield con, shows
    con.close()


def test_v2_moves_each_link_onto_the_headlining_entry(v1, caplog):
    con, s = v1
    with caplog.at_level(logging.WARNING, logger="db"):
        db.migrate(con)
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "2"
    assert links(con) == {
        entry_id(con, s.headliner, "The Paper Lanterns"): "aaa111",  # headliner, lowest position
        entry_id(con, s.coheadliners, "Velvet Arcade"): "bbb222",   # no headliner: the first coheadliner
        entry_id(con, s.openers, "Ruth's Garden"): "ccc333",        # neither: the lowest position of any role
    }
    url = con.execute("SELECT url FROM setlist_link WHERE setlistfm_id = 'aaa111'").fetchone()[0]
    assert url == "https://www.setlist.fm/setlist/x/2025/y-aaa111.html"
    # The show with no lineup loses its link, and says so.
    assert any(f"show {s.empty}" in r.getMessage() and "ddd444" in r.getMessage() for r in caplog.records)
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 5


def test_v2_table_is_the_same_as_a_new_databases(v1, con):
    old, _ = v1
    db.migrate(old)
    for pragma in ("table_info", "foreign_key_list", "index_list"):
        got = [tuple(r) for r in old.execute(f"PRAGMA {pragma}(setlist_link)")]
        assert got == [tuple(r) for r in con.execute(f"PRAGMA {pragma}(setlist_link)")], pragma
    names = lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master")}  # noqa: E731
    assert names(old) == names(con)  # no leftover table from the move
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v2_cascades_from_the_lineup_after_the_move(v1):
    con, s = v1
    db.migrate(con)
    con.execute("DELETE FROM lineup WHERE id = ?", (entry_id(con, s.headliner, "The Paper Lanterns"),))
    assert "aaa111" not in links(con).values()
    con.execute("DELETE FROM show WHERE id = ?", (s.coheadliners,))
    assert sorted(links(con).values()) == ["ccc333"]


def test_v2_migrating_twice_changes_nothing(v1):
    con, _ = v1
    db.migrate(con)
    dump = list(con.iterdump())
    db.migrate(con)
    db.MIGRATIONS[2](con)  # the step itself is idempotent too: a v2 table is left alone
    assert list(con.iterdump()) == dump


def test_v2_new_database_needs_no_move(con):
    assert {r["name"] for r in con.execute("PRAGMA table_info(setlist_link)")} == {"lineup_id", "setlistfm_id", "url"}


def test_v2_failing_part_way_leaves_v1_intact(v1, monkeypatch):
    con, s = v1
    before = list(con.iterdump())
    real = db.MIGRATIONS[2]

    def step_then_fail(c):
        real(c)  # the whole move, table swap included, then a failure before the version is written
        raise RuntimeError("disk full")

    monkeypatch.setitem(db.MIGRATIONS, 2, step_then_fail)
    with pytest.raises(RuntimeError):
        db.migrate(con)
    assert list(con.iterdump()) == before
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "1"
    assert con.execute("SELECT setlistfm_id FROM setlist_link WHERE show_id = ?", (s.empty,)).fetchone()[0] == "ddd444"
    monkeypatch.setitem(db.MIGRATIONS, 2, real)
    db.migrate(con)  # and the next start finishes the job
    assert len(links(con)) == 3
