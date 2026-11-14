# Stubs: design

Status: draft for owner approval, 2026-11-13.
Repo: `SDNick484/stubs` (public). Served at the owner's `concerts.` subdomain; this document says `concerts.example.com`.

## 1. Intent

Stubs is a personal concert history: every show the owner has been to, who was on the bill, where it was, and what
proves it. It starts from a CSV that Claude assembled from ticket emails and an old Last.fm history. That CSV is a
subset: more shows keep turning up in other records, so new finds must come in without duplicating anything, and the
owner must be able to add and fix shows by hand.

What it is for, in the owner's words and choices:

- Looking back: "every time I've seen X", across headliner, co-headliner, opener and festival slots.
- Seeing the history over place and time: a map with a year strip, in the spirit of pearljam.com's tour history page
  (map, years, step from show to show).
- Keeping an honest evidence trail: where each fact came from, and which facts are still soft ("likely") and need
  confirming later.
- Upcoming shows, so the list is one place for past and future.
- A visual record: posters (some artists, e.g. Pearl Jam, make one per show), photos, ticket stubs.

Audience: the owner and his wife edit; occasional friends browse a link.

## 2. Goals, non-goals, anti-goals

Goals (v1):

1. Upload the CSV, review what it will do, approve, and see the history. (First milestone.)
2. Re-uploading the same or a longer CSV never duplicates a show and never overwrites a hand edit.
3. Shows, lineups, artists and venues are editable by hand; duplicate artists and venues can be merged.
4. Artist pages count every role. Festival days list only the acts the owner actually watched.
5. Map + year strip main view; show, artist, venue, year and city views; stats.
6. Sources and confidence on shows and on each lineup entry; a to-confirm list.
7. Upcoming shows and a "Did you go?" list once they pass.
8. Companions (who went), editor-only.
9. Images (poster, photo, stub) and ratings.
10. A live setlist.fm panel on a show page.
11. Shows found elsewhere come in through an import API (for Claude or a script) and through "fetch from a URL".

Non-goals for v1, in the order they come next:

1. Stored setlists and song counts ("how many times have I heard X", "which of this band's songs have I never
   heard"), entered by paste from band and fan-club sites or typed; plus an artist song catalog.
2. MusicBrainz links on artists (stable IDs, better merging).
3. An MCP server whose first tool wraps the import API, then read tools ("every time I've seen X").

Not planned: ticket prices or spending; the app logging into Gmail or Last.fm itself; Matinee's Ask feature;
site-specific scrapers (the generic URL reader comes first).

Anti-goals (a failure even if it works):

- A missed, canceled or not-confirmed show counted anywhere.
- A festival lineup filled in automatically, so "times I've seen X" counts acts the owner never watched.
- An import that silently changes something the owner fixed by hand.
- Upcoming shows, companions, notes (which name third parties) or the evidence trail visible to a friend with the link.
- setlist.fm song data stored in the database (their API terms forbid keeping it beyond a short cache).
- Any real concert data, names, or the owner's hostname committed to the public repo.

## 3. Constraints

Follow `SDNick484/matinee`'s CLAUDE.md unless listed under Departures (section 12):

- Flask + SQLite, runtime Python dependencies only Alpine `py3-*` packages (`py3-flask py3-gunicorn py3-jwt
  py3-cryptography`, plus `sqlite`). Python 3.10–3.13 and an Alpine container job in CI.
- `app.py` holds the Flask object, auth and every route; route-free logic in sibling modules that take a
  `sqlite3.Connection` and never import `app`.
- Schema changes through an idempotent `migrate()`; bump `SCHEMA_VERSION`; no manual DB step on the host.
- Native ES modules in `static/js/`, no framework or build step, `static/js/package.json` = `{"type": "module"}`.
- pytest with recorded fixtures, no live network. External-API features test a miss, an HTTP error and a timeout.
- Alpine + OpenRC installer built by `installer/build.sh` into `dist/setup-stubs.sh`; re-runnable; `add_setting`
  only adds missing keys; `/var/lib/stubs` and `/etc/conf.d/stubs` survive upgrades; nightly backup in `ops/`.
- Hygiene test: no personal hostnames, LAN addresses, real names or real concert data in tracked files. Docs and
  tests use `concerts.example.com`, `192.168.1.100`, invented people (Alex, Sam, Jordan) and invented acts.
- Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`. OpenRC commands, `vim`.
- Default port 8773 (8768–8772 are taken on the host by sibling apps); gunicorn binds 127.0.0.1.

## 4. Approaches considered

**Setlists.** (A, chosen) The show page shows setlist.fm live with its required attribution link; stored setlists are
the owner's own, entered later (post-v1). (B) Ask setlist.fm for permission to store their data: an owner email; if
granted it only makes A's later paste step a one-click copy, so nothing is designed around it. (C) Compute song counts
live from setlist.fm on each question: rejected (rate limits, silent drift, leans on "short cache" wording).

**After the first import, which copy is the master.** (Chosen) The app. The CSV is a likely one-time load; later
CSVs and API batches are additive finds matched against what exists, never overwriting. (Rejected) The CSV as master
with an exported `id` column for round-trips: the owner expects to work in the app.

**Map.** (A, chosen) Leaflet + a marker-cluster plugin, vendored into `static/vendor/`, with OpenStreetMap raster
tiles: pans and zooms, so the Bay Area cluster and the far-away outliers both read well. Costs: tiles load from
OSM's servers in the viewer's browser, the page needs OSM attribution, and the CSP and referrer policy must allow
the tiles. (B) A self-drawn SVG of North America with a Bay Area inset: no outside calls, but no zoom and redraws as
travel grows. (C) Self-hosted vector tiles (MapLibre + a downloaded map file): zoomable and self-contained, but a
100 MB+ file and a heavy library for little gain over A.

**Access.** Matinee's model (chosen over Gift Exchange's shared password, because companions and upcoming shows need
an editor/public split).

## 5. Data model

All dates are ISO strings; timestamps are `created_at`/`updated_at` text as in Matinee. Names are matched through a
normalized form `norm()` (section 7.2). Schema v1:

- `meta(k, v)`: `schema_version`.
- `artist(id, name, norm, created_at, updated_at)`, `UNIQUE(norm)`.
- `artist_alias(norm PRIMARY KEY, artist_id)`: other spellings that resolve to an artist (filled by merges and by
  the owner); import matching checks `artist.norm` then `artist_alias.norm`.
- `venue(id, name, city, region, country, lat, lon, geocode_source, created_at, updated_at)`: `name` is the current
  name; `region` is a state or province code; `country` an ISO 3166 alpha-2 code.
- `venue_name(venue_id, name, norm)`, `UNIQUE(norm, venue_id)`: every name the venue has gone by, including the
  current one. A renamed amphitheatre is one venue for stats.
- `run(id, name, created_at)`: an optional label grouping days, e.g. a three-day festival or a two-night stand.
- `show(id, status, date, date_precision, sort_date, billing, venue_id, venue_label, run_id, tour, notes, rating,
  confidence, confidence_note, created_at, updated_at, version)`:
  - `status` ∈ `attended`, `upcoming`. There is no missed or canceled status: those never become shows.
  - `date` is `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; `date_precision` ∈ `year`, `month`, `day`; `sort_date` is the
    first day of the period (`2027` → `2027-01-01`).
  - `billing` is how the show is listed: a headliner, co-headliners ("Act A & Act B") or an event name.
  - `venue_label` is the venue name as on that night's ticket (one of the venue's names).
  - `notes` free text; `rating` 1–5 or NULL; `confidence` ∈ `confirmed`, `likely`; `version` for stale-save checks.
- `lineup(id, show_id, artist_id, role, position, rating, confidence, confidence_note)`,
  `UNIQUE(show_id, artist_id)`; `role` ∈ `headliner`, `coheadliner`, `opener`, `performer` (festival or benefit
  act the owner watched).
- `source(id, show_id, lineup_id, label, url, created_at)`: the evidence trail. `lineup_id` NULL means the source
  backs the show itself. `label` is free text ("Ticketmaster", "Last.fm", "setlist.fm", "Ten Club", "stub scan",
  "CSV import 2026-11-14").
- `person(id, name, created_at)` and `show_person(show_id, person_id)`: companions.
- `image(id, show_id, kind, file, caption, source_url, position, created_at)`; `kind` ∈ `poster`, `photo`, `stub`.
- `setlist_link(show_id PRIMARY KEY, setlistfm_id, url)`: only the setlist.fm identifier and link the owner picked;
  never song data.
- `import_batch(id, origin, label, status, created_at, applied_at)`: `origin` ∈ `csv`, `api`; `status` ∈ `pending`,
  `applied`, `discarded`.
- `import_row(id, batch_id, row_no, raw, parsed, verdict, match_show_id, reason, include)`: `raw` is the row as
  received (JSON), `parsed` the normalized proposal (JSON, editable in review), `verdict` ∈ `new`, `have`, `maybe`,
  `skip`.

Counts and "times I've seen X" use attended shows only. An artist's count is the number of attended shows with that
artist in the lineup, in any role.

## 6. Who sees what

Matinee's access code, renamed (`STUBS_API_KEY`, `STUBS_READ_KEY`):

- Editors: Cloudflare Access JWT (`CF_TEAM_DOMAIN`, `CF_AUD`) whose email is in `ADMIN_EMAILS` (the owner and his
  wife); `LAN_EDIT` on the LAN under `LAN_HOSTS`; the write key and read key for scripts. Writes live under
  `/admin/api/` with `@editor_required`, which refuses cross-site browser requests.
- Public: `/` and `/api/` when `PUBLIC_VIEW=1` (default, as Matinee). Every response carries
  `X-Robots-Tag: noindex, nofollow`.

Public: attended shows (date, billing, venue as on the ticket, city, run, tour), lineups with roles, artist, venue,
year and city pages, stats, the map, photos, the setlist.fm panel.

Editor-only: upcoming shows (and their effect on any page), notes, sources, confidence and the to-confirm list,
companions, ratings, posters and stubs (posters become public when `PUBLIC_POSTERS=1`), imports, "Did you go?".

`tests/test_privacy.py` walks every `/api/` rule in `app.url_map` with seeded editor-only data (an upcoming show, a
note, a companion, a source, a likely flag, a rating, a poster) and asserts none of it appears.

## 7. Behaviour

### 7.1 Shows, lineups, runs

- A show is one night at one venue. A multi-day festival the owner attended on two days is two shows with the same
  `run` ("Harbor Days 2009"); each day's lineup is the acts he watched there, entered by hand.
- The billing is display text. The lineup is the truth for counts: the import proposes it, the owner corrects it.
- Previous/next on a show page steps by `sort_date`, then `id`.

### 7.2 Names

`norm(s)`: Unicode NFKC, curly quotes and apostrophes to straight, `&`/`and` kept as written, case-folded,
whitespace collapsed, trimmed. Venue matching also drops a leading "the " and must agree on city. So `Jack’s` and
`Jack's` match, and "Rio Theatre" / "The Rio Theatre" in the same city match.

### 7.3 Import (CSV and API share one pipeline)

1. Parse into proposals. CSV columns (the owner's current file): `date, artist, venue, city, status,
   ticket_source, notes, openers, openers_source, openers_note`. Unknown extra columns are kept in `raw` and ignored.
   - `date`: `YYYY-MM-DD`, `YYYY-MM` or `YYYY`; anything else → `skip` with a reason.
   - `artist` → `billing`. Proposed lineup:
     - `"Event (A, B, C)"`: billing "Event", performers A, B, C.
     - `" + "` with spaces splits co-headliners ("Act A + Act B & Friends" → "Act A", "Act B & Friends").
     - `&` never splits ("Mumford & Sons"-style names are common); a billing containing `" & "` gets the review
       flag "check acts".
     - Otherwise one headliner named as the billing.
   - `city`: `"City, XX"`; `XX` a US state code → US, a Canadian province code → CA; otherwise the review asks.
   - `status`: `attended` → attended; `upcoming` or `rescheduled` → upcoming; `missed`, `canceled` → `skip`
     ("not attended"). Any other value → `skip` with a reason.
   - `ticket_source`: split on `" + "` and `" / "` → one show source each. Every imported show also gets the source
     "CSV import <date>" (or the API batch label).
   - `notes` → show notes, verbatim.
   - `openers`: split on `;` (a comma can be part of a name) → opener lineup entries in order. `openers_source` → a
     source on each. A non-empty `openers_note` → each opener `likely` with the note as `confidence_note`.
   - "Date approximate" in notes → show `likely`. (The owner can mark anything else likely in review.)
2. Match each proposal (attended and upcoming alike) against existing shows:
   - `have`: same venue (7.2) and `sort_date` within ±1 day and the billing norm or any lineup artist matches.
   - `maybe`: same city, ±1 day, a shared artist but a different or unknown venue; or `year`/`month` precision with
     the same venue and a shared artist in that period.
   - Within one batch, a second row matching an earlier row in the same batch is `have` against it.
   - Otherwise `new`.
3. Review screen (editor): rows grouped by verdict, each showing the proposal and, for `have`/`maybe`, the existing
   show. `new` and `maybe` rows can be edited (lineup acts and roles, venue pick, likely flag) and ticked or unticked;
   `have` rows show a "differs from what you have" note listing fields that differ, and are never applied. Batches
   wait as `pending` until approved or discarded.
4. Apply (one transaction): create venues, artists and runs as needed, then shows, lineups and sources for ticked
   rows. A `maybe` row that is ticked becomes a new show. Nothing existing is updated.

Re-importing the same file gives zero `new` rows. API batches: `POST /admin/api/imports` (write key or editor) with
`{"label": str, "shows": [proposal...]}` in the same proposal shape the CSV parser produces; returns the batch id and
counts; the batch then appears on the review screen.

### 7.4 Confidence and the to-confirm list

`likely` on a show or lineup entry draws a small marker beside it (editors only). The to-confirm list (editor) lists
every likely show and lineup entry with its note and sources, oldest first; "Confirm" sets `confirmed` and asks for an
optional source (e.g. "stub scan"). Likely facts still count in stats.

### 7.5 Upcoming and "Did you go?"

Upcoming shows appear (to editors) at the top of the timeline and on artist and venue pages, marked as upcoming, and
never in counts or on the map. Once an upcoming show's period has ended (the day after a `day` date; after the month
or year for coarser dates), it moves to the "Did you go?" list on the editor home: Yes → `attended`; No → the show is
deleted with its lineup, sources and images (after an inline confirm).

### 7.6 Venues and the map

- A venue's coordinates are looked up from OpenStreetMap Nominatim (`GEOCODER_URL`, `User-Agent` naming the app and
  repo, at most one request per second) by an editor action on the venue, and by "Locate all missing" which works
  through venues without coordinates at that rate and reports progress. Coordinates are editable; a venue without
  them is listed as "not on the map".
- Map: Leaflet with OSM tiles and attribution; one marker per venue, clustered at low zoom; a marker opens the
  venue's shows (filtered to the selected year). The year strip under the map selects all years or one year; the list
  beside or under the map shows that selection's shows, newest first. Phone first (412 px).
- Referrer policy is `strict-origin-when-cross-origin` so tile requests carry the referrer OSM's tile policy expects.

### 7.7 Views

- Home `#/`: map, year strip, show list. Editor home adds "Did you go?", the to-confirm count, and pending imports.
- Show `#/show/<id>`: billing, date (precision shown: "2027", "Jul 2025"), venue as on the ticket (linking to the
  venue), city, run, tour, lineup with roles, poster as hero, photos, setlist.fm panel, previous/next. Editors also
  see notes, sources, confidence, companions, ratings, stubs, and edit controls.
- Artist `#/artist/<id>`: times seen, first and last, by role, shows list.
- Venue `#/venue/<id>`: names it has had, city, times, shows.
- Year `#/year/<yyyy>` and city `#/city/<country>/<region>/<city>`.
- Stats `#/stats`: shows per year, top artists (any role), top venues, top cities, totals.
- Editor: show edit/new, import + review, to-confirm, people (companions), merge artists, merge venues.

### 7.8 Editing

Create, edit and delete shows; edit lineups (add, remove, reorder, role, likely); add and remove sources; set runs;
tag companions (people created inline). Saves send the show's `version`; a stale save is 409 and keeps the form.
Merging artists or venues moves every reference to the kept record and records the merged name as an alias or
former name.

### 7.9 Images

Upload JPEG, PNG or WebP (sniffed by magic bytes as Matinee does), at most 8 MB, stored flat in
`DATA_DIR/images/` under a content-hashed name; no resizing. A show has at most one poster; photos and stubs are
ordered. Deleting a show deletes its files.

### 7.10 Ratings

Show rating 1–5 and an optional 1–5 per lineup entry. Editor-only. Stats do not use them in v1.

### 7.11 setlist.fm panel

With `SETLISTFM_API_KEY` set, an editor can "Find on setlist.fm": the app searches by headliner and date and offers
the matches; the editor picks one and the app stores only its id and URL (`setlist_link`). Viewing the show fetches
the setlist live (in-memory cache, at most one hour) and shows it with setlist.fm's attribution link (followable, no
`nofollow`). No key, a miss, an error or a timeout show "Setlist unavailable" with the link if one is stored.

### 7.12 Fetch from a URL

An editor pastes a URL on a show. The server fetches it (http/https only; refuses hosts that resolve to loopback,
private, link-local or reserved addresses, re-checking every redirect; 5 s timeout; 2 MB cap) and reads `og:image`,
`og:title`, and schema.org `Event`/`MusicEvent` JSON-LD (`startDate`, `location`, `performer`). It returns
suggestions: "use this image as the poster", "add these performers", "date says …". Each applied suggestion records
the URL as its source. Nothing is applied without a click.

## 8. Settings

`.env.example` and the installer's `add_setting` list carry every setting: `PORT` (8773), `DATA_DIR`,
`CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS`, `LAN_EDIT`, `LAN_HOSTS`, `PUBLIC_VIEW`, `PUBLIC_POSTERS` (0),
`STUBS_API_KEY`, `STUBS_READ_KEY`, `SETLISTFM_API_KEY`, `GEOCODER_URL`
(`https://nominatim.openstreetmap.org/search`), `TILE_URL` (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`).

## 9. Testing

- pytest, one test file per module, recorded fixtures under `tests/fixtures/` (setlist.fm, Nominatim, fetched
  pages). Every external call tests a hit, a miss, an HTTP error and a timeout.
- An invented CSV fixture reproduces every quirk of the real file with invented acts and dates: a year-only upcoming
  row, a `rescheduled` row, `missed` and `canceled` rows, `" + "` and `" & "` billings, an `Event (A, B, C)` billing,
  a curly-apostrophe opener, two venues that differ only by "The", a venue under two names, a `ticket_source` with two
  sources, an `openers_note`, "Date approximate" in notes, and a note naming an invented person.
- Re-importing that fixture yields zero `new`.
- `tests/test_privacy.py` as in section 6; `tests/test_hygiene.py` as in Matinee, plus: no `*.csv` tracked outside
  `tests/fixtures/`.
- Frontend: `node --check` on `static/js/*.js`, `node --test` for DOM-free helpers, a Playwright UI smoke job at
  412 px (home map + list, show page, import review), which fails on any skip.

## 10. What the owner does

Create the GitHub repo; confirm port 8773 is free; DNS and the Cloudflare tunnel route for the `concerts.`
subdomain; a Cloudflare Access application for `/admin` with his and his wife's emails; run the installer on the
Alpine host and fill `/etc/conf.d/stubs`; get a setlist.fm API key; upload the real CSV and approve the review.

## 11. Left to the builder

Module boundaries and names; the review screen's exact layout; the year strip and list styling (follow Matinee's
look); Leaflet and plugin versions (pin them; record the version and licence in `static/vendor/README.md`); how the
classic-script Leaflet build is loaded alongside the ES module graph; the image file naming scheme.

## 12. Departures from Matinee's conventions

1. **No MCP server in v1** (Matinee rule 6). It is the third item after v1; the import API is its seam.
2. **A strict CSP**, taken from Gift Exchange: `default-src 'self'; script-src 'self'; style-src 'self';
   img-src 'self' data: <tile host>; connect-src 'self'; object-src 'none'; base-uri 'none';
   frame-ancestors 'none'`. Matinee has none for scripts. This brings Gift Exchange's escaping rules (`esc()` on every
   interpolation, no inline handlers or `style=` in markup) and its scan test.
3. **Referrer policy `strict-origin-when-cross-origin`** instead of `same-origin`, for OSM tiles.
4. **Vendored third-party JavaScript** in `static/vendor/` (Leaflet, marker cluster), exempt from the ES-module
   rules and from `node --check`'s glob.
5. **The server fetches arbitrary URLs** (7.12), behind the SSRF guard. Matinee only calls fixed APIs.
