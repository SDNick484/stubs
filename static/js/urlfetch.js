// The "From a link" show panel (spec 7.12), editor-only, order 50: paste a ticket or event page, and the server reads
// its og:image, og:title and schema.org Event JSON-LD and offers suggestions, each with its own button. Nothing is
// applied without a click; the server records the page as each applied suggestion's source. The server never sends
// a page's images to the browser (the CSP allows only our own), so the poster is offered as a link to look at.
// render* functions are DOM-free (tests/urlfetch.test.mjs); the panel wires its events inside renderPanel.
import { send } from "./api.js";
import { esc, fmtDate } from "./core.js";
import { registerShowPanel } from "./registry.js";

const isWeb = url => typeof url === "string" && /^https?:\/\//i.test(url);

function renderApply(kind, value, label) {
  return `<button type="button" class="btn" data-act="apply" data-kind="${esc(kind)}" data-value="${esc(value)}">${esc(label)}</button>`;
}

function renderTitle(title) {
  return title ? `<li class="fetch-info"><span class="muted">Page title:</span> ${esc(title)}</li>` : "";
}

function renderImage(url) {
  if (!isWeb(url)) return "";
  return `<li class="fetch-item"><span>Use <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">this image</a> as the poster</span>${renderApply("poster", url, "Use as poster")}</li>`;
}

function renderPerformer(p) {
  if (p.on_lineup) return `<li class="fetch-item">${esc(p.name)} <span class="muted">already on the lineup</span></li>`;
  return `<li class="fetch-item"><span>${esc(p.name)}</span>${renderApply("performer", p.name, "Add as performer")}</li>`;
}

function renderPerformers(performers) {
  return (performers ?? []).map(renderPerformer).join("");
}

function renderShowDate(data, show) {
  return data.date_differs ? `<span class="muted">(this show: ${fmtDate(show.date, show.precision)})</span>` : `<span class="muted">(the same as this show)</span>`;
}

function renderDate(data, show) {
  const date = data.event?.date;
  if (!date) return "";
  return `<li class="fetch-info">Date says ${fmtDate(date, "day")} ${renderShowDate(data, show)}</li>`;
}

function renderVenue(event) {
  return event?.venue ? `<li class="fetch-info"><span class="muted">Venue says</span> ${esc(event.venue)}</li>` : "";
}

function renderNothing(data) {
  const found = data.title || isWeb(data.image) || data.performers?.length || data.event?.date || data.event?.venue;
  return found ? "" : `<li class="muted">Nothing else to use on that page.</li>`;
}

function renderSource() {
  return `<li class="fetch-item"><span>Keep this page as a source</span>${renderApply("source", "", "Add source")}</li>`;
}

// data: the server's suggestions {url, title, image, event: {date, venue, performers}, performers: [{name,
// on_lineup}], date_differs}; show: the show detail (for its date).
export function renderSuggestions(data, show = {}) {
  return `<ul class="fetch-list">${renderTitle(data.title)}${renderImage(data.image)}${renderPerformers(data.performers)}${renderDate(data, show)}${renderVenue(data.event)}${renderNothing(data)}${renderSource()}</ul>`;
}

function renderForm() {
  return `<form class="fetch-form" data-form="fetch-url">
  <input type="url" name="url" required maxlength="2000" placeholder="https://" aria-label="Page address" autocomplete="off">
  <button class="btn" type="submit">Read page</button></form>`;
}

function renderMessage(text, isError) {
  return `<p class="${esc(isError ? "error" : "muted")}">${esc(text)}</p>`;
}

async function renderPanel(el, show) {
  el.hidden = !show.editor;
  if (!show.editor) return;
  const base = `/admin/api/shows/${encodeURIComponent(show.id)}/fetch-url`;
  el.innerHTML = `<h2>From a link</h2>${renderForm()}<div class="fetch-out" data-slot="out" aria-live="polite"></div><p class="fetch-status error" role="status"></p>`;
  const form = el.querySelector("form");
  const out = el.querySelector('[data-slot="out"]');
  const status = el.querySelector(".fetch-status");
  let page = null;
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const url = form.elements.url.value.trim();
    if (!url) return;
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "";
    out.innerHTML = renderMessage("Reading the page…", false);
    try {
      page = await send("POST", base, { url });
      out.innerHTML = renderSuggestions(page, show);
    } catch (err) {
      page = null;
      out.innerHTML = renderMessage(err?.error ?? "Couldn't read that page.", true);
    } finally {
      button.disabled = false;
    }
  });
  out.addEventListener("click", async e => {
    const btn = e.target.closest('[data-act="apply"]');
    if (!btn || !page || btn.disabled) return;
    btn.disabled = true;
    status.textContent = "";
    try {
      await send("POST", `${base}/apply`, { kind: btn.dataset.kind, value: btn.dataset.value, url: page.url });
      const done = document.createElement("span");
      done.className = "fetch-done";
      done.textContent = "Added";
      btn.replaceWith(done);
    } catch (err) {
      btn.disabled = false;
      status.textContent = err?.error ?? "Couldn't add that. Try again.";
    }
  });
}

registerShowPanel("From a link", { order: 50, editorOnly: true, render: renderPanel });
