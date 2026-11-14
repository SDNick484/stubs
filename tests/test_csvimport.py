"""csvimport.parse_csv, split_billing and parse_city (spec 7.3 step 1) against tests/fixtures/concerts.csv."""
import os

import pytest

from csvimport import CA_PROVINCES, US_STATES, parse_city, parse_csv, split_billing

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "concerts.csv")


def fixture_bytes():
    with open(FIXTURE, "rb") as f:
        return f.read()


p = parse_csv(fixture_bytes())  # p[i] is data row i + 1


def test_one_proposal_per_row():
    assert len(p) == 15
    assert [x["row_no"] for x in p] == list(range(1, 16))


def test_not_attended_rows_skip():
    assert p[7]["skip_reason"] == p[8]["skip_reason"] == "not attended"


def test_bad_date_skips():
    assert p[13]["skip_reason"].startswith("bad date")
    assert p[13]["date"] is None


def test_good_rows_do_not_skip():
    assert [i for i, x in enumerate(p) if x["skip_reason"]] == [7, 8, 13]


def test_rescheduled_year_only_is_upcoming():
    assert (p[11]["status"], p[11]["date"]) == ("upcoming", "2027")
    assert (p[12]["status"], p[12]["date"]) == ("upcoming", "2026-12-05")


def test_event_with_parenthesised_lineup():
    assert p[2]["billing"] == "Lantern Fest"
    assert [(a["name"], a["role"]) for a in p[2]["acts"]] == [
        ("The Paper Lanterns", "performer"), ("Marigold Static", "performer"), ("Ninefold", "performer")]


def test_plus_splits_coheadliners_and_openers_follow():
    assert [(a["name"], a["role"]) for a in p[3]["acts"]] == [
        ("Cold Harbor", "coheadliner"), ("Velvet Arcade", "coheadliner"), ("Ruth’s Garden", "opener")]
    assert p[3]["acts"][2]["sources"] == [{"label": "setlist.fm", "url": None}]
    assert p[3]["billing"] == "Cold Harbor + Velvet Arcade"


def test_ampersand_never_splits_and_flags():
    assert [a["name"] for a in p[4]["acts"]] == ["Northline & Sable Coast"] and "check acts" in p[4]["flags"]
    assert p[4]["acts"][0]["role"] == "headliner"
    assert p[0]["flags"] == []


def test_opener_with_comma_and_note_is_likely():
    [jun, asl] = p[5]["acts"]
    assert jun["confidence"] == "confirmed" and jun["role"] == "headliner"
    assert asl["name"] == "Andrew, Scott & Laura" and asl["confidence"] == "likely"
    assert asl["confidence_note"] == "tour-wide support; night not confirmed"
    assert asl["role"] == "opener" and asl["sources"] == [{"label": "web", "url": None}]


def test_openers_split_on_semicolon():
    rows = parse_csv(b"date,artist,venue,city,status,openers\n2020-01-01,A,V,\"Oakland, CA\",attended,\"X, Y; Z\"\n")
    assert [a["name"] for a in rows[0]["acts"]] == ["A", "X, Y", "Z"]


def test_ticket_sources_split():
    assert [s["label"] for s in p[1]["sources"]] == ["Ticketmaster", "Last.fm"]
    assert [s["label"] for s in p[4]["sources"]] == ["TicketWeb", "band store"]
    assert p[1]["sources"][0] == {"label": "Ticketmaster", "url": None}


def test_date_approximate_is_likely():
    assert p[9]["confidence"] == "likely" and p[9]["confidence_note"]
    assert p[0]["confidence"] == "confirmed" and p[0]["confidence_note"] is None


def test_city_parsing():
    assert (p[6]["city"], p[6]["region"], p[6]["country"]) == ("Vancouver", "BC", "CA")
    assert (p[0]["city"], p[0]["region"], p[0]["country"]) == ("Oakland", "CA", "US")
    assert p[14]["country"] is None and "country?" in p[14]["flags"]
    assert "country?" not in p[0]["flags"]


def test_parse_city_units():
    assert parse_city("Portland, OR") == ("Portland", "OR", "US")
    assert parse_city("Washington, DC") == ("Washington", "DC", "US")
    assert parse_city("Toronto, ON") == ("Toronto", "ON", "CA")
    assert parse_city("St. Louis, mo") == ("St. Louis", "MO", "US")
    assert parse_city("Lyon, FR") == ("Lyon", "FR", None)
    assert parse_city("Reykjavik") == ("Reykjavik", None, None)
    assert "DC" in US_STATES and len(US_STATES) == 51 and "BC" in CA_PROVINCES and len(CA_PROVINCES) == 13


def test_split_billing_units():
    assert split_billing("Solo Act") == ("Solo Act", [
        {"name": "Solo Act", "role": "headliner", "confidence": "confirmed", "confidence_note": None,
         "sources": []}], [])
    billing, acts, flags = split_billing("Act A + Act B & Friends")
    assert [a["name"] for a in acts] == ["Act A", "Act B & Friends"] and flags == ["check acts"]
    assert split_billing("Band (UK)")[1][0]["name"] == "Band (UK)"
    assert split_billing("C+D")[1][0]["name"] == "C+D"


def test_notes_kept_verbatim():
    assert p[10]["notes"] == "Tickets transferred to Riley Example"
    assert p[0]["notes"] is None


def test_other_proposal_fields():
    x = p[0]
    assert (x["venue"], x["status"], x["date"], x["tour"], x["run"]) == ("The Bellwether", "attended", "2005-04-02",
                                                                         None, None)


def test_unknown_status_skips():
    rows = parse_csv(b"date,artist,venue,city,status\n2020-01-01,A,V,\"Oakland, CA\",maybe\n")
    assert rows[0]["skip_reason"].startswith("unknown status")


# Review focus 1: a CSV saved from Excel
def test_bom_and_crlf():
    raw = b"\xef\xbb\xbf" + fixture_bytes().replace(b"\n", b"\r\n")
    assert parse_csv(raw) == p
    assert parse_csv(raw)[0]["date"] == "2005-04-02"  # the BOM did not stick to the first header


def test_cp1252_fallback():
    raw = fixture_bytes().decode("utf-8").encode("cp1252")
    assert b"Ruth\x92s Garden" in raw
    rows = parse_csv(raw)
    assert rows[3]["acts"][2]["name"] == "Ruth’s Garden"
    assert rows == p


def test_unknown_columns_ignored():
    lines = fixture_bytes().decode("utf-8").splitlines()
    raw = "\n".join([lines[0] + ",seat"] + [ln + ",12B" for ln in lines[1:]]).encode("utf-8")
    assert parse_csv(raw) == p


def test_column_order_and_header_case_do_not_matter():
    raw = b'Status, Venue ,CITY,Artist,Date\nattended,V,"Oakland, CA",A,2020-02-02\n'
    assert parse_csv(raw)[0]["billing"] == "A"


def test_missing_required_column_is_an_error():
    raw = fixture_bytes().replace(b"date,artist,venue,city", b"date,artist,place,city", 1)
    with pytest.raises(ValueError, match="missing column: venue"):
        parse_csv(raw)


def test_empty_file_is_an_error_and_header_only_is_empty():
    with pytest.raises(ValueError):
        parse_csv(b"")
    assert parse_csv(b"date,artist,venue,city,status\n") == []
