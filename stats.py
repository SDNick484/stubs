"""Totals, shows per year and top lists. Attended shows only; read-only queries, no private columns."""
TOP = 10


def _rows(con, sql):
    return [dict(r) for r in con.execute(sql, (TOP,))]


def stats(con) -> dict:
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    totals = {
        "shows": one("SELECT COUNT(*) FROM show WHERE status = 'attended'"),
        "artists": one("SELECT COUNT(DISTINCT l.artist_id) FROM lineup l JOIN show s ON s.id = l.show_id "
                       "WHERE s.status = 'attended'"),
        "venues": one("SELECT COUNT(DISTINCT venue_id) FROM show WHERE status = 'attended'"),
        "cities": one("SELECT COUNT(*) FROM (SELECT DISTINCT COALESCE(v.country, ''), COALESCE(v.region, ''), "
                      "v.city FROM show s JOIN venue v ON v.id = s.venue_id WHERE s.status = 'attended')"),
    }
    per_year = [{"year": int(r["y"]), "count": r["n"]} for r in con.execute(
        "SELECT substr(sort_date, 1, 4) AS y, COUNT(*) AS n FROM show WHERE status = 'attended' "
        "GROUP BY y ORDER BY y")]
    top_artists = _rows(con, "SELECT a.id AS artist_id, a.name AS name, COUNT(*) AS count FROM lineup l "
                             "JOIN show s ON s.id = l.show_id JOIN artist a ON a.id = l.artist_id "
                             "WHERE s.status = 'attended' GROUP BY a.id ORDER BY count DESC, a.name, a.id LIMIT ?")
    top_venues = _rows(con, "SELECT v.id AS venue_id, v.name AS name, COUNT(*) AS count FROM show s "
                            "JOIN venue v ON v.id = s.venue_id WHERE s.status = 'attended' "
                            "GROUP BY v.id ORDER BY count DESC, v.name, v.id LIMIT ?")
    top_cities = _rows(con, "SELECT v.country AS country, v.region AS region, v.city AS city, COUNT(*) AS count "
                            "FROM show s JOIN venue v ON v.id = s.venue_id WHERE s.status = 'attended' "
                            "GROUP BY COALESCE(v.country, ''), COALESCE(v.region, ''), v.city "
                            "ORDER BY count DESC, v.city, v.region, v.country LIMIT ?")
    top_songs = _rows(con, "SELECT g.id AS song_id, g.title AS title, a.id AS artist_id, a.name AS artist, "
                           "COUNT(*) AS count FROM setlist_entry e JOIN lineup l ON l.id = e.lineup_id "
                           "JOIN show s ON s.id = l.show_id JOIN song g ON g.id = e.song_id "
                           "JOIN artist a ON a.id = g.artist_id WHERE s.status = 'attended' "
                           "GROUP BY g.id ORDER BY count DESC, g.title, g.id LIMIT ?")
    return {"totals": totals, "per_year": per_year, "top_artists": top_artists, "top_venues": top_venues,
            "top_cities": top_cities, "top_songs": top_songs}
