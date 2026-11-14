// #/admin/merge (Task 15): fold a duplicate artist or venue into the one to keep. Each kind has two pickers, "Keep"
// and "Duplicate", that search as you type; with both picked, a confirm line ("Merge Bellwether into The Bellwether?
// 3 shows move.") and Merge / Cancel. The server moves every show and keeps the duplicate's name as an alias or a
// former name. No DOM access at import, so node --test can load the helpers.
import { get, send } from "./api.js";
import { esc, placeLabel } from "./core.js";

const KINDS = {
  artists: { title: "Merge artists", one: "artist", placeholder: "Search artists", search: "/admin/api/artists", merge: "/admin/api/artists/merge" },
  venues: { title: "Merge venues", one: "venue", placeholder: "Search venues", search: "/admin/api/venues", merge: "/admin/api/venues/merge" },
};
const SLOTS = { keep: "Keep", drop: "Duplicate" };

const showCount = n => (n === 1 ? "1 show" : `${Number(n) || 0} shows`);
const moves = n => (!n ? "No shows move." : n === 1 ? "1 show moves." : `${n} shows move.`);

// "Merge Bellwether into The Bellwether? 3 shows move." (escaped: it goes into markup).
export function renderMergeConfirm(drop, keep) {
  return `Merge ${esc(drop.name)} into ${esc(keep.name)}? ${esc(moves(drop.shows))}`;
}

const meta = item => [item.city !== undefined ? placeLabel(item) : "", showCount(item.shows)].filter(Boolean).join(" · ");

// One search result: an artist {id, name, shows} or a venue {id, name, city, region, country, shows}.
export function renderPick(item) {
  return `<li><button type="button" class="merge-pick" data-id="${esc(item.id)}">
  <span class="merge-pick-name">${esc(item.name)}</span><span class="merge-pick-meta">${esc(meta(item))}</span>
</button></li>`;
}

const renderPicks = items => (items.length ? items.map(renderPick).join("") : `<li class="muted small">No match.</li>`);
const renderChosen = item => `<span class="muted">Picked:</span> ${esc(item.name)} <span class="muted">(${esc(meta(item))})</span>`;

const renderSlot = (kind, slot) => `<div class="merge-slot" data-slot="${esc(slot)}">
  <label class="merge-label">${esc(SLOTS[slot])}
    <input type="search" class="merge-search" autocomplete="off" data-slot="${esc(slot)}"
      placeholder="${esc(KINDS[kind].placeholder)}">
  </label>
  <ul class="merge-picks"></ul>
  <p class="merge-chosen small" hidden></p>
</div>`;

const renderSection = kind => `<section class="card merge" data-kind="${esc(kind)}">
  <h2>${esc(KINDS[kind].title)}</h2>
  ${renderSlot(kind, "keep")}
  ${renderSlot(kind, "drop")}
  <div class="merge-confirm" hidden>
    <p class="merge-question"></p>
    <div class="merge-actions">
      <button type="button" class="btn btn-primary" data-act="merge">Merge</button>
      <button type="button" class="btn" data-act="cancel">Cancel</button>
    </div>
  </div>
  <p class="merge-status small" role="status"></p>
</section>`;

const renderEditorsOnly = () => `<h1>Merge</h1>
<div class="notice"><p>Editors only.</p><p><a class="btn" href="/admin#/admin/merge">Sign in</a></p></div>`;

// One kind's two pickers and its confirm line.
function wire(section, kind, ctx) {
  const cfg = KINDS[kind];
  const picked = { keep: null, drop: null };
  const results = { keep: [], drop: [] };
  const asked = { keep: 0, drop: 0 };
  const timers = {};
  const slotEl = slot => section.querySelector(`.merge-slot[data-slot="${slot}"]`);
  const confirmEl = section.querySelector(".merge-confirm");
  const status = section.querySelector(".merge-status");

  function say(message, isError = false) {
    status.textContent = message;
    status.classList.toggle("error", isError);
  }

  function refresh() {
    const { keep, drop } = picked;
    const same = keep && drop && keep.id === drop.id;
    confirmEl.hidden = !(keep && drop) || same;
    if (same) say(`Pick two different ${cfg.one}s.`, true);
    else if (keep && drop) {
      say("");
      section.querySelector(".merge-question").innerHTML = renderMergeConfirm(drop, keep);
    }
  }

  async function search(slot, q) {
    const mine = ++asked[slot];
    const list = slotEl(slot).querySelector(".merge-picks");
    if (!q.trim()) {
      results[slot] = [];
      list.innerHTML = "";
      return;
    }
    try {
      const items = await get(`${cfg.search}?q=${encodeURIComponent(q)}`);
      if (!ctx.alive() || mine !== asked[slot]) return;
      results[slot] = items;
      list.innerHTML = renderPicks(items);
    } catch (e) {
      if (!ctx.alive() || mine !== asked[slot]) return;
      say(e.error ?? "Search failed. Try again.", true);
    }
  }

  function choose(slot, item) {
    picked[slot] = item;
    const el = slotEl(slot);
    el.querySelector(".merge-search").value = item.name;
    el.querySelector(".merge-picks").innerHTML = "";
    const chosen = el.querySelector(".merge-chosen");
    chosen.innerHTML = renderChosen(item);
    chosen.hidden = false;
    refresh();
  }

  function clear(slot) {
    picked[slot] = null;
    asked[slot]++;
    const el = slotEl(slot);
    el.querySelector(".merge-search").value = "";
    el.querySelector(".merge-picks").innerHTML = "";
    el.querySelector(".merge-chosen").hidden = true;
  }

  async function doMerge(button) {
    const { keep, drop } = picked;
    if (!keep || !drop) return;
    button.disabled = true;
    try {
      const kept = await send("POST", cfg.merge, { keep: keep.id, drop: drop.id });
      if (!ctx.alive()) return;
      clear("keep");
      clear("drop");
      confirmEl.hidden = true;
      say(`Merged ${drop.name} into ${kept.name}. It now has ${showCount(kept.shows)}.`);
    } catch (e) {
      if (!ctx.alive()) return;
      say(e.error ?? "The merge failed. Try again.", true);
    } finally {
      button.disabled = false;
    }
  }

  section.addEventListener("input", e => {
    const slot = e.target.dataset?.slot;
    if (!slot) return;
    picked[slot] = null;
    slotEl(slot).querySelector(".merge-chosen").hidden = true;
    confirmEl.hidden = true;
    say("");
    clearTimeout(timers[slot]);
    timers[slot] = setTimeout(() => search(slot, e.target.value), 200);
  });

  section.addEventListener("click", e => {
    const pick = e.target.closest(".merge-pick");
    if (pick) {
      const slot = pick.closest(".merge-slot").dataset.slot;
      const item = results[slot].find(r => String(r.id) === pick.dataset.id);
      if (item) choose(slot, item);
      return;
    }
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "merge") doMerge(e.target.closest("button"));
    else if (act === "cancel") {
      clear("drop");
      confirmEl.hidden = true;
      say("");
    }
  });
}

export async function renderMerge(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = renderEditorsOnly();
    return;
  }
  view.innerHTML = `<h1>Merge</h1>
<p class="muted">Fold a duplicate into the one to keep: its shows move, and its name stays findable as another
spelling or a former name.</p>
${renderSection("artists")}
${renderSection("venues")}`;
  for (const kind of Object.keys(KINDS)) wire(view.querySelector(`.merge[data-kind="${kind}"]`), kind, ctx);
}
