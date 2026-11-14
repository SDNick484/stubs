# static/vendor

Third-party JavaScript and CSS, copied in unmodified and pinned. Nothing else lives here.

These files are exempt from the rules for `static/js/` (ES modules, the escaping scan in `tests/test_frontend.py`,
`node --check`): they are classic scripts loaded as they ship. Update one only by replacing it with another pinned
release, and record the new version here.

| Library | Version | Licence | Files | From |
|---|---|---|---|---|
| Leaflet | 1.9.4 | BSD-2-Clause | `leaflet/leaflet.js`, `leaflet/leaflet.css`, `leaflet/images/*.png`, `leaflet/LICENSE` | `npm pack leaflet@1.9.4` (the registry.npmjs.org tarball), `dist/` |
| Leaflet.markercluster | 1.5.3 | MIT | `markercluster/leaflet.markercluster.js`, `markercluster/MarkerCluster.css`, `markercluster/MarkerCluster.Default.css`, `markercluster/LICENSE` | `npm pack leaflet.markercluster@1.5.3` (the registry.npmjs.org tarball), `dist/` |

Both were taken from the registry tarballs, versions checked against each `package.json`, and copied
(source maps left out). Nothing is altered:
`tests/test_hygiene.py` skips its URL-host check (only that check) under `static/vendor/`. Leaflet.markercluster 1.5.3 declares and ships the MIT licence, not BSD-2-Clause.
Leaflet only sets styles from script (`element.style`), which the CSP's `style-src 'self'` allows; its images load
from `'self'`.
