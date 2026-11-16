// Concert watch (post-v1 #47), editors only: the admin card "New near you (N)" (order 12: Add as upcoming / Dismiss),
// #/admin/watch (watched artists, suggestions from history and Last.fm, add by name, watch areas, Check now, which
// sources are on) and, on the artist page under /admin, a Watch / Watching toggle with that artist's sightings. The
// server makes every outside call (connect-src is 'self'). No DOM at import, so node --test can load the helpers.
import { ApiError, get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";

const SOURCES = { ticketmaster: "Ticketmaster", bandsintown: "Bandsintown", email: "Email" };
const ORIGINS = { history: "from your shows", manual: "added by name", lastfm: "from Last.fm" };
const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");
const safeUrl = u => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : "");

// "Never seen", "Seen once", "Seen twice", "Seen 3 times".
export const seenLabel = times => (!times ? "Never seen" : times === 1 ? "Seen once" : times === 2 ? "Seen twice" : `Seen ${times} times`);
export const sourceLabel = source => SOURCES[source] ?? String(source ?? "");
// "Old Mill Theatre, Portland"
export const placeOf = s => [s.venue, s.city].filter(Boolean).join(", ");

// ---------------------------------------------------------------- one sighting
const renderSourceLink = s =>
  safeUrl(s.url) ? `<a href="${esc(safeUrl(s.url))}" rel="noopener noreferrer">${esc(sourceLabel(s.source))}</a>` : `<span>${esc(sourceLabel(s.source))}</span>`;
const renderHave = s => (s.have ? `<a class="tag tag-upcoming" href="#/show/${esc(s.have)}">You have tickets</a>` : "");
const renderNote = s => (s.note ? `<span class="muted small">${esc(s.note)}</span>` : "");

// "Marigold Static · Mar 14, 2027 · Old Mill Theatre, Portland", its source (a link when it has one) and, when a show
// already in Stubs is this concert, "You have tickets".
export const renderSightingText = s => `<strong>${esc(s.artist)}</strong>
<span class="muted">${fmtDate(s.date, "day")} · ${esc(placeOf(s))}</span>
<span class="watch-meta small">${renderSourceLink(s)} ${renderHave(s)} ${renderNote(s)}</span>`;

const renderAddButton = s => (s.have ? "" : `<button type="button" class="btn btn-primary" data-act="add">Add as upcoming</button>`);

export const renderSighting = s => `<li class="watch-sighting" data-id="${esc(s.id)}">
<div class="watch-what">${renderSightingText(s)}</div>
<div class="watch-actions">${renderAddButton(s)}
<button type="button" class="btn" data-act="dismiss">Dismiss</button></div>
<div class="watch-confirm" hidden><span>Dismiss this concert?</span>
<button type="button" class="btn" data-act="dismiss-yes">Yes</button>
<button type="button" class="btn" data-act="dismiss-no">No</button></div>
<p class="muted small watch-msg" role="status"></p></li>`;

const renderSightingItems = list => list.map(renderSighting).join("");
export const renderSightings = list => `<ul class="watch-list">${renderSightingItems(list)}</ul>`;
export const renderAdded = showId => `Added. <a href="#/show/${esc(showId)}">Open the show</a>`;

// Wires Add as upcoming / Dismiss (with its inline Yes / No) on the rows inside `box`. done(li) runs after either.
function wireSightings(box, done) {
  box.addEventListener("click", async ev => {
    const btn = ev.target.closest("button[data-act]");
    const li = btn?.closest(".watch-sighting");
    if (!li) return;
    const act = btn.dataset.act;
    const actions = li.querySelector(".watch-actions");
    const confirmRow = li.querySelector(".watch-confirm");
    const msg = li.querySelector(".watch-msg");
    if (act === "dismiss" || act === "dismiss-no") {
      confirmRow.hidden = act === "dismiss-no";
      actions.hidden = act === "dismiss";
      return;
    }
    if (act !== "add" && act !== "dismiss-yes") return;
    btn.disabled = true;
    try {
      if (act === "add") {
        const r = await send("POST", `/admin/api/watch/sightings/${encodeURIComponent(li.dataset.id)}/add`);
        actions.hidden = true;
        msg.innerHTML = renderAdded(r.show_id);
      } else {
        await send("POST", `/admin/api/watch/sightings/${encodeURIComponent(li.dataset.id)}/dismiss`);
        li.remove();
      }
      done?.(li);
    } catch (e) {
      msg.textContent = message(e);
      btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- the admin card (order 12)
export const renderCardTitle = n => `<h2>New near you (${esc(n)})</h2>`;

export async function renderNewNearYouCard(el) {
  const { sightings } = await get("/admin/api/watch/sightings");
  if (!sightings.length) return false;
  el.innerHTML = `${renderCardTitle(sightings.length)}${renderSightings(sightings)}
<p class="small"><a href="#/admin/watch">Concert watch</a></p>`;
  wireSightings(el, () => {
    const left = el.querySelectorAll(".watch-sighting").length;
    el.querySelector("h2").textContent = `New near you (${left})`;
  });
  return true;
}

// ---------------------------------------------------------------- #/admin/watch
const onOff = on => (on ? "on" : "off");
export const renderSources = s => `<p class="muted small watch-sources">Ticketmaster ${esc(onOff(s.ticketmaster))} ·
Last.fm suggestions ${esc(onOff(s.lastfm))} · Email filing on · Push ${esc(s.notify ?? "off")}</p>`;

const renderChecked = on => (on ? " checked" : "");
const renderLastChecked = w => (w.last_checked ? `Checked ${fmtDate(String(w.last_checked).slice(0, 10), "day")}` : "Not checked yet");
const renderLastSeen = w => (w.last ? ` · last ${fmtDate(w.last)}` : "");

const renderMiniSighting = s => `<li>${fmtDate(s.date, "day")} · ${esc(placeOf(s))}${renderStatus(s.status)}</li>`;
const renderStatus = status => (status === "added" ? ` <span class="tag">added</span>` : "");
const renderMiniItems = list => list.map(renderMiniSighting).join("");
const renderMiniList = list => (list.length ? `<ul class="watch-mini">${renderMiniItems(list)}</ul>` : "");

// One watched artist: name (to the artist page), "Seen 3 times · last …" or "Never seen", where it came from,
// Anywhere and Muted switches, when it was last checked, its upcoming sightings, and Stop watching (inline Yes / No).
export const renderWatchedRow = w => `<li class="watch-row" data-id="${esc(w.artist_id)}">
<div class="watch-head"><a href="#/artist/${esc(w.artist_id)}"><strong>${esc(w.name)}</strong></a>
<span class="tag">${esc(seenLabel(w.times))}${renderLastSeen(w)}</span></div>
<p class="muted small">${esc(ORIGINS[w.origin] ?? "")} · ${renderLastChecked(w)}</p>
<div class="watch-switches">
<label><input type="checkbox" data-act="anywhere"${renderChecked(w.anywhere)}> Anywhere</label>
<label><input type="checkbox" data-act="muted"${renderChecked(w.muted)}> Muted</label>
<button type="button" class="btn" data-act="unwatch">Stop watching</button></div>
<div class="watch-confirm" hidden><span>Stop watching ${esc(w.name)}?</span>
<button type="button" class="btn" data-act="unwatch-yes">Yes</button>
<button type="button" class="btn" data-act="unwatch-no">No</button></div>
${renderMiniList(w.sightings ?? [])}
<p class="muted small watch-msg" role="status"></p></li>`;

const renderWatchedItems = list => list.map(renderWatchedRow).join("");
const renderWatched = list =>
  list.length ? `<ul class="watch-list">${renderWatchedItems(list)}</ul>` : `<p class="muted">Not watching anyone yet.</p>`;

export const renderSuggestion = s => `<li class="watch-suggestion" data-id="${esc(s.artist_id)}">
<span><strong>${esc(s.name)}</strong> <span class="muted small">${esc(seenLabel(s.times))}${renderLastSeen(s)}</span></span>
<button type="button" class="btn" data-act="watch-one">Watch</button></li>`;
const renderSuggestionItems = list => list.map(renderSuggestion).join("");
const renderHistory = list => (list.length
  ? `<p><button type="button" class="btn" data-act="watch-twice">Watch all seen 2+ times</button></p>
<ul class="watch-list">${renderSuggestionItems(list)}</ul>`
  : `<p class="muted">Everyone you've seen is watched.</p>`);

const renderFmName = a => (safeUrl(a.url) ? `<a href="${esc(safeUrl(a.url))}" rel="noopener noreferrer">${esc(a.name)}</a>` : esc(a.name));
const renderFmSeen = a => (a.seen ? "seen" : "never seen");
export const renderLastfmRow = a => `<li class="watch-suggestion" data-name="${esc(a.name)}">
<span><strong>${renderFmName(a)}</strong> <span class="muted small">${esc(a.playcount)} plays · ${esc(renderFmSeen(a))}</span></span>
<button type="button" class="btn" data-act="watch-fm">Watch</button></li>`;
const renderLastfmItems = list => list.map(renderLastfmRow).join("");
export const renderLastfm = list => (list.length
  ? `<ul class="watch-list">${renderLastfmItems(list)}</ul>`
  : `<p class="muted">No new suggestions.</p>`);
const renderLastfmSection = on => (on
  ? `<section class="watch-section"><h2>From Last.fm</h2>
<p class="muted small">Top artists of the last 12 months, from <a href="https://www.last.fm/" rel="noopener noreferrer">Last.fm</a>.</p>
<p><button type="button" class="btn" data-act="lastfm">Show suggestions</button></p>
<div data-role="lastfm"></div></section>`
  : "");

export const renderArea = a => `<li class="watch-area" data-id="${esc(a.id)}">
<span><strong>${esc(a.name)}</strong> <span class="muted small">${esc(a.lat)}, ${esc(a.lon)} · ${esc(a.radius_km)} km</span></span>
<button type="button" class="btn" data-act="area-remove">Remove</button>
<span class="watch-confirm" hidden>Remove? <button type="button" class="btn" data-act="area-yes">Yes</button>
<button type="button" class="btn" data-act="area-no">No</button></span></li>`;
const renderAreaItems = list => list.map(renderArea).join("");
const renderAreas = list =>
  list.length ? `<ul class="watch-list">${renderAreaItems(list)}</ul>` : `<p class="muted">No watch areas yet: only artists marked Anywhere are checked.</p>`;

const renderWatchPage = (d, history) => `<h1>Concert watch</h1>
${renderSources(d.sources)}
<p><button type="button" class="btn btn-primary" data-act="run">Check now</button>
<span class="muted small" data-role="run" role="status"></span></p>
<section class="watch-section"><h2>Add a band</h2>
<form class="watch-form" data-form="add">
<label>Name <input type="text" name="name" required maxlength="200" autocomplete="off"></label>
<label class="watch-check"><input type="checkbox" name="anywhere"> Anywhere (I'd travel)</label>
<p><button type="submit" class="btn btn-primary">Watch</button></p>
<p class="error small" data-role="add-error" hidden></p></form></section>
<section class="watch-section"><h2>Watching (${esc(d.watched.length)})</h2>${renderWatched(d.watched)}</section>
<section class="watch-section"><h2>From your shows</h2>${renderHistory(history)}</section>
${renderLastfmSection(d.sources.lastfm)}
<section class="watch-section"><h2>Watch areas</h2>${renderAreas(d.areas)}
<form class="watch-form" data-form="area">
<label>Name <input type="text" name="name" required maxlength="100" placeholder="Home"></label>
<label>Latitude <input type="text" inputmode="decimal" name="lat" required></label>
<label>Longitude <input type="text" inputmode="decimal" name="lon" required></label>
<label>Radius (km) <input type="text" inputmode="decimal" name="radius_km" required value="100"></label>
<p><button type="submit" class="btn">Add area</button></p>
<p class="error small" data-role="area-error" hidden></p></form></section>`;

// A number within [min, max] from what was typed, or null.
export function parseNumber(raw, min, max) {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

export async function renderWatch(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Concert watch</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const [d, h] = await Promise.all([get("/admin/api/watch"), get("/admin/api/watch/suggestions")]);
  if (!ctx.alive()) return;
  view.innerHTML = renderWatchPage(d, h.artists);
  const status = view.querySelector("[data-role=run]");
  const fail = (slot, e) => {
    const p = view.querySelector(`[data-role=${slot}]`);
    p.textContent = message(e);
    p.hidden = false;
  };
  const again = () => ctx.alive() && ctx.rerender();

  view.addEventListener("submit", async ev => {
    const form = ev.target.closest("form[data-form]");
    if (!form) return;
    ev.preventDefault();
    const f = new FormData(form);
    try {
      if (form.dataset.form === "add") {
        await send("POST", "/admin/api/watch", { name: String(f.get("name") ?? ""), anywhere: f.get("anywhere") === "on", origin: "manual" });
      } else {
        const lat = parseNumber(f.get("lat"), -90, 90);
        const lon = parseNumber(f.get("lon"), -180, 180);
        const radius = parseNumber(f.get("radius_km"), 0.001, 20000);
        if (lat === null || lon === null || radius === null) {
          fail("area-error", new ApiError(400, "Latitude is -90 to 90, longitude -180 to 180, and the radius more than 0 km."));
          return;
        }
        await send("POST", "/admin/api/watch/areas", { name: String(f.get("name") ?? ""), lat, lon, radius_km: radius });
      }
      again();
    } catch (e) {
      if (ctx.alive()) fail(form.dataset.form === "add" ? "add-error" : "area-error", e);
    }
  });

  view.addEventListener("change", async ev => {
    const box = ev.target.closest("input[data-act]");
    const li = box?.closest(".watch-row");
    if (!li) return;
    try {
      await send("PATCH", `/admin/api/watch/${encodeURIComponent(li.dataset.id)}`, { [box.dataset.act]: box.checked });
    } catch (e) {
      box.checked = !box.checked;
      li.querySelector(".watch-msg").textContent = message(e);
    }
  });

  view.addEventListener("click", async ev => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const li = btn.closest("li");
    try {
      if (act === "run") {
        btn.disabled = true;
        status.textContent = "Checking…";
        const r = await send("POST", "/admin/api/watch/run");
        if (!ctx.alive()) return;
        status.textContent = `Checked ${r.checked}, ${r.new} new, ${r.notified} pushed.`;
        btn.disabled = false;
      } else if (act === "unwatch" || act === "unwatch-no") {
        li.querySelector(".watch-confirm").hidden = act === "unwatch-no";
      } else if (act === "unwatch-yes") {
        await send("DELETE", `/admin/api/watch/${encodeURIComponent(li.dataset.id)}`);
        again();
      } else if (act === "watch-one") {
        await send("POST", "/admin/api/watch", { artist_id: Number(li.dataset.id), origin: "history" });
        again();
      } else if (act === "watch-twice") {
        const ids = h.artists.filter(a => a.times >= 2).map(a => a.artist_id);
        if (ids.length) await send("POST", "/admin/api/watch", { artist_ids: ids, origin: "history" });
        again();
      } else if (act === "lastfm") {
        const box = view.querySelector("[data-role=lastfm]");
        box.textContent = "Asking Last.fm…";
        const r = await get("/admin/api/watch/lastfm");
        if (ctx.alive()) box.innerHTML = renderLastfm(r.artists);
      } else if (act === "watch-fm") {
        await send("POST", "/admin/api/watch", { name: li.dataset.name, origin: "lastfm" });
        li.remove();
      } else if (act === "area-remove" || act === "area-no") {
        li.querySelector(".watch-confirm").hidden = act === "area-no";
      } else if (act === "area-yes") {
        await send("DELETE", `/admin/api/watch/areas/${encodeURIComponent(li.dataset.id)}`);
        again();
      }
    } catch (e) {
      if (!ctx.alive()) return;
      btn.disabled = false;
      const msg = li?.querySelector(".watch-msg") ?? (act === "lastfm" ? view.querySelector("[data-role=lastfm]") : status);
      msg.textContent = message(e);
    }
  });
}

// ---------------------------------------------------------------- the artist page (under /admin)
const renderToggle = w => (w.watching
  ? `<button type="button" class="btn btn-primary" data-act="unwatch" aria-pressed="true">Watching</button>`
  : `<button type="button" class="btn" data-act="watch" aria-pressed="false">Watch</button>`);
const renderArtistSighting = s => `<li class="watch-sighting">${renderSightingText(s)}${renderStatus(s.status)}</li>`;
const renderArtistSightingItems = list => list.map(renderArtistSighting).join("");
const renderArtistSightings = list => (list.length ? `<ul class="watch-list">${renderArtistSightingItems(list)}</ul>` : "");

// The Watch / Watching toggle (Watching asks "Stop watching? Yes / No") and the artist's sightings.
export const renderArtistWatch = w => `<h2>Concert watch</h2>
<div class="watch-switches">${renderToggle(w)}
<span class="watch-confirm" hidden>Stop watching? <button type="button" class="btn" data-act="unwatch-yes">Yes</button>
<button type="button" class="btn" data-act="unwatch-no">No</button></span></div>
${renderArtistSightings(w.sightings)}
<p class="muted small watch-msg" role="status"></p>`;

// The artist page (whatever `inner` draws), plus the watch section for editors on /admin pages.
export async function showArtistWithWatch(inner, ctx) {
  await inner(ctx);
  if (!ctx.alive() || !ctx.session?.editor || !location.pathname.startsWith("/admin")) return;
  const base = `/admin/api/watch/artists/${encodeURIComponent(ctx.params.id)}`;
  let w;
  try {
    w = await get(base);
  } catch {
    return;
  }
  if (!ctx.alive()) return;
  const section = document.createElement("section");
  section.className = "artist-watch";
  const anchor = ctx.view.querySelector(".mb-block") ?? ctx.view.querySelector(".summary");
  if (anchor) anchor.after(section);
  else ctx.view.append(section);
  const paint = () => {
    section.innerHTML = renderArtistWatch(w);
  };
  paint();
  section.addEventListener("click", async ev => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    try {
      if (act === "unwatch" || act === "unwatch-no") {
        section.querySelector(".watch-confirm").hidden = act === "unwatch-no";
        return;
      }
      if (act === "watch") await send("POST", "/admin/api/watch", { artist_id: w.artist_id, origin: w.seen ? "history" : "manual" });
      else if (act === "unwatch-yes") await send("DELETE", `/admin/api/watch/${encodeURIComponent(w.artist_id)}`);
      else return;
      try {
        w = await get(base);
      } catch (e) {
        // an artist that was only watched is gone with its watch: back to the watch page
        if (e instanceof ApiError && e.status === 404 && ctx.alive()) location.hash = "#/admin/watch";
        return;
      }
      if (ctx.alive()) paint();
    } catch (e) {
      if (ctx.alive()) section.querySelector(".watch-msg").textContent = message(e);
    }
  });
}
