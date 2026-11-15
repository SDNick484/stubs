// MusicBrainz links (post-v1 #45): the "MusicBrainz" link and the editor's Link / Change / Unlink on the artist page,
// #/admin/artists (artists without a link: Suggest, Suggest for all unlinked, Link all exact) and the admin card
// "Artists without a MusicBrainz link (N)". The server makes every MusicBrainz call (connect-src is 'self'). Nothing
// links itself: the editor picks. No DOM at import, so node --test can load the helpers.
import { ApiError, get, send } from "./api.js";
import { esc } from "./core.js";
import { renderProgress } from "./venues.js";

const countLabel = n => `${n} ${n === 1 ? "show" : "shows"}`;
const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");

export const mbUrl = mbid => `https://musicbrainz.org/artist/${encodeURIComponent(mbid)}`;

// "UK post-punk band · GB": a candidate's note and country, whichever it has.
const detail = c => [c.disambiguation, c.country].filter(Boolean).join(" · ");

// ---------------------------------------------------------------- the artist page
const renderMbAnchor = mbid => `<a class="mb-link" href="${esc(mbUrl(mbid))}" rel="noopener noreferrer">MusicBrainz</a>`;
const renderMbNote = a => (a.mb_disambiguation ? ` <span class="muted">${esc(a.mb_disambiguation)}</span>` : "");
const renderMbLine = a => (a.mbid ? `<p class="mb-line">${renderMbAnchor(a.mbid)}${renderMbNote(a)}</p>` : "");
const renderMbControls = editor => (editor ? `<div class="mb-controls" data-role="mb-controls"></div>` : "");

// The public MusicBrainz link under the artist's summary; editors on /admin pages also get the controls' mount point.
export const renderMbBlock = (a, editor) => `<div class="mb-block">${renderMbLine(a)}${renderMbControls(editor)}</div>`;

export const renderMergeLink = (other, mine) =>
  `Another artist has this MusicBrainz ID. <a href="#/admin/merge?keep=${esc(other)}&amp;drop=${esc(mine)}">Merge them</a>`;

export const renderCandidate = c => `<li class="mb-candidate" data-mbid="${esc(c.mbid)}">
<div class="mb-cand-head"><strong>${esc(c.name)}</strong> <span class="muted">${esc(detail(c))}</span>
<span class="tag">${esc(c.score)}</span></div>
<button class="btn" type="button" data-act="use" data-mbid="${esc(c.mbid)}">Use</button></li>`;

const renderCandidateItems = list => list.map(renderCandidate).join("");
const renderCandidates = list =>
  list.length ? `<ul class="mb-candidates">${renderCandidateItems(list)}</ul>` : `<p class="muted">No match.</p>`;

const renderButtons = a => (a.mbid
  ? `<button class="btn" type="button" data-act="find">Change</button>
<button class="btn" type="button" data-act="unlink">Unlink</button>`
  : `<button class="btn" type="button" data-act="find">Link to MusicBrainz</button>`);

const renderConfirmUnlink = () => `Unlink? <button class="btn" type="button" data-act="unlink-yes">Yes</button>
<button class="btn" type="button" data-act="unlink-no">No</button>`;

// Wires the editor's controls inside the mount point renderMbBlock left (a no-op when there is none).
export function mountMbControls(ctx, a) {
  const box = ctx.view.querySelector("[data-role=mb-controls]");
  if (!box) return;
  const draw = () => {
    box.innerHTML = `<div class="mb-actions">${renderButtons(a)}</div><div class="mb-panel" role="status"></div>`;
  };
  draw();
  const panel = () => box.querySelector(".mb-panel");
  const base = `/admin/api/artists/${encodeURIComponent(a.id)}/musicbrainz`;
  let found = [];

  async function find() {
    panel().textContent = "Looking…";
    const r = await get(`${base}/search`);
    if (!ctx.alive()) return;
    found = r.candidates;
    panel().innerHTML = renderCandidates(found);
  }

  async function use(mbid) {
    const c = found.find(x => x.mbid === mbid);
    if (!c) return;
    try {
      await send("PUT", base, { mbid: c.mbid, disambiguation: c.disambiguation });
    } catch (e) {
      if (!ctx.alive()) return;
      if (e instanceof ApiError && e.status === 409 && e.data?.artist_id) panel().innerHTML = renderMergeLink(e.data.artist_id, a.id);
      else panel().textContent = message(e);
      return;
    }
    if (ctx.alive()) ctx.rerender();
  }

  box.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    try {
      if (act === "find") await find();
      else if (act === "use") await use(btn.dataset.mbid);
      else if (act === "unlink") panel().innerHTML = renderConfirmUnlink();
      else if (act === "unlink-no") panel().textContent = "";
      else if (act === "unlink-yes") {
        await send("DELETE", base);
        if (ctx.alive()) ctx.rerender();
      }
    } catch (err) {
      if (ctx.alive()) panel().textContent = message(err);
    }
  });
}

// ---------------------------------------------------------------- #/admin/artists
const renderExactTag = exact => (exact ? ` <span class="tag">exact</span>` : "");

// One suggestion under an unlinked artist: {candidates, exact} from the server -> the top match with its score and
// note, and Link / Skip; "No match." when there is none.
export function renderSuggestion(s) {
  const top = s.candidates[0];
  if (!top) return `<p class="muted">No match.</p>`;
  return `<div class="mb-top"><strong>${esc(top.name)}</strong> <span class="muted">${esc(detail(top))}</span>
<span class="tag">${esc(top.score)}</span>${renderExactTag(s.exact)}</div>
<div class="mb-row-actions"><button class="btn btn-primary" type="button" data-act="link">Link</button>
<button class="btn" type="button" data-act="skip">Skip</button></div>`;
}

const renderRow = a => `<li class="mb-row" data-id="${esc(a.id)}">
<div class="mb-head"><strong>${esc(a.name)}</strong> <span class="tag">${esc(countLabel(a.shows))}</span>
<button class="btn" type="button" data-act="suggest">Suggest</button></div>
<div class="mb-suggestion"></div><p class="muted mb-msg" role="status"></p></li>`;

const renderRows = list => list.map(renderRow).join("");
const renderPage = list => `<h1>Artists without a MusicBrainz link</h1>
<p class="muted">Suggest asks MusicBrainz, at most once a second, and shows the best match. Nothing links until you do.</p>
<p><button class="btn btn-primary" type="button" data-act="suggest-all">Suggest for all unlinked</button>
<button class="btn" type="button" data-act="link-exact" hidden>Link all exact</button>
<span class="muted" data-role="progress" role="status"></span></p>
<ul class="mb-list">${renderRows(list)}</ul>
<p class="muted empty" hidden>Every artist has a MusicBrainz link.</p>`;

export async function renderArtistsPage(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Artists without a MusicBrainz link</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const { artists } = await get("/admin/api/artists/unlinked");
  if (!ctx.alive()) return;
  view.innerHTML = renderPage(artists);
  const empty = view.querySelector(".empty");
  const progress = view.querySelector("[data-role=progress]");
  const allBtn = view.querySelector("[data-act=suggest-all]");
  const exactBtn = view.querySelector("[data-act=link-exact]");
  const suggested = new Map(); // artist id -> {candidates, exact}
  const sync = () => {
    empty.hidden = view.querySelector(".mb-row") !== null;
    allBtn.hidden = !empty.hidden;
    exactBtn.hidden = ![...suggested.entries()].some(([id, s]) => s.exact && view.querySelector(`.mb-row[data-id="${id}"]`));
  };
  sync();

  const say = (li, text) => {
    li.querySelector(".mb-msg").textContent = text;
  };

  async function suggest(li) {
    say(li, "Looking…");
    const s = await get(`/admin/api/artists/${li.dataset.id}/musicbrainz/search`);
    if (!ctx.alive()) return;
    suggested.set(li.dataset.id, s);
    li.querySelector(".mb-suggestion").innerHTML = renderSuggestion(s);
    say(li, "");
    sync();
  }

  // True when linked; false when the server refused (the row says why).
  async function link(li) {
    const s = suggested.get(li.dataset.id);
    const top = s?.candidates[0];
    if (!top) return false;
    try {
      await send("PUT", `/admin/api/artists/${li.dataset.id}/musicbrainz`, { mbid: top.mbid, disambiguation: top.disambiguation });
    } catch (e) {
      if (!ctx.alive()) return false;
      if (e instanceof ApiError && e.status === 409 && e.data?.artist_id) li.querySelector(".mb-msg").innerHTML = renderMergeLink(e.data.artist_id, li.dataset.id);
      else say(li, message(e));
      return false;
    }
    if (!ctx.alive()) return false;
    suggested.delete(li.dataset.id);
    li.remove();
    sync();
    return true;
  }

  async function suggestAll() {
    const rows = [...view.querySelectorAll(".mb-row")];
    allBtn.disabled = true;
    let done = 0;
    progress.textContent = renderProgress(done, rows.length);
    for (const li of rows) {
      try {
        await suggest(li);
      } catch (e) {
        if (!ctx.alive()) return;
        say(li, message(e));
      }
      if (!ctx.alive()) return;
      done += 1;
      progress.textContent = renderProgress(done, rows.length);
    }
    progress.textContent = "";
    allBtn.disabled = false;
  }

  async function linkExact() {
    exactBtn.disabled = true;
    let n = 0;
    for (const li of [...view.querySelectorAll(".mb-row")]) {
      if (suggested.get(li.dataset.id)?.exact && (await link(li))) n += 1;
      if (!ctx.alive()) return;
    }
    exactBtn.disabled = false;
    progress.textContent = n === 1 ? "Linked 1 artist." : `Linked ${n} artists.`;
    sync();
  }

  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    const act = btn?.dataset.act;
    const li = btn?.closest(".mb-row");
    try {
      if (act === "suggest-all") await suggestAll();
      else if (act === "link-exact") await linkExact();
      else if (act === "suggest" && li) await suggest(li);
      else if (act === "link" && li) await link(li);
      else if (act === "skip" && li) {
        suggested.delete(li.dataset.id);
        li.querySelector(".mb-suggestion").innerHTML = "";
        sync();
      }
    } catch (err) {
      if (li && ctx.alive()) say(li, message(err));
    }
  });
}

// Admin card "Artists without a MusicBrainz link (N)": hidden when every artist has one.
export async function renderUnlinkedCard(el) {
  const { artists } = await get("/admin/api/artists/unlinked");
  if (!artists.length) return false;
  el.innerHTML = `<h2>Artists without a MusicBrainz link (${esc(artists.length)})</h2>
<p class="muted">A MusicBrainz ID tells two bands of one name apart.</p>
<p><a class="btn" href="#/admin/artists">Link them</a></p>`;
  return true;
}
