"""The to-confirm list (Task 13): shows and lineup entries recorded as "likely", oldest show first. Editor-only: the
confidence and its note never reach a public response."""
from common import ApiError
from db import transaction
from records import add_source

KINDS = ("show", "lineup")

_SHOW = ("s.id AS show_id, s.date, s.date_precision, s.billing, s.venue_label, s.sort_date")


def _show(r):
    return {"id": r["show_id"], "date": r["date"], "precision": r["date_precision"], "billing": r["billing"],
            "venue_label": r["venue_label"]}


def _sources(con, show_id, lineup_id):
    q = "SELECT label, url FROM source WHERE show_id = ? AND lineup_id "
    args = (show_id,) if lineup_id is None else (show_id, lineup_id)
    rows = con.execute(q + ("IS NULL" if lineup_id is None else "= ?") + " ORDER BY id", args)
    return [{"label": r["label"], "url": r["url"]} for r in rows]


def to_confirm(con) -> list:
    """Likely shows and likely lineup entries, oldest show first (upcoming included); a show's own entry comes
    before its lineup entries."""
    items = []
    for r in con.execute(f"SELECT {_SHOW}, s.confidence_note AS note FROM show s WHERE s.confidence = 'likely'"):
        items.append((r["sort_date"], r["show_id"], 0, 0, {
            "kind": "show", "id": r["show_id"], "show": _show(r), "artist": None, "note": r["note"],
            "sources": _sources(con, r["show_id"], None)}))
    for r in con.execute(
            f"SELECT {_SHOW}, l.id AS lineup_id, l.position, l.confidence_note AS note, a.name AS artist "
            "FROM lineup l JOIN show s ON s.id = l.show_id JOIN artist a ON a.id = l.artist_id "
            "WHERE l.confidence = 'likely'"):
        items.append((r["sort_date"], r["show_id"], 1, r["position"], {
            "kind": "lineup", "id": r["lineup_id"], "show": _show(r), "artist": r["artist"], "note": r["note"],
            "sources": _sources(con, r["show_id"], r["lineup_id"])}))
    items.sort(key=lambda t: t[:4])
    return [t[4] for t in items]


def confirm(con, kind, id, source_label=None, source_url=None) -> None:
    """Mark one likely fact confirmed and clear its note; add a source when a label is given; bump the show's
    version. Atomic. 400 for an unknown kind, 404 for an unknown id."""
    if kind not in KINDS:
        raise ApiError(400, "kind must be show or lineup.")
    with transaction(con):
        if kind == "show":
            row = con.execute("SELECT id FROM show WHERE id = ?", (id,)).fetchone()
            show_id, lineup_id = (row["id"], None) if row else (None, None)
        else:
            row = con.execute("SELECT show_id FROM lineup WHERE id = ?", (id,)).fetchone()
            show_id, lineup_id = (row["show_id"], id) if row else (None, None)
        if show_id is None:
            raise ApiError(404, "Not found")
        if source_label and source_label.strip():
            add_source(con, show_id, source_label, source_url, lineup_id)
        if kind == "show":
            con.execute("UPDATE show SET confidence = 'confirmed', confidence_note = NULL WHERE id = ?", (id,))
        else:
            con.execute("UPDATE lineup SET confidence = 'confirmed', confidence_note = NULL WHERE id = ?", (id,))
        con.execute("UPDATE show SET version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (show_id,))
