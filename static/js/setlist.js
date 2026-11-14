// The Setlist show panel (spec 7.11): every linked act's setlist, in lineup order under the act's name, each fetched
// live from setlist.fm with its own attribution link. Editors also get "Find setlists" (the whole show, searched by
// venue and date and matched to the lineup; nothing is linked until they confirm) and a "Find" per act. The server
// never stores song data; this file only draws what it is given.
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
  return name ? `<h4 class="setlist-set-name">${esc(name)}</h4>` : "";
}

function renderSongs(songs) {
  return songs.map(renderSong).join("");
}

function renderSet(set) {
  return `<div class="setlist-set${esc(set.encore ? " is-encore" : "")}">${renderSetName(set)}<ol class="setlist-songs">${renderSongs(set.songs)}</ol></div>`;
}

// One act's set: the server's {available, url, sets}.
export function renderSetlist(data) {
  if (!data || !data.available) {
    return `<p class="muted">Setlist unavailable.</p>${renderAttribution(data?.url)}`;
  }
  return `${renderSets(data.sets ?? [])}${renderAttribution(data.url)}`;
}

function renderSets(sets) {
  return sets.length ? sets.map(renderSet).join("") : `<p class="muted">No songs listed yet.</p>`;
}

// One linked act: its name, its set (or "Setlist unavailable." with its link), and for editors Find / Remove link.
export function renderAct(act, editor) {
  return `<section class="setlist-act" data-lineup="${esc(act.lineup_id)}"><h3 class="setlist-act-name">${esc(act.name)}</h3>${renderSetlist(act)}${renderActTools(editor)}</section>`;
}

function renderActTools(editor) {
  if (!editor) return "";
  return `<p class="setlist-tools"><button type="button" class="btn" data-act="find">Find</button><span data-slot="unlink">${renderUnlink(false)}</span></p><div data-slot="found"></div>`;
}

export function renderActs(acts, editor) {
  return acts.length ? acts.map(a => renderAct(a, editor)).join("") : `<p class="muted">No setlist yet.</p>`;
}

// Editors: the acts with no setlist linked, each with its own Find.
export function renderUnlinked(unlinked) {
  if (!unlinked.length) return "";
  return `<div class="setlist-missing"><h3 class="setlist-act-name">No setlist linked</h3><ul class="setlist-results">${renderMissingItems(unlinked)}</ul></div>`;
}

function renderMissingItems(unlinked) {
  return unlinked.map(renderMissing).join("");
}

function renderMissing(act) {
  return `<li class="setlist-act" data-lineup="${esc(act.lineup_id)}"><p class="setlist-result"><span>${esc(act.name)}</span><button type="button" class="btn" data-act="find">Find</button></p><div data-slot="found"></div></li>`;
}

export function renderShowTools() {
  return `<p class="setlist-tools"><button type="button" class="btn btn-primary" data-act="find-all">Find setlists</button></p><div data-slot="matches"></div>`;
}

// The whole panel. data: the server's {acts, unlinked?}, or null when it couldn't be read.
export function renderPanelBody(data, editor) {
  if (!data) return `<p class="muted">Setlist unavailable.</p>`;
  return `${renderActs(data.acts ?? [], editor)}${renderEditorTools(data, editor)}`;
}

function renderEditorTools(data, editor) {
  return editor ? `${renderUnlinked(data.unlinked ?? [])}${renderShowTools()}` : "";
}

// setlist.fm writes dates dd-MM-yyyy.
export const fromSetlistDate = d => String(d ?? "").split("-").reverse().join("-");

function renderResultText(r) {
  return `${fmtDate(fromSetlistDate(r.date), "day")} · ${esc(r.artist)} · ${esc(r.venue)}, ${esc(r.city)}`;
}

// One act's search ({available, results}): pick one.
function renderResult(r) {
  return `<li class="setlist-result"><span>${renderResultText(r)}</span>
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

// The whole show's search ({available, acts: [{lineup_id, name, linked, results}], unmatched}): one choice per act
// that has results, then the acts nothing was found for, then the results that match no one on the lineup (listed,
// never linked: adding the act is the Edit panel's job).
// An act starts on the setlist it already has; else on "Keep the current link" when it has another; else on the
// first result. -1 is that "Keep the current link" (or "Don't link") choice.
export function defaultPick(act) {
  if (!act.results.length) return -1;
  if (act.linked) return act.results.findIndex(r => r.id === act.linked);
  return 0;
}

export function renderShowMatches(body) {
  if (!body.available) return `<p class="muted">setlist.fm can't be searched right now.</p>`;
  const found = body.acts.filter(a => a.results.length);
  if (!found.length && !body.unmatched.length) {
    return `<p class="muted">Nothing on setlist.fm for that venue and date.</p>`;
  }
  return `<form class="setlist-matches" data-slot="match-form">${renderMatchGroups(found)}${renderNotFound(body.acts)}${renderUnmatched(body.unmatched)}${renderConfirm(found)}</form>`;
}

function renderMatchGroups(acts) {
  return acts.map(renderMatchGroup).join("");
}

function renderMatchGroup(act) {
  const pick = defaultPick(act);
  return `<fieldset class="setlist-match" data-lineup="${esc(act.lineup_id)}" data-linked="${esc(act.linked ?? "")}"><legend>${esc(act.name)}</legend>${renderOptions(act, pick)}${renderLeave(act, pick)}</fieldset>`;
}

function renderOptions(act, pick) {
  return act.results.map((r, i) => renderOption(act, r, i === pick)).join("");
}

const renderChecked = on => (on ? " checked" : "");

function renderOption(act, r, checked) {
  return `<label class="setlist-option"><input type="radio" name="setlist-${esc(act.lineup_id)}" value="${esc(r.id)}" data-url="${esc(r.url)}"${renderChecked(checked)}><span>${renderResultText(r)}</span></label>`;
}

function renderLeave(act, pick) {
  return `<label class="setlist-option"><input type="radio" name="setlist-${esc(act.lineup_id)}" value=""${renderChecked(pick === -1)}><span>${esc(act.linked ? "Keep the current link" : "Don't link")}</span></label>`;
}

function renderNotFound(acts) {
  const none = acts.filter(a => !a.results.length);
  if (!none.length) return "";
  return `<p class="small muted">Nothing found for ${esc(none.map(a => a.name).join(", "))}: try its own Find.</p>`;
}

function renderUnmatched(results) {
  if (!results.length) return "";
  return `<div class="setlist-unmatched"><p class="small muted">Not on this show's lineup (add the act in Edit show to link it):</p><ul class="setlist-results">${renderUnmatchedItems(results)}</ul></div>`;
}

function renderUnmatchedItems(results) {
  return results.map(renderUnmatchedItem).join("");
}

function renderUnmatchedItem(r) {
  return `<li class="setlist-result"><span>${renderResultText(r)}</span></li>`;
}

function renderConfirm(found) {
  if (!found.length) return "";
  return `<p class="setlist-tools"><button type="submit" class="btn btn-primary">Link selected</button></p><p class="small" data-slot="match-status"></p>`;
}

// An inline Yes / No, never window.confirm.
export function renderUnlink(asking) {
  if (!asking) return `<button type="button" class="btn" data-act="unlink">Remove link</button>`;
  return `Remove link? <button type="button" class="btn" data-act="unlink-yes">Yes</button> <button type="button" class="btn" data-act="unlink-no">No</button>`;
}

// The ticked choices that change something: [{lineup, id, url}].
function chosenLinks(form) {
  const out = [];
  for (const fs of form.querySelectorAll("fieldset[data-lineup]")) {
    const input = fs.querySelector("input:checked");
    if (input && input.value && input.value !== fs.dataset.linked) {
      out.push({ lineup: fs.dataset.lineup, id: input.value, url: input.dataset.url });
    }
  }
  return out;
}

async function renderPanel(el, show) {
  const base = show.editor ? "/admin/api" : "/api";
  const mine = `${base}/shows/${encodeURIComponent(show.id)}`;
  const entry = lineup => `${mine}/lineup/${encodeURIComponent(lineup)}/setlistfm`;
  el.hidden = show.status === "upcoming" && !show.editor;
  if (el.hidden) return;
  let data;
  try {
    data = await get(`${mine}/setlist`);
  } catch {
    data = null;
  }
  const box = document.createElement("div");
  box.innerHTML = renderPanelBody(data, show.editor);
  el.replaceChildren(box);
  if (!show.editor || !data) return;

  box.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const holder = btn.closest("[data-lineup]");
    const slot = holder ? holder.querySelector('[data-slot="found"]') : box.querySelector('[data-slot="matches"]');
    try {
      if (act === "find") {
        slot.innerHTML = `<p class="muted">Searching…</p>`;
        slot.innerHTML = renderResults(await get(`${mine}/setlistfm/search?lineup_id=${encodeURIComponent(holder.dataset.lineup)}`));
      } else if (act === "find-all") {
        slot.innerHTML = `<p class="muted">Searching…</p>`;
        slot.innerHTML = renderShowMatches(await get(`${mine}/setlistfm/search`));
      } else if (act === "pick") {
        await send("PUT", entry(holder.dataset.lineup), { id: btn.dataset.id, url: btn.dataset.url });
        await renderPanel(el, show);
      } else if (act === "unlink" || act === "unlink-no") {
        holder.querySelector('[data-slot="unlink"]').innerHTML = renderUnlink(act === "unlink");
      } else if (act === "unlink-yes") {
        await send("DELETE", entry(holder.dataset.lineup));
        await renderPanel(el, show);
      }
    } catch (err) {
      slot.textContent = err.error ?? "Something went wrong.";
    }
  });

  // Confirming the whole-show search: link each ticked act, one after another, then redraw.
  box.addEventListener("submit", async e => {
    const form = e.target.closest('[data-slot="match-form"]');
    if (!form) return;
    e.preventDefault();
    const status = form.querySelector('[data-slot="match-status"]');
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      for (const pick of chosenLinks(form)) {
        await send("PUT", entry(pick.lineup), { id: pick.id, url: pick.url });
      }
      await renderPanel(el, show);
    } catch (err) {
      submit.disabled = false;
      status.textContent = err.error ?? "Something went wrong.";
    }
  });
}

registerShowPanel("Setlist", { order: 20, editorOnly: false, render: renderPanel });
