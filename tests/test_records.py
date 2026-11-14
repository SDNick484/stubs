import os

import pytest

import settings
from common import ApiError
from conftest import mk_show, mk_venue
from records import (add_lineup_entry, add_source, create_show, delete_show, find_venue, get_or_create_artist,
                     get_or_create_run, get_or_create_venue, resolve_artist)


def test_artist_resolves_through_curly_apostrophe(con):
    a = get_or_create_artist(con, "Ruth's Garden")
    assert get_or_create_artist(con, "Ruth’s Garden") == a
    assert con.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 1
    assert con.execute("SELECT name FROM artist WHERE id=?", (a,)).fetchone()[0] == "Ruth's Garden"


def test_artist_resolves_through_alias(con):
    a = get_or_create_artist(con, "The Paper Lanterns")
    con.execute("INSERT INTO artist_alias (norm, artist_id) VALUES (?, ?)", ("paper lanterns", a))
    assert resolve_artist(con, "Paper  Lanterns") == a
    assert get_or_create_artist(con, "PAPER LANTERNS") == a
    assert resolve_artist(con, "Marigold Static") is None
    assert resolve_artist(con, "  ") is None


def test_get_or_create_artist_trims_and_rejects_blank(con):
    a = get_or_create_artist(con, "  Cold Harbor ")
    assert con.execute("SELECT name FROM artist WHERE id=?", (a,)).fetchone()[0] == "Cold Harbor"
    with pytest.raises(ApiError) as e:
        get_or_create_artist(con, "   ")
    assert e.value.status == 400


def test_venue_matches_without_the_in_same_city_only(con):
    v = get_or_create_venue(con, "The Bellwether", "Oakland", "CA", "US")
    assert find_venue(con, "Bellwether", "Oakland") == v
    assert find_venue(con, "Bellwether", "oakland ") == v
    assert find_venue(con, "Bellwether", "Portland") is None
    assert get_or_create_venue(con, "Bellwether", "Oakland", "CA", "US") == v
    other = get_or_create_venue(con, "Bellwether", "Portland", "OR", "US")
    assert other != v


def test_new_venue_has_a_venue_name_row(con):
    v = get_or_create_venue(con, "The Bellwether", "Oakland", "CA", "US")
    rows = con.execute("SELECT name, norm FROM venue_name WHERE venue_id=?", (v,)).fetchall()
    assert [tuple(r) for r in rows] == [("The Bellwether", "bellwether")]


def test_venue_matches_a_former_name(con):
    v = mk_venue(con, "Old Mill Theatre", city="Oakland")
    con.execute("UPDATE venue SET name='Harbor Hall' WHERE id=?", (v,))
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, 'Harbor Hall', 'harbor hall')", (v,))
    assert find_venue(con, "Old Mill Theatre", "Oakland") == v
    assert get_or_create_venue(con, "The Old Mill Theatre", "Oakland", "CA", "US") == v


def test_run_is_reused_by_name(con):
    r = get_or_create_run(con, "Spring Residency")
    assert get_or_create_run(con, " spring residency ") == r
    assert get_or_create_run(con, "Other") != r


def test_create_show_sets_precision_and_sort_date(con):
    v = mk_venue(con, "Old Mill Theatre")
    s = create_show(con, status="upcoming", date="2027", billing="Velvet Arcade", venue_id=v,
                    venue_label="Old Mill Theatre")
    row = con.execute("SELECT date_precision, sort_date, confidence, version FROM show WHERE id=?", (s,)).fetchone()
    assert tuple(row) == ("year", "2027-01-01", "confirmed", 1)
    s2 = create_show(con, status="attended", date="2025-07-19", billing="Cold Harbor", venue_id=v,
                     venue_label="Old Mill Theatre", tour="Long Way", notes="n", confidence="likely",
                     confidence_note="from memory")
    row = con.execute("SELECT date_precision, sort_date, tour, notes, confidence, confidence_note FROM show "
                      "WHERE id=?", (s2,)).fetchone()
    assert tuple(row) == ("day", "2025-07-19", "Long Way", "n", "likely", "from memory")


@pytest.mark.parametrize("kw", [{"date": "2025-13-01"}, {"date": "soon"}, {"status": "missed"}, {"billing": " "},
                                {"confidence": "sure"}])
def test_create_show_rejects_bad_input(con, kw):
    v = mk_venue(con, "Old Mill Theatre")
    args = dict(status="attended", date="2025-07-19", billing="Cold Harbor", venue_id=v, venue_label="x")
    args.update(kw)
    with pytest.raises(ApiError) as e:
        create_show(con, **args)
    assert e.value.status == 400
    assert con.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 0


def _show(con):
    v = mk_venue(con, "Old Mill Theatre")
    return mk_show(con, "Cold Harbor", "2025-07-19", v, lineup=())


def test_lineup_position_defaults_to_last_plus_one(con):
    s = _show(con)
    a, b = get_or_create_artist(con, "Cold Harbor"), get_or_create_artist(con, "Marigold Static")
    add_lineup_entry(con, s, a, "headliner")
    lid = add_lineup_entry(con, s, b, "opener", confidence="likely", confidence_note="maybe")
    row = con.execute("SELECT position, confidence, confidence_note FROM lineup WHERE id=?", (lid,)).fetchone()
    assert tuple(row) == (2, "likely", "maybe")
    c = get_or_create_artist(con, "Velvet Arcade")
    lid = add_lineup_entry(con, s, c, "performer", position=9)
    assert con.execute("SELECT position FROM lineup WHERE id=?", (lid,)).fetchone()[0] == 9


def test_duplicate_lineup_entry_is_409(con):
    s = _show(con)
    a = get_or_create_artist(con, "Cold Harbor")
    add_lineup_entry(con, s, a, "headliner")
    with pytest.raises(ApiError) as e:
        add_lineup_entry(con, s, a, "opener")
    assert (e.value.status, e.value.message) == (409, "Already on this show.")
    assert con.execute("SELECT COUNT(*) FROM lineup").fetchone()[0] == 1


def test_bad_lineup_role_is_400(con):
    s = _show(con)
    with pytest.raises(ApiError) as e:
        add_lineup_entry(con, s, get_or_create_artist(con, "Cold Harbor"), "dancer")
    assert e.value.status == 400


def test_add_source(con):
    s = _show(con)
    lid = add_lineup_entry(con, s, get_or_create_artist(con, "Cold Harbor"), "headliner")
    a = add_source(con, s, " Ticketmaster ", "https://concerts.example.com/t/1")
    b = add_source(con, s, "stub scan", lineup_id=lid)
    rows = {r["id"]: tuple(r) for r in con.execute("SELECT id, label, url, lineup_id FROM source")}
    assert rows[a] == (a, "Ticketmaster", "https://concerts.example.com/t/1", None)
    assert rows[b] == (b, "stub scan", None, lid)


def test_blank_source_label_is_400(con):
    s = _show(con)
    for label in ("", "   ", None):
        with pytest.raises(ApiError) as e:
            add_source(con, s, label)
        assert e.value.status == 400


def test_delete_show_removes_image_files(con, tmp_path):
    s = _show(con)
    keep = _show(con)
    os.makedirs(settings.IMAGE_DIR, exist_ok=True)
    f1, f2 = settings.IMAGE_DIR / "1-poster-aaa.jpg", settings.IMAGE_DIR / "2-photo-bbb.jpg"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'poster', ?)", (s, f1.name))
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'photo', ?)", (keep, f2.name))
    add_source(con, s, "x")
    delete_show(con, s)
    assert not f1.exists() and f2.exists()
    assert con.execute("SELECT COUNT(*) FROM show WHERE id=?", (s,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM image WHERE show_id=?", (s,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0


def test_delete_show_tolerates_missing_file_and_never_leaves_image_dir(con, tmp_path):
    s = _show(con)
    outside = tmp_path / "keep.txt"
    outside.write_text("z")
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'poster', 'gone.jpg')", (s,))
    con.execute("INSERT INTO image (show_id, kind, file) VALUES (?, 'photo', ?)", (s, "../../" + str(outside)))
    delete_show(con, s)
    assert outside.exists()


def test_delete_missing_show_is_404(con):
    with pytest.raises(ApiError) as e:
        delete_show(con, 999)
    assert e.value.status == 404
