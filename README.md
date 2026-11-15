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
`STUBS_API_KEY`, `STUBS_READ_KEY`, `SETLISTFM_API_KEY`, `GEOCODER_URL`, `MUSICBRAINZ_URL`, `TILE_URL`.

Scripts use `STUBS_API_KEY` (writes) or `STUBS_READ_KEY` (reads) as `X-API-Key: <key>` or
`Authorization: Bearer <key>`.

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
