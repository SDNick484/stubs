"""The schema (spec section 5, now v5), migrate() with its v1 -> v2, v2 -> v3, v3 -> v4 and v4 -> v5 steps, and the
connection settings."""
import logging
import re
import sqlite3
import types

import pytest

import db
import settings
from conftest import mk_artist, mk_show, mk_venue

TABLES = {"meta", "artist", "artist_alias", "venue", "venue_name", "run", "show", "lineup", "source", "person",
          "show_person", "image", "setlist_link", "import_batch", "import_row", "song", "song_alias", "setlist",
          "setlist_entry", "watch", "watch_area", "sighting"}
V3_TABLES = ("song", "song_alias", "setlist", "setlist_entry")
V5_TABLES = ("watch", "watch_area", "sighting")


def tables(con):
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrate_creates_every_table(con):
    names = tables(con)
    assert TABLES <= names
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    assert db.SCHEMA_VERSION == "5"


def test_migrate_is_idempotent(con):
    before = tables(con)
    db.migrate(con)
    db.migrate(con)
    assert tables(con) == before
    assert con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"


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


def v4_statements():
    """Today's SCHEMA without the tables and indexes schema v5 added (concert watch): what a v4 database was made of."""
    return [s for s in db._statements(db.SCHEMA)
            if not re.search(r"\b(?:TABLE|ON) (?:IF NOT EXISTS )?(?:%s)\b" % "|".join(V5_TABLES), s)]


def v3_statements():
    """v4's SCHEMA without what schema v4 added (artist's mbid and mb_disambiguation, and the unique index on mbid):
    what a v3 database was made of."""
    out = []
    for s in v4_statements():
        if re.search(r"\bINDEX IF NOT EXISTS artist_mbid\b", s):
            continue
        if s.startswith("CREATE TABLE IF NOT EXISTS artist ("):
            s = re.sub(r"\s*mbid TEXT,\s*mb_disambiguation TEXT,", "", s)
            assert "mbid" not in s
        out.append(s)
    return out


def v2_statements():
    """Today's SCHEMA without the tables and indexes schema v3 added, and without v4's artist columns: what a v2
    database was made of."""
    return [s for s in v3_statements()
            if not re.search(r"\b(?:TABLE|ON) (?:IF NOT EXISTS )?(?:%s)\b" % "|".join(V3_TABLES), s)]


def v1_database(path):
    """A database as schema v1 left it: v2's tables (with v1's setlist_link, the one table v2 changed) and
    schema_version 1."""
    con = db.connect(path)
    stmts = v2_statements()
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
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
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


# ---------------------------------------------------------------- the v2 -> v3 step (stored setlists)
def v2_database(path):
    """A database as schema v2 left it: no song, song_alias, setlist or setlist_entry, and schema_version 2."""
    con = db.connect(path)
    for stmt in v2_statements():
        con.execute(stmt)
    con.execute("INSERT INTO meta (k, v) VALUES ('schema_version', '2')")
    return con


def columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def everything(con, skip=V3_TABLES):
    """Every row of every table but the v3 ones (and v5's, which only later steps add), by table name (artist without
    v4's columns)."""
    return {t: sorted(tuple(r) for r in con.execute(
        'SELECT id, name, norm, created_at, updated_at FROM artist' if t == "artist" else f'SELECT * FROM "{t}"'))
            for t in sorted(tables(con)) if t not in skip and t not in V5_TABLES and t != "meta"}


@pytest.fixture
def v2(tmp_path):
    con = v2_database(tmp_path / "v2.db")
    v = mk_venue(con, "Granite Hall")
    show = mk_show(con, "The Paper Lanterns", "2025-07-19", v,
                   lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))
    opener = entry_id(con, show, "Marigold Static")
    con.execute("INSERT INTO setlist_link (lineup_id, setlistfm_id, url) VALUES (?, 'abc123', "
                "'https://www.setlist.fm/setlist/x/2025/y-abc123.html')", (opener,))
    con.execute("INSERT INTO source (show_id, lineup_id, label) VALUES (?, ?, 'Ticket stub')", (show, opener))
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('the paper lantern', ?)",
                (mk_artist(con, "The Paper Lanterns"),))
    yield con, show
    con.close()


def test_v3_keeps_every_v2_row(v2):
    con, show = v2
    before = everything(con)
    db.migrate(con)
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    assert everything(con) == before
    assert links(con) == {entry_id(con, show, "Marigold Static"): "abc123"}  # the per-act link is untouched
    for table in V3_TABLES:
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_v3_tables_are_the_same_as_a_new_databases(v2, con):
    old, _ = v2
    db.migrate(old)
    for table in V3_TABLES:
        for pragma in ("table_info", "foreign_key_list", "index_list"):
            got = [tuple(r) for r in old.execute(f"PRAGMA {pragma}({table})")]
            assert got == [tuple(r) for r in con.execute(f"PRAGMA {pragma}({table})")], (table, pragma)
    names = lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master")}  # noqa: E731
    assert names(old) == names(con)
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v3_migrating_twice_changes_nothing(v2):
    con, _ = v2
    db.migrate(con)
    dump = list(con.iterdump())
    db.migrate(con)
    db.MIGRATIONS[3](con)  # the step itself is idempotent too
    assert list(con.iterdump()) == dump


def test_v1_still_reaches_v5(v1):
    con, s = v1
    db.migrate(con)
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    assert TABLES <= tables(con)
    assert {"mbid", "mb_disambiguation"} <= columns(con, "artist")
    assert len(links(con)) == 3
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v3_failing_part_way_leaves_v2_intact(v2, monkeypatch):
    con, _ = v2
    before = list(con.iterdump())
    real = db.MIGRATIONS[3]

    def step_then_fail(c):
        real(c)
        raise RuntimeError("disk full")

    monkeypatch.setitem(db.MIGRATIONS, 3, step_then_fail)
    with pytest.raises(RuntimeError):
        db.migrate(con)
    assert list(con.iterdump()) == before
    monkeypatch.setitem(db.MIGRATIONS, 3, real)
    db.migrate(con)
    assert {"song", "setlist"} <= tables(con)


def song(con, artist_id, title, norm=None):
    return con.execute("INSERT INTO song (artist_id, title, norm) VALUES (?, ?, ?)",
                       (artist_id, title, norm or title.lower())).lastrowid


def test_song_checks(con):
    a, b = mk_artist(con, "The Paper Lanterns"), mk_artist(con, "Marigold Static")
    s = song(con, a, "Lantern Light")
    song(con, b, "Lantern Light")  # another act's song with the same title is another song
    assert con.execute("SELECT in_catalog FROM song WHERE id = ?", (s,)).fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        song(con, a, "LANTERN LIGHT", "lantern light")  # UNIQUE(artist_id, norm)
    with pytest.raises(sqlite3.IntegrityError):
        song(con, 99999, "Paper Boats")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO song (artist_id, title, norm, in_catalog) VALUES (?, 'x', 'x', 2)", (a,))
    con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'lantern lite', ?)", (a, s))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO song_alias (artist_id, norm, song_id) VALUES (?, 'lantern lite', ?)", (a, s))
    with pytest.raises(sqlite3.IntegrityError):  # a song with an alias, or played, can't just be deleted
        con.execute("DELETE FROM song WHERE id = ?", (s,))
    with pytest.raises(sqlite3.IntegrityError):  # nor an artist with songs
        con.execute("DELETE FROM artist WHERE id = ?", (b,))


def test_setlist_cascades_from_the_lineup_and_the_show(con):
    v = mk_venue(con, "The Bellwether")
    s = mk_show(con, "Cold Harbor", "2016-12-01", v, lineup=(("Cold Harbor", "headliner"), ("Ninefold", "opener")))
    head, opener = [r[0] for r in con.execute("SELECT id FROM lineup WHERE show_id = ? ORDER BY position", (s,))]
    title = song(con, mk_artist(con, "Cold Harbor"), "Harbor Lights")
    for lid in (head, opener):
        con.execute("INSERT INTO setlist (lineup_id, source_label) VALUES (?, 'typed')", (lid,))
        con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id) VALUES (?, 1, ?)", (lid, title))
    with pytest.raises(sqlite3.IntegrityError):  # one setlist per act
        con.execute("INSERT INTO setlist (lineup_id, source_label) VALUES (?, 'again')", (head,))
    with pytest.raises(sqlite3.IntegrityError):  # entries belong to a setlist
        con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id) VALUES (99999, 1, ?)", (title,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO setlist_entry (lineup_id, position, song_id, encore) VALUES (?, 2, ?, 5)",
                    (head, title))
    con.execute("DELETE FROM lineup WHERE id = ?", (opener,))
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist")] == [head]
    assert [r[0] for r in con.execute("SELECT lineup_id FROM setlist_entry")] == [head]
    con.execute("DELETE FROM show WHERE id = ?", (s,))
    assert con.execute("SELECT COUNT(*) FROM setlist").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM setlist_entry").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM song").fetchone()[0] == 1  # songs are never cascaded


# ---------------------------------------------------------------- the v3 -> v4 step (MusicBrainz links)
def v3_database(path):
    """A database as schema v3 left it: today's tables without artist.mbid, artist.mb_disambiguation and artist_mbid."""
    con = db.connect(path)
    for stmt in v3_statements():
        con.execute(stmt)
    con.execute("INSERT INTO meta (k, v) VALUES ('schema_version', '3')")
    return con


@pytest.fixture
def v3(tmp_path):
    con = v3_database(tmp_path / "v3.db")
    assert not {"mbid", "mb_disambiguation"} & columns(con, "artist")
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "The Paper Lanterns", "2025-07-19", v, lineup=(("Marigold Static", "opener"), ("The Paper Lanterns", "headliner")))
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('the paper lantern', ?)", (mk_artist(con, "The Paper Lanterns"),))
    yield con
    con.close()


def artist_rows(con):
    return sorted(tuple(r) for r in con.execute("SELECT id, name, norm, created_at, updated_at FROM artist"))


def test_v4_keeps_every_v3_row_and_adds_null_columns(v3):
    before = everything(v3, skip=())
    db.migrate(v3)
    assert v3.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    assert everything(v3, skip=()) == before
    assert artist_rows(v3) == before["artist"]
    assert v3.execute("SELECT COUNT(*) FROM artist WHERE mbid IS NULL AND mb_disambiguation IS NULL").fetchone()[0] == 2


def test_v4_artist_table_is_the_same_as_a_new_databases(v3, con):
    db.migrate(v3)
    for pragma in ("table_info", "index_list"):
        assert [tuple(r) for r in v3.execute(f"PRAGMA {pragma}(artist)")] == \
            [tuple(r) for r in con.execute(f"PRAGMA {pragma}(artist)")], pragma
    names = lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master")}  # noqa: E731
    assert names(v3) == names(con)
    assert v3.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v4_mbid_is_unique_where_set(v3):
    db.migrate(v3)
    a, b = (r[0] for r in v3.execute("SELECT id FROM artist ORDER BY id"))
    u = "11111111-1111-4111-8111-111111111111"
    v3.execute("UPDATE artist SET mbid = ? WHERE id = ?", (u, a))
    with pytest.raises(sqlite3.IntegrityError):
        v3.execute("UPDATE artist SET mbid = ? WHERE id = ?", (u, b))
    v3.execute("UPDATE artist SET mbid = NULL WHERE id = ?", (b,))  # any number of artists may have none
    assert v3.execute("SELECT COUNT(*) FROM artist WHERE mbid IS NULL").fetchone()[0] == 1
    v3.execute("UPDATE artist SET mbid = NULL WHERE id = ?", (a,))
    assert v3.execute("SELECT COUNT(*) FROM artist WHERE mbid IS NULL").fetchone()[0] == 2


def test_v4_migrating_twice_changes_nothing(v3):
    db.migrate(v3)
    dump = list(v3.iterdump())
    db.migrate(v3)
    db.MIGRATIONS[4](v3)  # the step itself is idempotent too
    assert list(v3.iterdump()) == dump


def test_v2_still_reaches_v5(tmp_path):
    con = v2_database(tmp_path / "v2.db")
    mk_artist(con, "Cold Harbor")
    db.migrate(con)
    assert con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    assert {"mbid", "mb_disambiguation"} <= columns(con, "artist")
    assert con.execute("SELECT name FROM artist").fetchone()[0] == "Cold Harbor"


def test_v4_failing_part_way_leaves_v3_intact(v3, monkeypatch):
    before = list(v3.iterdump())
    real = db.MIGRATIONS[4]

    def step_then_fail(c):
        real(c)
        raise RuntimeError("disk full")

    monkeypatch.setitem(db.MIGRATIONS, 4, step_then_fail)
    with pytest.raises(RuntimeError):
        db.migrate(v3)
    assert list(v3.iterdump()) == before
    monkeypatch.setitem(db.MIGRATIONS, 4, real)
    db.migrate(v3)
    assert "mbid" in columns(v3, "artist")


# ---------------------------------------------------------------- the v4 -> v5 step (concert watch, post-v1 #47)
def v4_database(path):
    """A database as schema v4 left it: today's tables without watch, watch_area and sighting."""
    con = db.connect(path)
    for stmt in v4_statements():
        con.execute(stmt)
    con.execute("INSERT INTO meta (k, v) VALUES ('schema_version', '4')")
    return con


@pytest.fixture
def v4(tmp_path):
    con = v4_database(tmp_path / "v4.db")
    assert not set(V5_TABLES) & tables(con)
    v = mk_venue(con, "Granite Hall")
    mk_show(con, "Cold Harbor", "2009-06-20", v, lineup=(("Velvet Arcade", "opener"), ("Cold Harbor", "headliner")))
    con.execute("UPDATE artist SET mbid = '11111111-1111-4111-8111-111111111111' WHERE name = 'Cold Harbor'")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES ('cold harbour', ?)", (mk_artist(con, "Cold Harbor"),))
    yield con
    con.close()


def every_row(con):
    return {t: sorted(tuple(r) for r in con.execute(f'SELECT * FROM "{t}"')) for t in sorted(tables(con)) if t != "meta"}


def test_v5_keeps_every_v4_row_and_adds_empty_tables(v4):
    before = every_row(v4)
    db.migrate(v4)
    assert v4.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
    after = every_row(v4)
    assert {t: rows for t, rows in after.items() if t not in V5_TABLES} == before
    assert all(after[t] == [] for t in V5_TABLES)


def test_v5_tables_are_the_same_as_a_new_databases(v4, con):
    db.migrate(v4)
    for table in V5_TABLES:
        for pragma in ("table_info", "foreign_key_list", "index_list"):
            got = [tuple(r) for r in v4.execute(f"PRAGMA {pragma}({table})")]
            assert got == [tuple(r) for r in con.execute(f"PRAGMA {pragma}({table})")], (table, pragma)
    names = lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master")}  # noqa: E731
    assert names(v4) == names(con)
    assert v4.execute("PRAGMA foreign_key_check").fetchall() == []
    assert v4.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v5_migrating_twice_changes_nothing(v4):
    db.migrate(v4)
    dump = list(v4.iterdump())
    db.migrate(v4)
    db.MIGRATIONS[5](v4)  # the step itself is idempotent too
    assert list(v4.iterdump()) == dump


def test_v1_and_v3_still_reach_v5(v1, tmp_path):
    con, _ = v1
    db.migrate(con)
    old = v3_database(tmp_path / "v3.db")
    mk_artist(old, "Marigold Static")
    db.migrate(old)
    for c in (con, old):
        assert c.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0] == "5"
        assert TABLES <= tables(c)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("SELECT name FROM artist").fetchone()[0] == "Marigold Static"
    old.close()


def test_v5_failing_part_way_leaves_v4_intact(v4, monkeypatch):
    before = list(v4.iterdump())
    real = db.MIGRATIONS[5]

    def step_then_fail(c):
        real(c)
        raise RuntimeError("disk full")

    monkeypatch.setitem(db.MIGRATIONS, 5, step_then_fail)
    with pytest.raises(RuntimeError):
        db.migrate(v4)
    assert list(v4.iterdump()) == before
    monkeypatch.setitem(db.MIGRATIONS, 5, real)
    db.migrate(v4)
    assert set(V5_TABLES) <= tables(v4)


def test_watch_tables_checks_and_cascades(con):
    a = mk_artist(con, "Marigold Static")
    con.execute("INSERT INTO watch (artist_id, origin) VALUES (?, 'manual')", (a,))
    for bad in ("INSERT INTO watch (artist_id, origin) VALUES (99, 'manual')",  # no such artist
                "UPDATE watch SET origin = 'friend'", "UPDATE watch SET anywhere = 2", "UPDATE watch SET muted = -1",
                "INSERT INTO watch_area (name, lat, lon, radius_km) VALUES ('Home', 91, 0, 50)",
                "INSERT INTO watch_area (name, lat, lon, radius_km) VALUES ('Home', 0, 181, 50)",
                "INSERT INTO watch_area (name, lat, lon, radius_km) VALUES ('Home', 0, 0, 0)",
                "INSERT INTO watch_area (name, lat, lon, radius_km) VALUES ('', 0, 0, 10)"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(bad)
    ins = ("INSERT INTO sighting (artist_id, source, source_event_id, date, venue, city, status) "
           "VALUES (?, ?, ?, '2027-03-14', 'Old Mill Theatre', 'Portland', ?)")
    con.execute(ins, (a, "ticketmaster", "Z1", "new"))
    for args in ((a, "ticketmaster", "Z1", "new"), (a, "myspace", "Z2", "new"), (a, "email", "Z3", "maybe")):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(ins, args)
    con.execute(ins, (a, "email", "Z1", "new"))  # unique per source, not across sources
    con.execute("DELETE FROM watch")
    con.execute("DELETE FROM artist WHERE id = ?", (a,))
    assert con.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 0
