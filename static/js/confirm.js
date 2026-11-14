// To confirm (Task 13): #/admin/confirm lists the shows and lineup entries recorded as "likely", oldest first; each
// can be confirmed, optionally with a source. Also the admin card "To confirm (N)". No DOM at import.
import { ApiError, get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";

const sourceLabels = sources => sources.map(s => s.label).join(", ");
const renderSources = sources => (sources.length ? `<p class="muted">Sources: ${esc(sourceLabels(sources))}</p>` : "");

const renderNote = note => (note ? `<p class="confirm-note">${esc(note)}</p>` : "");

// One item of the list: what is unsure, the show it belongs to, the note, and the form that confirms it.
export function renderConfirmItem(item) {
  const what = item.kind === "lineup" ? `Opener: ${item.artist}` : "The show itself";
  return `<li class="confirm-item" data-kind="${esc(item.kind)}" data-id="${esc(item.id)}">
<p><strong>${esc(what)}</strong></p>
<p><a href="#/show/${esc(item.show.id)}">${esc(item.show.billing)}</a>
<span class="muted">${esc(fmtDate(item.show.date, item.show.precision))} · ${esc(item.show.venue_label)}</span></p>
${renderNote(item.note)}${renderSources(item.sources)}
<div class="confirm-form"><input type="text" name="source_label" maxlength="200" placeholder="Source, e.g. stub scan" aria-label="Source label">
<input type="url" name="source_url" maxlength="2000" placeholder="Link (optional)" aria-label="Source link">
<button class="btn btn-primary" type="button" data-act="confirm">Confirm</button></div>
<p class="muted confirm-msg" role="status"></p></li>`;
}

const renderConfirmItems = items => items.map(renderConfirmItem).join("");

const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");

export async function renderConfirmPage(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>To confirm</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const { items } = await get("/admin/api/to-confirm");
  if (!ctx.alive()) return;
  view.innerHTML = `<h1>To confirm</h1>
<p class="muted">Facts you recorded as likely. Confirm one when you have checked it; add a source if you have one.</p>
<ul class="confirm-list">${renderConfirmItems(items)}</ul>
<p class="muted empty" hidden>Nothing to confirm.</p>`;
  const empty = view.querySelector(".empty");
  const sync = () => {
    empty.hidden = view.querySelector(".confirm-item") !== null;
  };
  sync();
  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act=confirm]");
    const li = btn?.closest(".confirm-item");
    if (!li) return;
    btn.disabled = true;
    try {
      await send("POST", "/admin/api/to-confirm/confirm", {
        kind: li.dataset.kind,
        id: Number(li.dataset.id),
        source_label: li.querySelector("[name=source_label]").value,
        source_url: li.querySelector("[name=source_url]").value,
      });
      if (!ctx.alive()) return;
      li.remove();
      sync();
    } catch (err) {
      if (!ctx.alive()) return;
      btn.disabled = false;
      li.querySelector(".confirm-msg").textContent = message(err);
    }
  });
}

// Admin card "To confirm (N)": hidden when nothing is likely.
export async function renderConfirmCard(el) {
  const { items } = await get("/admin/api/to-confirm");
  if (!items.length) return false;
  el.innerHTML = `<h2>To confirm (${esc(items.length)})</h2>
<p class="muted">Shows and lineup entries you marked as likely.</p>
<p><a class="btn" href="#/admin/confirm">Review them</a></p>`;
  return true;
}
