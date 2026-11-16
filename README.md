# Stubs

A personal concert history: every show you've been to, who was on the bill, where it was, and what proves it.
Flask + SQLite, a phone-first single page, a map with a year strip, and an evidence trail for every fact.

- Import a CSV of shows, review what it will do, approve. Re-importing never duplicates a show or overwrites a hand
  edit.
- Edit shows, lineups, artists and venues by hand; merge duplicates.
- Browse by show, artist ("every time I've seen X", in any role), venue, year and city; stats.
- Sources and confidence on every show and lineup entry, with a list of facts still to confirm.
- Upcoming shows, and a "Did you go?" list once they pass.
- Posters, photos and ticket stubs; a live setlist.fm panel.
- A Claude connector (MCP): ask "every time I've seen X" or "how many times have I heard Y" from any Claude app, and
  let Claude send shows it finds in emails to the import review screen.
- Concert watch: upcoming concerts by bands you've seen (or want to), near you, pushed to your phone.

Friends with the link see the attended shows; upcoming shows, notes, sources, companions, ratings and imports are for
editors only.

Design: `docs/superpowers/specs/2026-11-13-stubs-design.md`. Plan: `docs/superpowers/plans/2026-11-13-stubs.md`.

## Install (Alpine Linux, OpenRC)

```sh
sh installer/build.sh            # on a checkout: writes dist/setup-stubs.sh
sh setup-stubs.sh                # on the host, as root: install or upgrade (re-runnable)
vim /etc/conf.d/stubs            # set CF_TEAM_DOMAIN, CF_AUD, ADMIN_EMAILS
rc-service stubs restart
```

The service listens on `127.0.0.1:8773` only (`STUBS_PORT=<port> sh setup-stubs.sh` for another port on a first
install). Route a Cloudflare Tunnel hostname such as `concerts.example.com` to it, and put a Cloudflare Access
application on `concerts.example.com/admin` that lets in the editors' emails; list the same emails in `ADMIN_EMAILS`.
Editors use `https://concerts.example.com/admin`; everyone else uses `/`.

Upgrades keep `/var/lib/stubs` (database and images) and `/etc/conf.d/stubs`, and only add settings that are
missing. A nightly backup goes to `/var/backups/stubs` (14 days). `sh setup-stubs.sh --uninstall` removes the app and
service and keeps the data, config and backups.

## Settings

Every setting, with its default, is in `.env.example` (and in `/etc/conf.d/stubs` on the host): `PORT`, `DATA_DIR`,
`CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS`, `LAN_EDIT`, `LAN_HOSTS`, `PUBLIC_VIEW`, `PUBLIC_POSTERS`,
`STUBS_API_KEY`, `STUBS_READ_KEY`, `SETLISTFM_API_KEY`, `GEOCODER_URL`, `MUSICBRAINZ_URL`, `TILE_URL`,
`TICKETMASTER_API_KEY`, `LASTFM_API_KEY`, `LASTFM_USER`, `WATCH_NOTIFY_URL`, `WATCH_NOTIFY_FORMAT`.
The MCP service's: `STUBS_URL`, `STUBS_MCP_WRITES`, `MCP_HTTP_BIND`, `MCP_HTTP_PORT`, `MCP_HTTP_PATH`,
`MCP_PUBLIC_HOSTS`, `CF_ACCESS_AUD`, `MCP_ALLOWED_EMAILS` (`docs/mcp-setup.md`).

Scripts use `STUBS_API_KEY` (writes) or `STUBS_READ_KEY` (reads) as `X-API-Key: <key>` or
`Authorization: Bearer <key>`.

## Claude connector (MCP)

`mcp_server.py` gives Claude read tools over the history (`times_seen`, `search_shows`, `get_show`, `song_count`,
`never_heard` and more) and, with `STUBS_MCP_WRITES=import`, `import_shows`, which sends shows to the import review
screen; nothing is applied until you approve the batch there, and companions never leave the app. The installer runs
it as the `stubs-mcp` service on `127.0.0.1:8774` (its own venv from `requirements-mcp.txt`), started once
`MCP_PUBLIC_HOSTS`, `CF_ACCESS_AUD` and `STUBS_READ_KEY` are set; put it behind a tunnel route such as
`https://mcp.example.com/stubs/mcp` with Cloudflare Access. Setup, settings and rollback: `docs/mcp-setup.md`.

## Concert watch

Editors only. `Watch` in the header (`#/admin/watch`) lists the bands you watch, suggests more from your shows and
from Last.fm, and holds your watch areas (a name, a point and a radius in km: type the coordinates of a spot near
you, not your front door). New concerts near an area, or anywhere for a band marked "Anywhere", show on the admin home
as "New near you", with Add as upcoming and Dismiss, and are pushed to your phone once.

1. **Ticketmaster** (where the concerts come from): sign up at https://developer.ticketmaster.com, open My Apps and
   copy the Consumer Key. Put it in `TICKETMASTER_API_KEY`. The free key allows 5,000 calls a day; the hourly check
   uses about 1,000.
2. **Last.fm** (optional, suggestions only): create an API account at https://www.last.fm/api/account/create and put
   the API key in `LASTFM_API_KEY` and your Last.fm user name in `LASTFM_USER`. Stubs reads your public top artists;
   it never logs in.
3. **Where to push** (optional; without it the admin card still shows them):
   - ntfy: pick a long, unguessable topic (anyone who knows it can read it), subscribe to it in the ntfy app, and set
     `WATCH_NOTIFY_URL='https://ntfy.sh/stubs-k3v9q2x7'` (or your own server, `https://ntfy.example.com/stubs-k3v9q2x7`)
     with `WATCH_NOTIFY_FORMAT='ntfy'`.
   - Home Assistant: make an automation triggered by a webhook (ID e.g. `stubs-k3v9q2x7`) whose action calls a notify
     service with `{{ trigger.json.title }}` and `{{ trigger.json.message }}`, then set
     `WATCH_NOTIFY_URL='https://ha.example.com/api/webhook/stubs-k3v9q2x7'` and `WATCH_NOTIFY_FORMAT='json'`
     (`items` carries each concert's artist, date, venue, city, region, country, link and source).
4. `STUBS_API_KEY` must be set: the hourly job (`/etc/periodic/hourly/stubs-watch`) uses it to ask the app to check.
   `vim /etc/conf.d/stubs`, then `rc-service stubs restart`. The job logs counts to `/var/log/stubs-watch.log`.

The keys and the push URL are secrets: they never appear in a page, an API response or a log. Concerts you've
already got a ticket for (an upcoming show at the same venue that night) show as "You have tickets" and are never
pushed. Claude can file concerts it finds in your mail (tour announcements, presales) through
`POST /admin/api/watch/sightings` with the write key.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env              # optional
.venv/bin/python app.py           # http://127.0.0.1:8773 (editing works from localhost while LAN_EDIT=1)
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
```

The full check list is in `CLAUDE.md`. The browser smoke tests (`tests/test_*_ui.py`) need
`pip install playwright && python -m playwright install chromium`.
The MCP tests (`tests/test_mcp_*.py`) need `.venv/bin/pip install -r requirements-mcp.txt httpx`.
