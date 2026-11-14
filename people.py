"""Companions: the people who went to a show with the owner. Editor-only data; nothing here is reachable from a public
route. Functions take a sqlite3.Connection and never import app."""
import db
from common import ApiError

NAME_MAX = 60


def list_people(con):
    rows = con.execute(
        "SELECT p.id, p.name, COUNT(sp.show_id) AS shows FROM person p "
        "LEFT JOIN show_person sp ON sp.person_id = p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.id").fetchall()
    return [{"id": r["id"], "name": r["name"], "shows": r["shows"]} for r in rows]


def create_person(con, name):
    """The id of the person called name, created when missing. Unique case-insensitively (Unicode casefold)."""
    if not isinstance(name, str) or not name.strip():
        raise ApiError(400, "name is required.")
    name = name.strip()
    if len(name) > NAME_MAX:
        raise ApiError(400, f"name must be at most {NAME_MAX} characters.")
    with db.transaction(con):
        for r in con.execute("SELECT id, name FROM person ORDER BY id"):
            if r["name"].casefold() == name.casefold():
                return r["id"]
        return con.execute("INSERT INTO person (name) VALUES (?)", (name,)).lastrowid


def companions(con, show_id):
    rows = con.execute("SELECT p.id, p.name FROM show_person sp JOIN person p ON p.id = sp.person_id "
                       "WHERE sp.show_id = ? ORDER BY p.name COLLATE NOCASE, p.id", (show_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def set_companions(con, show_id, person_ids):
    """Replace the show's companions with person_ids. An unknown id is 400 and changes nothing."""
    ids = list(dict.fromkeys(person_ids))
    with db.transaction(con):
        for pid in ids:
            if not con.execute("SELECT 1 FROM person WHERE id = ?", (pid,)).fetchone():
                raise ApiError(400, f"Unknown person {pid}.")
        con.execute("DELETE FROM show_person WHERE show_id = ?", (show_id,))
        con.executemany("INSERT INTO show_person (show_id, person_id) VALUES (?, ?)", [(show_id, p) for p in ids])


def person_shows(con, person_id):
    rows = con.execute(
        "SELECT s.id, s.date, s.date_precision, s.billing, s.venue_label FROM show_person sp "
        "JOIN show s ON s.id = sp.show_id WHERE sp.person_id = ? ORDER BY s.sort_date DESC, s.id DESC",
        (person_id,)).fetchall()
    return [{"id": r["id"], "date": r["date"], "precision": r["date_precision"], "billing": r["billing"],
             "venue_label": r["venue_label"]} for r in rows]
