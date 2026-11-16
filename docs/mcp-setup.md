# The Stubs MCP server

Stubs has a Model Context Protocol server (`mcp_server.py`, post-v1 #46) so Claude can answer from the concert
history ("every time I've seen The Paper Lanterns", "what did I see in 2009", "how many times have I heard Lantern
Light") and, when you allow it, send shows it finds elsewhere (ticket emails, receipts, photos) to the import review
screen. It follows Matinee's MCP server: the same shared `mcp_remote.py`, the same Cloudflare Access setup, the same
kind of write level.

What it never does:

- **Apply, edit or delete anything.** There is no such tool. A batch Claude sends waits in `#/admin/import/<id>` like a
  CSV upload, and you apply it there (spec 2: no import silently changes anything).
- **Show companions.** No tool reads who went with you; that stays in the app.
- **Open the database.** It reads Stubs through `/admin/api` over HTTP, as the `stubs-mcp` user, which has no access
  to `/var/lib/stubs`.

## 1. Tools

| Tool | Answers | Stubs route(s) |
|------|---------|----------------|
| `find_artist(name)` | Artists whose name or an alias contains `name`, each with `id`, `times` (attended shows, any role), `first`, `last`, `by_role` and `upcoming` | `GET /admin/api/artists?q=`, `/admin/api/artists/<id>` |
| `times_seen(artist \| artist_id)` | "Every time I've seen X": each attended show, newest first, with date, venue, city and role; upcoming shows apart and never counted | `GET /admin/api/artists/<id>` |
| `search_shows(year?, artist?, venue?, city?, artist_id?, venue_id?, limit=100)` | "What did I see in 2009", at a venue, in a city | `GET /admin/api/shows?year=&artist_id=&venue_id=&city=` |
| `get_show(show_id)` | One show in full: lineup with roles, notes, sources, confidence, stored setlists | `GET /admin/api/shows/<id>` |
| `get_stats()` | Totals, shows per year, top artists, venues, cities and songs | `GET /api/stats` |
| `upcoming_shows()` | Shows you have tickets for; `passed: true` once the date has gone by and Stubs is waiting for "Did you go?" | `GET /admin/api/upcoming`, `/admin/api/did-you-go` |
| `to_confirm()` | Facts still marked "likely", with their notes and sources | `GET /admin/api/to-confirm` |
| `songs_heard(artist \| artist_id)` | Every song heard live, most played first (plays, nights, first, last) | `GET /admin/api/artists/<id>/songs` |
| `song_count(artist, song)` | "How many times have I heard X play Y": plays, nights, first, last and each night | `GET /admin/api/artists/<id>/songs?q=`, `/admin/api/songs/<id>` |
| `never_heard(artist \| artist_id)` | Catalog songs never heard live | `GET /admin/api/artists/<id>/songs` |
| `import_shows(label, shows)` | *Write level `import` only.* Sends a batch of Proposals for review; returns the batch id, its counts (new, have, maybe, skip) and `review_link` | `POST /admin/api/imports` |
| `list_imports()` | *Write level `import` only.* Batches waiting for review, each with `review_link` | `GET /admin/api/imports` |

A name that matches several artists (or venues) is answered with the list and their ids, and Claude asks which one;
an exact name wins over partial matches, and an alias counts as the artist's name. Song counts come only from setlists
stored in Stubs (#43): a night without a stored setlist adds nothing, and a count of zero says so.

`import_shows` takes the plan's Proposal shape (one show per night; a festival is one show per day with only the acts
you actually watched, as `performer`; `confidence: "likely"` and a `confidence_note` when Claude is unsure; a source
for every fact). Its tool description carries the rules and an invented example, so Claude sees them every time. A
bad show is refused with the API's own message, for example `Stubs refused the import, and stored nothing: Row 2:
date is required.`, and nothing is stored.

`review_link` is a path, `/admin#/admin/import/12`: open it on your Stubs site
(`https://concerts.example.com/admin#/admin/import/12`), or use the "Imports waiting" card on `/admin`.

## 2. Keys and write levels

`STUBS_MCP_WRITES` picks the write level. The service holds exactly one Stubs key:

| Level | Tools | Key the service holds |
|-------|-------|-----------------------|
| `none` (default; also any unknown value, which is logged) | the ten read tools | `STUBS_READ_KEY` (GET-only on `/admin/api`) |
| `import` | the ten, plus `import_shows` and `list_imports` | `STUBS_API_KEY` |

At `none` the init script keeps `STUBS_API_KEY` out of the service's environment, and the service refuses to start if
it is there anyway (`STUBS_API_KEY must not be in this service's environment at write level none`). It also refuses
to start when the key for its level is empty. At `import` the service holds the write key, which can do everything
under `/admin/api`: the tool list limits what Claude can ask for, not what a compromised process could do.

The read key sees what an editor sees (upcoming shows, notes, sources, confidence), which is why the connector sits
behind Cloudflare Access with an email allow-list.

## 3. Remote access from the Claude app (`stubs-mcp`)

```
Claude app -> Anthropic cloud -> https://mcp.example.com/stubs/mcp
  -> Cloudflare Access (sign-in, Managed OAuth) -> tunnel route ^/stubs/ -> http://127.0.0.1:8774  (stubs-mcp)
  -> checks the Access JWT again                -> http://127.0.0.1:8773/admin/api/...  (Stubs, with STUBS_READ_KEY;
                                                                                         STUBS_API_KEY at import)
```

The installer sets up the `stubs-mcp` user (its own group, no home, no shell), a venv at `/opt/stubs-mcp/venv` with
`requirements-mcp.txt` (the `mcp` package isn't an Alpine `py3-*` package; the Flask app stays apk-only), and
`/etc/init.d/stubs-mcp`. If pip can't reach PyPI it prints a warning, skips `stubs-mcp` and installs Stubs as usual.
It starts `stubs-mcp`, adds it to the runlevel and adds `Stubs MCP|http://127.0.0.1:8774/healthz|stubs-mcp` to
`HEALTH_CHECKS` only once it is configured and answering.

### 3.1 Settings (`/etc/conf.d/stubs`)

The installer adds any missing key and never overwrites one you edited. The file is `chmod 600 root:root`; the init
script reads it as root and exports only what the service needs.

| Setting | Default | Meaning |
|---------|---------|---------|
| `STUBS_MCP_WRITES` | `none` | Write level (section 2). Case and surrounding spaces don't matter. |
| `STUBS_URL` | *(empty)* | Where `stubs-mcp` reaches the app; empty means `http://127.0.0.1:<PORT>` (`http://127.0.0.1:8773`). |
| `MCP_HTTP_BIND` | `127.0.0.1` | Where `stubs-mcp` listens. The tunnel route points here, as it does for the app's loopback port. Any other address is refused without the Access settings. |
| `MCP_HTTP_PORT` | `8774` | Port. Check it is free first (`netstat -ltn \| grep 8774`); the installer won't start `stubs-mcp` on a port in use. |
| `MCP_HTTP_PATH` | `/stubs/mcp` | Endpoint path. The tunnel does not strip the prefix. |
| `MCP_PUBLIC_HOSTS` | *(required)* | `mcp.example.com`. Without it requests through the tunnel get HTTP 421. |
| `CF_TEAM_DOMAIN` | *(already set for `/admin`)* | Access team domain, shared with the app. |
| `CF_ACCESS_AUD` | *(required)* | AUD tag of the Access application for `mcp.example.com`. **Not** `CF_AUD` (the `/admin` application). |
| `MCP_ALLOWED_EMAILS` | *(empty)* | Second gate: only these Access emails may call tools. A token with no email is refused when this is set. Set it. |
| `STUBS_READ_KEY` | *(empty)* | Needed at `none`. Make one with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`, keep it different from `STUBS_API_KEY`, and restart `stubs` too (the app checks it). |
| `STUBS_API_KEY` | *(empty)* | Needed at `import` only. |

### 3.2 Turn it on

```sh
netstat -ltn | grep 8774           # nothing: the port is free (else pick another MCP_HTTP_PORT)
vim /etc/conf.d/stubs              # STUBS_READ_KEY, MCP_PUBLIC_HOSTS, CF_ACCESS_AUD, MCP_ALLOWED_EMAILS
rc-service stubs restart           # the app picks up a new STUBS_READ_KEY
rc-service stubs-mcp restart
rc-update add stubs-mcp default
tail -n 20 /var/log/stubs-mcp.log  # "stubs-mcp starting: /stubs/mcp on 127.0.0.1:8774 (read-only)"
wget -qO- http://127.0.0.1:8774/healthz   # {"status": "ok"} while Stubs itself is healthy
```

Or re-run the installer after editing the config: it starts the service and adds the health check.

### 3.3 Tunnel and Cloudflare

In the tunnel's published application routes add:

| Field | Value |
|-------|-------|
| Hostname | `mcp.example.com` |
| Path | `^/stubs/` |
| Service | `http://127.0.0.1:8774` |
| HTTP Host header override | unset |

The Cloudflare Access application for `mcp.example.com` is the one the other MCP servers use (Managed OAuth on, path
empty); if there is none yet, create a self-hosted application on `mcp.example.com` with Managed OAuth on and a policy
that lets in your email. Check that no other Access application or Bypass policy covers a path under `/stubs`, and
copy the application's AUD tag into `CF_ACCESS_AUD`.

Check that the endpoint refuses anonymous calls (from any machine):

```sh
curl -si -X POST https://mcp.example.com/stubs/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
# expect: HTTP 401 with a www-authenticate header from Access. Any 200 is a failure.
```

Straight to the origin (no Access header) the service answers `403 {"error": "forbidden"}`.

### 3.4 Connect Claude

In Claude, open **Customize > Connectors > Add custom connector**, enter `https://mcp.example.com/stubs/mcp`, choose
**Sign in now**, and sign in through Cloudflare Access. For Claude Code:
`claude mcp add --transport http stubs https://mcp.example.com/stubs/mcp`.

Ask "Every time I've seen The Paper Lanterns?" and Claude calls `times_seen`.

### 3.5 Letting Claude send shows for review

```sh
vim /etc/conf.d/stubs              # STUBS_MCP_WRITES=import and a non-empty STUBS_API_KEY
rc-service stubs restart           # only if STUBS_API_KEY is new
rc-service stubs-mcp restart
```

Then "Here are my ticket emails from 2009 to 2012: add the shows" ends with `import_shows`, and Claude gives you the
review link. Nothing is in the history until you apply the batch.

### 3.6 Rollback

| Goal | Action |
|------|--------|
| Stop remote access now | `rc-service stubs-mcp stop` and `rc-update del stubs-mcp`. The app is unaffected. |
| Remove public exposure | Delete the `^/stubs/` tunnel route and the connector in Claude. |
| Stop imports, keep reads | `STUBS_MCP_WRITES=none`, `rc-service stubs-mcp restart`. |
| A key leaked | A new value in `/etc/conf.d/stubs`, then `rc-service stubs restart && rc-service stubs-mcp restart`. |

## 4. Over stdio (Claude Code or Desktop)

`mcp_server.py` also runs over stdio, with the same rules (a key for its level, and never the write key at `none`).
It needs the packages in `requirements-mcp.txt` and a Stubs it can reach with a key. Stubs listens on loopback only
and `/admin` on its public hostname sits behind Access, so run it beside the app: on the Stubs host, or with a
development copy (`python3 app.py`):

```json
{
  "mcpServers": {
    "stubs": {
      "command": "/path/to/stubs/.venv/bin/python",
      "args": ["/path/to/stubs/mcp_server.py"],
      "env": {"STUBS_URL": "http://127.0.0.1:8773", "STUBS_READ_KEY": "your-read-key"}
    }
  }
}
```

Add `"STUBS_MCP_WRITES": "import"` and `"STUBS_API_KEY"` (instead of the read key) for `import_shows`.

## 5. Keeping it in step

Matinee's rule 6 now holds for Stubs too: new data an assistant would want means a new or extended tool in
`mcp_server.py`, a test in `tests/test_mcp_server.py`, and an update to this file.
