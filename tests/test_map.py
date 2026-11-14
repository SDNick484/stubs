"""map.py and GET /api/map: attended shows grouped by venue, venues with coordinates only."""
from pathlib import Path

from conftest import mk_show, mk_venue
from map import unmapped_count, venue_points


def test_points_group_shows_by_venue(con):
    v = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.2)
    a = mk_show(con, "Cold Harbor", "2009-06-20", v)
    b = mk_show(con, "Marigold Static", "2010-01-02", v)
    (p,) = venue_points(con)
    assert p == {"venue_id": v, "name": "Granite Hall", "city": "Oakland", "lat": 37.8, "lon": -122.2, "count": 2,
                 "show_ids": [b, a]}
    assert unmapped_count(con) == 0


def test_points_skip_upcoming_and_unlocated(con):
    v = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.2)
    nowhere = mk_venue(con, "The Bellwether")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Marigold Static", "2030-01-02", v, status="upcoming")
    mk_show(con, "The Paper Lanterns", "2011-05-05", nowhere)
    assert [p["count"] for p in venue_points(con)] == [1]
    assert unmapped_count(con) == 1


def test_points_filter_by_year(con):
    v = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.2)
    w = mk_venue(con, "The Bellwether")
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Marigold Static", "2010-01-02", v)
    mk_show(con, "The Paper Lanterns", "2010-05-05", w)
    assert [p["count"] for p in venue_points(con, year=2009)] == [1]
    assert venue_points(con, year=2010)[0]["count"] == 1
    assert unmapped_count(con, year=2009) == 0 and unmapped_count(con, year=2010) == 1
    assert venue_points(con, year=1999) == []


def test_map_route(client, con):
    v = mk_venue(con, "Granite Hall", lat=37.8, lon=-122.2)
    mk_show(con, "Cold Harbor", "2009-06-20", v)
    mk_show(con, "Marigold Static", "2030-01-02", v, status="upcoming")
    body = client.get("/api/map").get_json()
    assert body["unmapped"] == 0 and body["points"][0]["count"] == 1
    assert client.get("/api/map?year=2010").get_json() == {"points": [], "unmapped": 0}
    assert client.get("/api/map?year=abc").status_code == 400
    assert client.get("/api/map?year=abc").get_json() == {"error": "year must be four digits"}


def test_vendor_files_present_and_pinned():
    readme = Path("static/vendor/README.md").read_text()
    assert "1.9.4" in readme and "1.5.3" in readme
    for f in ("leaflet/leaflet.js", "leaflet/leaflet.css", "leaflet/images/marker-icon.png",
              "markercluster/leaflet.markercluster.js", "markercluster/MarkerCluster.css",
              "markercluster/MarkerCluster.Default.css"):
        assert Path("static/vendor", f).is_file(), f
    assert "Leaflet 1.9.4" in Path("static/vendor/leaflet/leaflet.js").read_text()[:200]
