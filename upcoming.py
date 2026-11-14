"""Upcoming shows and the "Did you go?" check (Task 14). Editor-only: an upcoming show is never public.
Route-free: every function takes a Connection."""
from common import ApiError, period_end
from records import delete_show

_SELECT = ("SELECT s.id, s.date, s.date_precision, s.billing, s.venue_label, s.sort_date "
           "FROM show s WHERE s.status = 'upcoming'")


def _row(r) -> dict:
    return {"id": r["id"], "date": r["date"], "precision": r["date_precision"], "billing": r["billing"],
            "venue_label": r["venue_label"]}


def upcoming(con) -> list[dict]:
    """Every upcoming show, soonest first."""
    rows = con.execute(f"{_SELECT} ORDER BY s.sort_date, s.id").fetchall()
    return [_row(r) for r in rows]


def due(con, today: str) -> list[dict]:
    """Upcoming shows whose whole period ended before `today` (a day show is due the day after), oldest first."""
    rows = con.execute(f"{_SELECT} ORDER BY s.sort_date, s.id").fetchall()
    return [_row(r) for r in rows if period_end(r["date"], r["date_precision"]) < today]


def resolve(con, show_id, went: bool) -> None:
    """Went: the show becomes attended and its version is bumped. Not went: it is deleted with its images.
    409 when the show isn't upcoming, 404 when there is no such show."""
    row = con.execute("SELECT status FROM show WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Show not found.")
    if row["status"] != "upcoming":
        raise ApiError(409, "That show is already marked as attended.")
    if went:
        con.execute("UPDATE show SET status = 'attended', version = version + 1 WHERE id = ?", (show_id,))
    else:
        delete_show(con, show_id)
