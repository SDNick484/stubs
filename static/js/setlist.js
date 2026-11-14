// The Setlist show panel (spec 7.11): the setlist fetched live from setlist.fm with its attribution link, and for
// editors "Find on setlist.fm". The server never stores song data; this file only draws what it is given.
import { get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";
import { registerShowPanel } from "./registry.js";

const SITE = "https://www.setlist.fm/";

// The attribution link setlist.fm requires: followable (no nofollow), and only ever to setlist.fm itself.
export function renderAttribution(url) {
  if (typeof url !== "string" || !url.startsWith(SITE)) return "";
  return `<p class="small setlist-credit"><a href="${esc(url)}" target="_blank" rel="noopener">Setlist from setlist.fm</a></p>`;
}

function renderSong(song) {
  return `<li>${esc(song)}</li>`;
}

function renderSetName(set) {
  const name = set.encore ? "Encore" : set.name;
  return name ? `<h3 class="setlist-set-name">${esc(name)}</h3>` : "";
}

function renderSongs(songs) {
  return songs.map(renderSong).join("");
}

function renderSet(set) {
  return `<div class="setlist-set${esc(set.encore ? " is-encore" : "")}">${renderSetName(set)}<ol class="setlist-songs">${renderSongs(set.songs)}</ol></div>`;
}

// data: the server's {available, url, sets}.
export function renderSetlist(data) {
  if (!data || !data.available) {
    return `<p class="muted">Setlist unavailable.</p>${renderAttribution(data?.url)}`;
  }
  return `${renderSets(data.sets ?? [])}${renderAttribution(data.url)}`;
}

function renderSets(sets) {
  return sets.length ? sets.map(renderSet).join("") : `<p class="muted">No songs listed yet.</p>`;
}

// setlist.fm writes dates dd-MM-yyyy.
export const fromSetlistDate = d => String(d ?? "").split("-").reverse().join("-");

function renderResult(r) {
  return `<li class="setlist-result"><span>${fmtDate(fromSetlistDate(r.date), "day")} · ${esc(r.artist)} · ${esc(r.venue)}, ${esc(r.city)}</span>
  <button type="button" class="btn" data-act="pick" data-id="${esc(r.id)}" data-url="${esc(r.url)}">Use this one</button></li>`;
}

function renderMatches(results) {
  return results.map(renderResult).join("");
}

export function renderResults(body) {
  if (!body.available) return `<p class="muted">setlist.fm can't be searched right now.</p>`;
  if (!body.results.length) return `<p class="muted">Nothing on setlist.fm for that date.</p>`;
  return `<ul class="setlist-results">${renderMatches(body.results)}</ul>`;
}

export function renderTools(linked) {
  return `<p class="setlist-tools"><button type="button" class="btn" data-act="find">Find on setlist.fm</button>${renderUnlinkSlot(linked)}</p><div data-slot="results"></div>`;
}

function renderUnlinkSlot(linked) {
  return linked ? `<span data-slot="unlink">${renderUnlink(false)}</span>` : "";
}

// An inline Yes / No, never window.confirm.
export function renderUnlink(asking) {
  if (!asking) return `<button type="button" class="btn" data-act="unlink">Remove link</button>`;
  return `Remove link? <button type="button" class="btn" data-act="unlink-yes">Yes</button> <button type="button" class="btn" data-act="unlink-no">No</button>`;
}

async function renderPanel(el, show) {
  const base = show.editor ? "/admin/api" : "/api";
  const mine = `${base}/shows/${encodeURIComponent(show.id)}`;
  el.hidden = show.status === "upcoming" && !show.editor;
  if (el.hidden) return;
  let data;
  try {
    data = await get(`${mine}/setlist`);
  } catch {
    data = { available: false, url: null, sets: [] };
  }
  el.innerHTML = `<h2>Setlist</h2><div data-slot="setlist">${renderSetlist(data)}</div>`;
  if (!show.editor) return;
  const tools = document.createElement("div");
  tools.innerHTML = renderTools(Boolean(data.url));
  el.append(tools);
  const results = tools.querySelector('[data-slot="results"]');
  tools.addEventListener("click", async e => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    try {
      if (act === "find") {
        results.innerHTML = `<p class="muted">Searching…</p>`;
        results.innerHTML = renderResults(await get(`${mine}/setlistfm/search`));
      } else if (act === "pick") {
        await send("PUT", `${mine}/setlistfm`, { id: btn.dataset.id, url: btn.dataset.url });
        await renderPanel(el, show);
      } else if (act === "unlink" || act === "unlink-no") {
        tools.querySelector('[data-slot="unlink"]').innerHTML = renderUnlink(act === "unlink");
      } else if (act === "unlink-yes") {
        await send("DELETE", `${mine}/setlistfm`);
        await renderPanel(el, show);
      }
    } catch (err) {
      results.textContent = err.error ?? "Something went wrong.";
    }
  });
}

registerShowPanel("Setlist", { order: 20, editorOnly: false, render: renderPanel });
