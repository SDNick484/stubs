"""common.py: dates, names, request-body helpers and the clock."""
from datetime import date

import pytest

import app as stubs
import common
from common import ApiError, json_body, norm, parse_date, period_end, text, today, venue_norm


@pytest.mark.parametrize("raw,out", [
    ("2025-07-19", ("2025-07-19", "day", "2025-07-19")),
    ("2025-07", ("2025-07", "month", "2025-07-01")),
    ("2027", ("2027", "year", "2027-01-01")),
    (" 2024-02-29 ", ("2024-02-29", "day", "2024-02-29"))])
def test_parse_date(raw, out):
    assert parse_date(raw) == out


@pytest.mark.parametrize("bad", ["2018-13-40", "19/07/2025", "", "2025-7-1", "2025-13", "2023-02-29", "0000",
                                 "25", "2025-07-19T20:00", "２０２５", None, 2025])
def test_parse_date_rejects(bad):
    with pytest.raises(ValueError):
        parse_date(bad)


def test_period_end():
    assert period_end("2027", "year") == "2027-12-31"
    assert period_end("2024-02", "month") == "2024-02-29"
    assert period_end("2025-02", "month") == "2025-02-28"
    assert period_end("2025-07-19", "day") == "2025-07-19"
    with pytest.raises(ValueError):
        period_end("2025-07", "day")  # precision must agree with the date


def test_norm():
    assert norm("  Ruth’s   Garden ") == norm("ruth's garden") == "ruth's garden"
    assert norm("Juniper & The Owls") != norm("Juniper and The Owls")
    assert venue_norm("The Bellwether") == venue_norm("Bellwether")


def test_norm_details():
    assert norm("“Lantern Fest”") == '"lantern fest"'
    assert norm("ＣＯＬＤ\tHarbor\n") == "cold harbor"  # NFKC folds full-width letters; tabs and newlines collapse
    assert norm("") == "" and norm(None) == ""
    assert venue_norm("  THE   Old Mill Theatre") == "old mill theatre"
    assert venue_norm("Theatre Royal") == "theatre royal"  # only a leading "the " word is dropped
    assert venue_norm("The") == "the"


def test_today_is_iso_and_clock_freezes_it(clock):
    assert today() == date.today().isoformat()
    clock.set("2026-11-14")
    assert today() == common.today() == "2026-11-14"


def test_json_body_and_text():
    with stubs.app.test_request_context("/x", method="POST", json={"label": "  Ticketmaster  ", "n": 3, "e": ""}):
        d = json_body()
        assert text(d, "label", 20) == "Ticketmaster"
        assert text(d, "e", 20) is None and text(d, "missing", 20) is None
        for call in (lambda: text(d, "label", 5), lambda: text(d, "n", 20), lambda: text(d, "missing", 20, required=True)):
            with pytest.raises(ApiError) as e:
                call()
            assert e.value.status == 400
    for body in ([1, 2], "x", None):
        with stubs.app.test_request_context("/x", method="POST", json=body):
            with pytest.raises(ApiError) as e:
                json_body()
            assert e.value.status == 400
    with stubs.app.test_request_context("/x", method="POST", data="{not json", content_type="application/json"):
        with pytest.raises(ApiError):
            json_body()


def test_text_over_limit_is_400_never_cut():
    with pytest.raises(ApiError) as e:
        text({"notes": "x" * 11}, "notes", 10)
    assert e.value.status == 400 and "10" in e.value.message
    assert text({"notes": "x" * 10}, "notes", 10) == "x" * 10
