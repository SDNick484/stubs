# Stubs

A self-hosted concert history (Flask + SQLite): every show the owner has been to, who was on the bill, where, and
what proves it; imported from a CSV, edited by hand, browsed on a map with a year strip. Spec:
`docs/superpowers/specs/2026-11-13-stubs-design.md`. Plan: `docs/superpowers/plans/2026-11-13-stubs.md` (each task is
delivered as one PR; the plan's task block is the contract for names, signatures and messages; where it and the spec
disagree, the spec wins and the PR says so).

**Status:** Built: Task 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20. Post-v1: #43.

## Commands

```
python -m pytest -q                                 # report the skip count and reasons
node --test tests/*.test.mjs                        # DOM-free helpers in static/js/
for f in static/js/*.js; do node --check "$f"; done
ruff check
shellcheck -s sh installer/setup-head.sh installer/build.sh ops/stubs-backup
sh installer/build.sh && sh -n dist/setup-stubs.sh  # writes dist/setup-stubs.sh (git-ignored)
python3 app.py                                      # dev server on 127.0.0.1:8773
```

Run the whole gate before every commit. CI (`.github/workflows/ci.yml`) runs the same in the jobs Lint, UI smoke,
Python 3.10–3.13 and Alpine. The UI smoke job runs `tests/test_*_ui.py` with Playwright and Chromium at 412 px and
fails on any skip (`STUBS_UI_REQUIRED=1`); the other jobs leave those files out. Locally they run when Playwright and
its Chromium are installed (`pip install playwright && python -m playwright install chromium`) and skip otherwise.
`tests/test_hygiene.py` walks `git ls-files`, so new files must be added to git before it sees them.

## Rules that must keep holding

- Python 3.10–3.13 (no newer syntax or stdlib). Runtime dependencies are only the Alpine packages `py3-flask
  py3-gunicorn py3-jwt py3-cryptography`, plus `sqlite` for the backup. Everything else is the standard library
  (`urllib.request`, `html.parser`, `csv`, `json`, `ipaddress`, `socket`, `unicodedata`): no requests, no Pillow, no
  BeautifulSoup. Dev only: pytest, `ruff==0.16.9`, `shellcheck-py==0.11.0.1`, playwright (UI smoke job).
- No frontend framework, bundler or npm step. Native ES modules in `static/js/`, with `static/js/package.json` =
  `{"type": "module"}`. Third-party JS only in `static/vendor/`, pinned, with version and licence in
  `static/vendor/README.md`.
- Never committed: the owner's real concert CSV or any row of it, real people's names, the owner's domain or
  hostnames, LAN addresses, container IDs, API keys, `data/`, `dist/`, `.env`. Tests use invented acts (The Paper
  Lanterns, Marigold Static, Cold Harbor…), invented people (Alex, Sam, Jordan, Riley), `concerts.example.com` and
  `192.168.1.100`. `.gitignore` ignores `*.csv` except `tests/fixtures/*.csv`.
- Schema changes go through the idempotent `migrate()` and bump `SCHEMA_VERSION`. No manual DB step on the host.
- Writes live under `/admin/api/` with `@editor_required`; public reads under `/api/` with `@public_read` (403
  `{"error": "Sign in to view"}` when `PUBLIC_VIEW=0` and not an editor).
- Public responses never carry: upcoming shows, notes, sources, confidence, companions, ratings, posters (unless
  `PUBLIC_POSTERS=1`), stubs, imports. A public request for an upcoming show's id is 404, never 403.
  `tests/test_privacy.py` checks every `/api/` GET rule.
- Every response: `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `X-Content-Type-Options: nosniff`, and CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data: https://<TILE_URL host>; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`.
  `Cache-Control: no-store` on `/api/` and `/admin/api/`.
- Escaping: every `${...}` that builds markup is exactly one call to `esc`, `fmtDate` or a `render*` function;
  `innerHTML` is only assigned a template literal, `""` or a `render*` call; no inline `<script>`/`<style>`,
  `style="..."`, `on*=`, `.onclick =`, `confirm(` or `alert(` in `static/js/`. `tests/test_frontend.py` scans for all
  of it (it doesn't parse regex literals: keep quotes and backticks out of them, as `\x22`, `\x27`, `\x60`).
- Errors are JSON `{"error": str}` via `common.ApiError(status, message)`. Bodies via `json_body()`; strings via
  `text(data, key, max_len)`; text over its limit is 400, never cut.
- Outbound HTTP sends `settings.UA` (`stubs/1.0 (concert history; https://github.com/SDNick484/stubs)`) and a timeout;
  tests use recorded fixtures under `tests/fixtures/` and never touch the network. External features test a hit, a
  miss, an HTTP error and a timeout.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`, on
  a feature branch merged by PR. Docs use OpenRC commands and say `vim`.
- Default port 8773 (`PORT`); gunicorn binds `127.0.0.1` only.

## Layout

```
app.py            Flask object, access (access_identity, lan_host, cross_site, is_editor, editor_required,
                  public_read), the security-header hook, error handlers, every route. Task 1's routes: /, /admin,
                  /manifest.json, /robots.txt, /healthz, /api/session, /admin/api/session. Section markers
                  `# ==== Task N: <name>` for each later task.
settings.py       environment config, read once at import after /etc/conf.d/stubs and ./.env (keys not already set)
db.py             SCHEMA (v3, spec section 5), SCHEMA_VERSION, connect(), transaction(), migrate() (MIGRATIONS: _to_v1,
                  _to_v2 moves v1's per-show setlist_link onto lineup entries, _to_v3 adds _V3_SCHEMA's stored-setlist
                  tables), db() (per request, in g), close_db(), init_db()
common.py         ApiError, json_body, text, norm, venue_norm, parse_date, period_end, today
csvimport.py      parse_csv(bytes) -> Proposals (UTF-8/BOM or cp1252, any CRLF), split_billing, parse_city, US_STATES,
                  CA_PROVINCES (spec 7.3 step 1); tests/fixtures/concerts.csv is its invented sample, reused by Task 4
records.py        write-side core taking a Connection: resolve_artist, get_or_create_artist/venue/run, find_venue,
                  create_show, add_lineup_entry, add_source, delete_show (unlinks image files); songs (#43):
                  resolve_song(con, artist_id, title) (song.norm, then song_alias.norm, within the artist),
                  get_or_create_song, drop_unused_songs(con, artist_ids) (not played, not in_catalog, no alias),
                  move_setlist_songs (a renamed lineup entry's setlist follows it). Bad input is ApiError
browse.py         the browse reads (Task 6): show_row (the Show row contract, from a SHOW_SELECT row), list_shows,
                  show_detail (each lineup entry carries its stored `setlist` or None), artist_detail, venue_detail,
                  years, city_detail; each served at /api/ (editor=False,
                  whoever asks) and /admin/api/ (editor=True). Public: attended shows only, no editor-only field;
                  an upcoming show, and an artist, venue or city with no attended show, is None -> 404 "Not found".
                  Lists newest first (editors' upcoming shows first). City paths: /api/cities/<country>/<region>/<city>,
                  a missing country or region written "-" (/api/cities/FR/-/Lyon), matched case-insensitively
stats.py          stats(con): totals, shows per year, top artists/venues/cities (attended only); GET /api/stats
people.py         companions (editor-only): list_people, create_person, companions, set_companions, person_shows
geocode.py        Nominatim lookup (1 req/s, UA, 10 s), locate_venue, set_coords, missing_coords (Task 16); its
                  front end is static/js/venues.js (#/admin/venues, the "Not on the map" card)
setlistfm.py      setlist.fm client (Task 18, per act since #40): search(artist, date), search_venue(venue, city, date),
                  find_for_show(con, show_id) (venue + date, matched to the lineup through resolve_artist; links
                  nothing), fetch_setlist(id) (1 h in-memory cache, never stored), link/unlink/linked(con, lineup_id)
                  (setlist_link: id and url only), panel(con, show_id, editor=) (each linked act, lineup order),
                  Unavailable. Routes in app.py: GET .../shows/<id>/setlist, GET /admin/api/shows/<id>/setlistfm/search
                  [?lineup_id=], PUT/DELETE /admin/api/shows/<id>/lineup/<lineup_id>/setlistfm
images.py         posters, photos, stubs (spec 7.9): MAX_IMAGE_BYTES, sniff_image, save_image, list_images, update_image,
                  delete_image, visible_file (who may see a file); routes in app.py under Task 17
merge.py          merge_artists, merge_venues (atomic; aliases and former names kept, shows' version bumped; an artist
                  merge folds songs by title/alias and moves a folded entry's stored setlist like its setlist_link),
                  search_artists, search_venues (GET /admin/api/artists|venues?q=, also Task 11's venue picker)
imports.py        import batches (spec 7.3 steps 2-4): validate_proposal, match, create_batch, batch_view (the review
                  shape Task 5 reads), update_row, apply_batch (one IMMEDIATE transaction; a second apply is 409),
                  discard_batch, pending_batches; routes in app.py under Task 4 at /admin/api/imports and
                  /admin/api/import-rows/<id>
confirm.py        the to-confirm list (Task 13): to_confirm(con), confirm(con, kind, id, source_label, source_url);
                  routes GET /admin/api/to-confirm, POST /admin/api/to-confirm/confirm in app.py
upcoming.py       upcoming shows (Task 14): due(con, today), upcoming(con), resolve(con, show_id, went) (went -> attended,
                  version bumped; not went -> records.delete_show; 409 if not upcoming); routes in app.py
map.py            venue_points(con, year=None), unmapped_count(con, year=None): attended shows grouped by venue
                  (coordinates only) for GET /api/map?year= (Task 9)
fetchurl.py       fetch from a URL (spec 7.12) behind the SSRF guard: check_host (every resolved address public;
                  inet_aton spellings and IPv4-in-IPv6 unmasked), safe_fetch (http.client pinned to the checked
                  address, 5 redirects each re-checked, 5 s, 2 MB), extract (og:image, og:title, JSON-LD Event),
                  suggestions, apply (poster, performer, source). Routes in app.py; tests patch _open and getaddrinfo
editing.py        show editing (Task 11): create, update_show (version-checked, 409 STALE), set_lineup, delete,
                  add_show_source, remove_source, list_runs; routes in app.py (POST/PATCH/DELETE
                  /admin/api/shows[/<id>], PUT .../lineup, sources, GET /admin/api/runs)
setlists.py       stored setlists (post-v1 #43): parse_setlist(text) (the paste parser: numbering, headings, encores,
                  segues, notes; 400 naming the line), preview, save (version-checked, bumps it; songs made per act),
                  delete, for_show(con, show_id, editor=) (what show_detail adds; source_label/url for editors only);
                  routes in app.py under "Post-v1 #43": POST /admin/api/lineup/<id>/setlist/preview, PUT and DELETE
                  /admin/api/lineup/<id>/setlist. 409 "Add a setlist after the show." on an upcoming show
venueedit.py      venue editing (Task 20): view, update_venue (name/city/region/country; a rename keeps the old name,
                  coordinates kept), add_name, remove_name (409 for the current name, a name a ticket uses, or a name
                  another venue in the city has); routes in app.py under Task 20 at /admin/api/venues/<id>[/names]
static/           index.html (the shell; / and /admin fill in __APP_NAME__), style.css (markers `/* ==== Task N */`),
                  manifest.json (GET /manifest.json sets name and short_name), icon.svg, robots.txt
static/js/        hash-routed ES modules, no build step: main.js (router, header, document.title; route(),
                  headerLink(); markers `// ==== Task N`), core.js (esc, fmtDate, placeLabel, roleLabel,
                  renderShowRow, parseHash, matchRoute, pickRoute; no DOM at import), api.js (get, send, ApiError),
                  registry.js (registerShowPanel, registerAdminCard, showPanels, adminCards), admin.js (#/admin)
  stats.js        #/stats: renderBars, cityHref, renderStats
  companions.js   the With show panel (order 40) and #/admin/people
  venueedit.js    #/admin/venue/<id>/edit and the "Edit venue" link on the venue page under /admin: renderNameRow,
                  renderVenueForm, changedFields
  venues.js       #/admin/venues and the "Not on the map" admin card (order 40)
  setlist.js      the Setlist show panel (order 20): each linked act's set; editors get Find setlists (confirm the
                  ticked acts) and Find per act. renderPanelBody, renderAct, renderShowMatches, defaultPick,
                  renderResults
  setlists.js     the Setlists show panel (order 15, #43): the owner's setlists from the show detail, by set and
                  encore; editors get Add setlist / Edit (paste, Preview with new songs tagged, source, Save) and an
                  inline Delete. renderSets, groupEntries, setlistText, renderPreview, renderPanelBody,
                  renderSetlistsPanel
  images.js       the Pictures show panel (order 10): groupImages, renderPictures, renderPicturesPanel
  merge.js        #/admin/merge: renderMergeConfirm, renderPick
  confirm.js      #/admin/confirm and the To confirm admin card (order 20): renderConfirmItem, renderConfirmPage,
                  renderConfirmCard
  didyougo.js     the "Did you go?" (order 10) and "Coming up" (order 15) admin cards: renderDue, renderUpcoming
  show.js         #/show/<id>: renderLineup, renderSources, renderShow, drawPanels (each panel isolated), renderShowPage
  pages.js        #/artist/:id, #/venue/:id, #/year/:yyyy, #/city/:country/:region/:city: renderArtistSummary,
                  renderVenueSummary
  home.js         #/: map, year strip, show list; renderYearStrip, renderUnmapped, renderPopup
  leaflet-loader.js  loadLeaflet(): loads the vendored Leaflet and markercluster once, resolves with window.L
  urlfetch.js     the "From a link" show panel (editor-only, order 50): renderSuggestions
  imports.js      #/admin/import, #/admin/import/<id> and the Imports waiting admin card (order 30): renderImportRow,
                  renderGroups
  edit.js         #/admin/show/new, #/admin/show/<id>/edit, the Edit show panel (order 0), "+ Show" header link
static/vendor/    pinned third-party files (README.md lists them): leaflet/ 1.9.4 (BSD-2-Clause),
                  markercluster/ 1.5.3 (MIT); exempt from the static/js/ rules
tests/            conftest.py + one test file per module; test_privacy.py (public walk), test_hygiene.py,
                  test_frontend.py (shell, CSP and escaping scans), test_installer_static.py, test_shell_ui.py
                  (Playwright), frontend.test.mjs (node --test)
installer/        setup-head.sh (the installer's text), build.sh (head + base64 tarball of every top-level *.py,
                  README.md, .env.example, ops/ and static/ -> dist/setup-stubs.sh)
ops/              stubs-backup (nightly `sqlite3 .backup` + images tarball, 0600 in /var/backups/stubs, 14 days)
.env.example      every setting, with its default
```

Add each new module to this list when it lands.

## Shared append points

These files are added to by many tasks. Add only inside your own task's marked section, so parallel PRs touch
different hunks.

| File | What a task adds |
|---|---|
| `app.py` | Its imports and routes, under `# ==== Task N: <name>` |
| `static/js/main.js` | Its `import` line and `route(...)` / `headerLink(...)` / `registerShowPanel(...)` / `registerAdminCard(...)` calls, under `// ==== Task N` |
| `static/style.css` | Its rules, under `/* ==== Task N */` |
| `tests/conftest.py` | Only helpers two or more test files use, appended at the end; prefer helpers inside your own test file |
| `tests/test_privacy.py` | A line in `PARAM_SAMPLES` only if a new public route uses a path parameter not already listed |
| `CLAUDE.md` | Its module in **Layout** and its task number in the **Status** line (`Built: Task 1, …`) |

`installer/build.sh` copies every top-level `*.py`, `static/` and `ops/`, so a new module needs no installer edit. The
UI smoke job runs `tests/test_*_ui.py` by glob, so a new UI test needs no CI edit. Every setting already exists
(spec section 8): a task that needs a new one adds it to `settings.py`, `.env.example`, the installer's `add_setting`
list and the init script's `export` line (`tests/test_installer_static.py` checks all four).

## Conventions

- Route-free logic lives in sibling modules that take a `sqlite3.Connection` and never import `app`; routes call
  `db()`. Connections are autocommit: wrap anything that must be atomic in `with db.transaction(con):` (nests as a
  savepoint).
- Schema decisions (v1, plus v2's per-act `setlist_link` and v3's stored setlists): names match through `artist.norm`
  (UNIQUE) and `artist_alias.norm`; every name a venue has had is a `venue_name` row (`UNIQUE(norm, venue_id)`, norm =
  `venue_norm`), the current one included. `show.date` is `YYYY`, `YYYY-MM` or `YYYY-MM-DD` with `date_precision` and
  `sort_date` (first day of the period) from `common.parse_date`. `ON DELETE CASCADE` from `lineup`, `source`,
  `show_person`, `image` to `show`, from `source` and `setlist_link` (one per lineup entry, schema v2) to `lineup`, and
  from `import_row` to `import_batch`; `import_row.match_show_id` is set to NULL when its show is deleted. Artists,
  venues, runs and people are never cascaded: a venue or artist still in use can't be deleted (merge first). Schema v3
  (#43): `song(id, artist_id, title, norm, in_catalog, created_at, updated_at)` `UNIQUE(artist_id, norm)` (a song is the
  performing act's; `in_catalog` is for #44), `song_alias(artist_id, norm, song_id)` PRIMARY KEY `(artist_id, norm)`,
  `setlist(lineup_id PRIMARY KEY, source_label, source_url, created_at, updated_at)` (one per lineup entry, cascades
  from `lineup`), `setlist_entry(id, lineup_id, position, set_name, encore, song_id, note, segue)` `UNIQUE(lineup_id,
  position)` (cascades from `setlist`). Songs are never cascaded: writers that drop setlist entries call
  `records.drop_unused_songs`. Timestamps are SQLite `CURRENT_TIMESTAMP` text.
- Access (`app.py`): `access_identity()` is `"api-key"` (`STUBS_API_KEY` as `X-API-Key` or `Authorization: Bearer`),
  `"read-key"` (`STUBS_READ_KEY`: editor reads, no writes), the email of a Cloudflare Access JWT
  (`Cf-Access-Jwt-Assertion`, RS256, audience `CF_AUD`, issuer `https://<CF_TEAM_DOMAIN>`) that is in
  `ADMIN_EMAILS`, or `"lan"` (`LAN_EDIT=1`, no `Cf-Ray`/`Cf-Connecting-Ip`/JWT header, and a `Host` in `LAN_HOSTS`),
  else None. `editor_required` refuses a cross-site browser request (403, `Origin` host or `Sec-Fetch-Site:
  cross-site`), then non-editors (401 `Sign in to edit`), then the read key on a write (403).
- Cloudflare Access guards only `/admin`, so only requests under `/admin` carry the editor's JWT. The shell is served
  at `/` and `/admin`; main.js asks `/admin/api/session` on pages under `/admin` and `/api/session` elsewhere. Editor
  pages fetch `/admin/api/...`; public pages fetch `/api/...`. With `PUBLIC_VIEW=0`, `/` redirects to `/admin`.
- Tests: `tests/conftest.py` puts every setting in the environment before importing `app` (so `/etc/conf.d/stubs`
  and `.env` can't leak in), gives every test a fresh database, and offers `client` (anonymous, `LAN_EDIT` off,
  `PUBLIC_VIEW` on), `editor` (sends `X-API-Key: test-write-key`), `con`, `clock` (`clock.set("2026-11-14")` freezes
  `common.today()`, even where it was imported by name), and the seeders `mk_artist(con, name)`,
  `mk_venue(con, name, city="Oakland", region="CA", country="US", lat=None, lon=None)` and
  `mk_show(con, billing, date, venue_id, lineup=((billing, "headliner"),), status="attended", **cols)`. Settings are
  read as `settings.X` at request time, so tests change them with `monkeypatch.setattr(settings, ...)`.
- Frontend: a route renders into `ctx.view` (`ctx = {view, params, query, session, alive, rerender}`); check
  `ctx.alive()` after each await. Show panels and admin cards plug in through `registry.js` with an `order`
  (spec'd orders: Edit 0, Pictures 10, Setlists 15, Setlist 20, With 40, From a link 50; cards Did you go? 10, Coming up 15,
  To confirm 20, Imports waiting 30, Not on the map 40). An admin card's `render(el)` may return `false` to hide
  itself; with nothing left `#/admin` says "Nothing needs you." `core.js` touches no DOM at import so `node --test`
  can load it. `document.title` is set only in `main.js`, from `session.app_name`. Deletes and confirmations use an
  inline "Delete? Yes / No", never `window.confirm`.
- Installer: Alpine + OpenRC, service `stubs` (gunicorn on `127.0.0.1:${PORT}` as user `stubs`), `/opt/stubs`
  replaced on each run, `/var/lib/stubs` (with `images/`) and `/etc/conf.d/stubs` (root only, 0600) kept;
  `STUBS_PORT` picks the port on a first install. `add_setting` only adds missing keys, single-quoted. Nightly
  `/etc/periodic/daily/stubs-backup`, 14 days, optional `BACKUP_TARGET` copy and health check via `/etc/conf.d/ops`.

## Departures from Matinee's conventions (spec section 12)

1. **No MCP server in v1** (Matinee rule 6). It is the third item after v1; the import API is its seam.
2. **A strict CSP**, taken from Gift Exchange (above). This brings Gift Exchange's escaping rules (`esc()` on every
   interpolation, no inline handlers or `style=` in markup) and its scan test.
3. **Referrer policy `strict-origin-when-cross-origin`** instead of `same-origin`, for OSM tiles.
4. **Vendored third-party JavaScript** in `static/vendor/` (Leaflet, marker cluster), exempt from the ES-module rules
   and from `node --check`'s glob.
5. **The server fetches arbitrary URLs** (spec 7.12), behind the SSRF guard. Matinee only calls fixed APIs.
