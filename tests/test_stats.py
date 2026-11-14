"""stats(con) and GET /api/stats: attended shows only, any role counts, a renamed venue is one venue."""
import common
import stats
from conftest import mk_show, mk_venue


def _seed(con):
    v = mk_venue(con, "The Orchard Room", city="Oakland", region="CA", country="US")
    con.execute("INSERT INTO venue_name (venue_id, name, norm) VALUES (?, ?, ?)",
                (v, "Cold Hall", common.venue_norm("Cold Hall")))
    w = mk_venue(con, "Harbor Hall", city="Lyon", region=None, country="FR")
    mk_show(con, "The Paper Lanterns", "2019-05-04", v, lineup=(("The Paper Lanterns", "headliner"),
                                                              ("Marigold Static", "opener")))
    mk_show(con, "Cold Harbor", "2019-08-09", v, venue_label="Cold Hall")
    mk_show(con, "Marigold Static", "2021", w)
    mk_show(con, "Cold Harbor", "2030-01-01", w, status="upcoming")
    return v, w


def test_opener_counts_in_top_artists(con):
    _seed(con)
    top = {a["name"]: a["count"] for a in stats.stats(con)["top_artists"]}
    assert top["Marigold Static"] == 2 and top["The Paper Lanterns"] == 1


def test_upcoming_counts_nowhere(con):
    _seed(con)
    s = stats.stats(con)
    assert s["totals"] == {"shows": 3, "artists": 3, "venues": 2, "cities": 2}
    assert [y["year"] for y in s["per_year"]] == [2019, 2021]
    assert {a["name"]: a["count"] for a in s["top_artists"]}["Cold Harbor"] == 1
    assert sum(c["count"] for c in s["top_cities"]) == 3


def test_renamed_venue_is_one_venue(con):
    v, _ = _seed(con)
    tv = stats.stats(con)["top_venues"]
    assert [(t["venue_id"], t["count"]) for t in tv][0] == (v, 2)
    assert len([t for t in tv if t["venue_id"] == v]) == 1


def test_years_with_no_shows_are_absent(con):
    _seed(con)
    assert stats.stats(con)["per_year"] == [{"year": 2019, "count": 2}, {"year": 2021, "count": 1}]


def test_ties_by_name_and_top_ten(con):
    v = mk_venue(con, "Hall")
    for i in range(12):
        mk_show(con, f"Act {chr(65 + 11 - i)}", f"2020-01-{i + 1:02d}", v)
    top = stats.stats(con)["top_artists"]
    assert len(top) == 10 and [t["name"] for t in top][:3] == ["Act A", "Act B", "Act C"]


def test_empty(con):
    s = stats.stats(con)
    assert s["totals"] == {"shows": 0, "artists": 0, "venues": 0, "cities": 0}
    assert s["per_year"] == [] and s["top_cities"] == []


def test_city_without_region(con):
    _seed(con)
    c = {(t["country"], t["region"], t["city"]) for t in stats.stats(con)["top_cities"]}
    assert ("FR", None, "Lyon") in c


def test_route_public_and_gated(client, con, monkeypatch):
    import settings
    _seed(con)
    r = client.get("/api/stats")
    assert r.status_code == 200 and r.get_json()["totals"]["shows"] == 3
    assert "no-store" in r.headers["Cache-Control"]
    monkeypatch.setattr(settings, "PUBLIC_VIEW", False)
    assert client.get("/api/stats").status_code == 403
