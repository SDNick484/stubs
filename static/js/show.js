// #/show/<id>: the show, its lineup, its evidence (editors) and every registered show panel. No DOM at import.
import { get } from "./api.js";
import { esc, fmtDate, placeLabel } from "./core.js";
import { showPanels } from "./registry.js";
import { cityHref } from "./stats.js";

const GROUPS = [
  ["headliner", "Headliner"],
  ["coheadliner", "Co-headliners"],
  ["opener", "Openers"],
  ["performer", "Performers"],
];

const safeUrl = url => (/^https?:\/\//i.test(String(url ?? "")) ? String(url) : "");

const renderSource = s => {
  const href = safeUrl(s.url);
  const label = s.label || s.url || "source";
  return href
    ? `<li><a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a></li>`
    : `<li>${esc(label)}</li>`;
};

// A list of sources ({label, url}); empty markup for none. Links open only for http(s) URLs.
const renderSourceItems = sources => sources.map(renderSource).join("");

export function renderSources(sources) {
  if (!sources || !sources.length) return "";
  return `<ul class="sources">${renderSourceItems(sources)}</ul>`;
}

export const renderRating = rating => (rating ? `<span class="rating">★ ${esc(rating)}/5</span>` : "");

const renderLikely = e =>
  e.confidence === "likely" ? `<span class="tag tag-likely" title="${esc(e.confidence_note)}">likely</span>` : "";

const renderEntry = (e, editor) => `<li><a href="#/artist/${esc(e.artist_id)}">${esc(e.name)}</a>${
  renderEditorBits(e, editor)}</li>`;

const renderEditorBits = (e, editor) => (editor ? `${renderLikely(e)}${renderRating(e.rating)}${renderSources(e.sources)}` : "");

const renderEntries = (entries, editor) => entries.map(e => renderEntry(e, editor)).join("");

const renderGroup = (title, entries, editor) => `<section class="lineup-group"><h3>${esc(title)}</h3>
<ul class="lineup-list">${renderEntries(entries, editor)}</ul></section>`;

// The lineup grouped by role (Headliner, Co-headliners, Openers, Performers; empty groups left out). Likely marks,
// ratings and per-entry sources appear only when `editor` is true.
export function renderLineup(lineup, editor) {
  return GROUPS.map(([role, title]) => [title, (lineup || []).filter(e => e.role === role)])
    .filter(([, entries]) => entries.length)
    .map(([title, entries]) => renderGroup(title, entries, editor))
    .join("");
}

const renderVenue = v => `<a href="#/venue/${esc(v.id)}">${esc(v.label)}</a> · <a href="${esc(cityHref(v))}">${esc(placeLabel(v))}</a>`;

const renderMeta = (label, value) => (value ? `<p class="show-meta"><span class="muted">${esc(label)}</span> ${esc(value)}</p>` : "");

const renderUpcomingTag = s => (s.status === "upcoming" ? `<span class="tag tag-upcoming">Upcoming</span>` : "");

const renderNotes = s => (s.notes ? `<section class="show-notes"><h2>Notes</h2><p>${esc(s.notes)}</p></section>` : "");

const renderEvidence = s =>
  (s.sources && s.sources.length ? `<section class="show-sources"><h2>Sources</h2>${renderSources(s.sources)}</section>` : "");

const renderShowEditorBits = (s, editor) => (editor ? `${renderLikely(s)}${renderRating(s.rating)}` : "");

const renderEditorSection = (s, editor) => (editor ? `${renderNotes(s)}${renderEvidence(s)}` : "");

export function renderShowHead(s, editor) {
  return `<h1>${esc(s.billing)} ${renderUpcomingTag(s)}</h1>
<p class="show-when">${fmtDate(s.date, s.precision)}${renderShowEditorBits(s, editor)}</p>
<p class="show-where">${renderVenue(s.venue)}</p>
${renderMeta("Run", s.run)}${renderMeta("Tour", s.tour)}`;
}

const renderStep = (id, label) =>
  id ? `<a class="btn" href="#/show/${esc(id)}">${esc(label)}</a>` : `<span class="btn is-disabled" aria-disabled="true">${esc(label)}</span>`;

export const renderStepper = s => `<nav class="show-step">${renderStep(s.prev_id, "← Previous")}${renderStep(s.next_id, "Next →")}</nav>`;

export function renderShow(s, editor) {
  return `${renderShowHead(s, editor)}
<div class="lineup">${renderLineup(s.lineup, editor)}</div>
${renderEditorSection(s, editor)}
<div class="show-panels"></div>
${renderStepper(s)}`;
}

// Draw every panel in order into its own section; one that throws or rejects is replaced by a short notice and the
// rest carry on.
export async function drawPanels(host, show, ctx) {
  for (const p of showPanels({ editor: show.editor })) {
    const sec = document.createElement("section");
    sec.className = "show-panel";
    sec.dataset.panel = p.name;
    const h = document.createElement("h2");
    h.textContent = p.name;
    const body = document.createElement("div");
    sec.append(h, body);
    host.append(sec);
    try {
      await p.render(body, show);
    } catch (e) {
      if (!ctx.alive()) return;
      console.error(e);
      body.textContent = `${p.name} isn't available right now.`;
      body.className = "muted";
    }
  }
}

export async function renderShowPage(ctx) {
  const editor = Boolean(ctx.session.editor);
  const id = encodeURIComponent(ctx.params.id);
  let show;
  try {
    show = await get(`${editor ? "/admin/api" : "/api"}/shows/${id}`);
  } catch (e) {
    if (e.status !== 404) throw e;
    if (!ctx.alive()) return;
    ctx.view.innerHTML = `<div class="notice"><p>Show not found.</p><p><a class="btn" href="#/">Go home</a></p></div>`;
    return;
  }
  if (!ctx.alive()) return;
  show.editor = editor;
  ctx.view.innerHTML = renderShow(show, editor);
  await drawPanels(ctx.view.querySelector(".show-panels"), show, ctx);
}
