# static/vendor

Third-party JavaScript and CSS, copied in unmodified and pinned. Nothing else lives here.

These files are exempt from the rules for `static/js/` (ES modules, the escaping scan in `tests/test_frontend.py`,
`node --check`): they are classic scripts loaded as they ship. Update one only by replacing it with another pinned
release, and record the new version here.

| Library | Version | Licence | Files | From |
|---|---|---|---|---|

Nothing is vendored yet. Task 9 adds Leaflet and Leaflet.markercluster (both BSD-2-Clause) and fills in this table.
