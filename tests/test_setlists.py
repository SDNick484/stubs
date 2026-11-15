"""Post-v1 #43: stored setlists. The paste parser (setlists.parse_setlist). Fixture pastes live in
tests/fixtures/setlists/; every act and song is invented."""
import os

import pytest

from common import ApiError
from setlists import parse_setlist

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "setlists")


def paste(name):
    with open(os.path.join(FIX, name), encoding="utf-8", newline="") as f:
        return f.read()


def flat(entries):
    return [(e["title"], e["set_name"], e["encore"], e["note"], e["segue"]) for e in entries]


# ---------------------------------------------------------------- the parser
def test_parser_strips_numbering_and_bullets():
    assert [e["title"] for e in parse_setlist(paste("numbered.txt"))] == [
        "Lantern Light", "Paper Boats", "Low Tide Hymn", "Ferris Wheel Static", "Night Ferry", "Candlewick",
        "Saltwater Choir", "Moth Orchard",
        "99 Lanterns",  # a number that is not the next song's number is part of the title
    ]


def test_parser_headings_start_sets_and_encores():
    assert [(e["title"], e["set_name"], e["encore"]) for e in parse_setlist(paste("encores.txt"))] == [
        ("Lantern Light", None, False),
        ("Paper Boats", None, False),
        ("Low Tide Hymn", "Set II", False),
        ("Ferris Wheel Static", "Acoustic Set", False),
        ("Night Ferry", "Under the Pier", False),
        ("Candlewick", None, True),
        ("Saltwater Choir", "Encore 2", True),
        ("Moth Orchard", "Set 3", False),
        ("Paper Crown", "Encore 3", True),  # a third encore, named so it stays its own set
        ("Last Tram Home", "Encore 4", True),
    ]


def test_parser_segues():
    assert [(e["title"], e["segue"]) for e in parse_setlist(paste("segues.txt"))] == [
        ("Lantern Light", True), ("Paper Boats", False),
        ("Low Tide Hymn", True), ("Ferris Wheel Static", True), ("Night Ferry", False),
        ("Candlewick", True), ("Saltwater Choir", False),
        ("Moth Orchard", True), ("Paper Crown", False),
    ]


def test_parser_notes_and_quotes():
    assert [(e["title"], e["note"]) for e in parse_setlist(paste("notes.txt"))] == [
        ("Paper Boats", "with a guest"),
        ("Low Tide Hymn", "tease"),
        ("Night Ferry", None),
        ("Candlewick", "first time"),
        ("Ruth’s Song", "cover; extended"),
        ("(Untitled)", None),  # nothing left without the brackets: they are the title
        ("Moth (Orchard) Waltz", None),  # brackets inside a title stay
        ("Saltwater Choir", None),
    ]


def test_parser_crlf():
    assert flat(parse_setlist(paste("crlf.txt"))) == [
        ("Lantern Light", "Set 1", False, None, False),
        ("Paper Boats", "Set 1", False, None, True),
        ("Night Ferry", None, True, None, False),
    ]


def test_parser_note_and_segue_on_one_line():
    assert flat(parse_setlist("3. Paper Boats (tease) > Night Ferry [with a guest] >\nCandlewick")) == [
        ("Paper Boats", None, False, "tease", True),
        ("Night Ferry", None, False, "with a guest", True),
        ("Candlewick", None, False, None, False),
    ]


def test_parser_ignores_blank_lines_and_nothing_is_empty():
    assert parse_setlist("\n\n   \n") == []
    assert parse_setlist("Encore:\n") == []
    assert [e["title"] for e in parse_setlist("  Lantern  Light  \n\n\nPaper Boats")] == ["Lantern Light", "Paper Boats"]


def test_parser_refuses_a_long_line_with_its_number():
    with pytest.raises(ApiError) as e:
        parse_setlist("Lantern Light\n\n" + "x" * 201)
    assert e.value.status == 400 and "Line 3" in e.value.message
    assert parse_setlist("x" * 200)[0]["title"] == "x" * 200


def test_parser_refuses_more_than_300_songs():
    assert len(parse_setlist("\n".join(f"Song {i}" for i in range(300)))) == 300
    with pytest.raises(ApiError) as e:
        parse_setlist("Encore:\n" + "\n".join(f"Song {i}" for i in range(301)))
    assert e.value.status == 400 and "Line 302" in e.value.message and "300" in e.value.message
