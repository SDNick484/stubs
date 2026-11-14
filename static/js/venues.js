// Venue coordinates (Task 16): #/admin/venues (venues without coordinates; Locate one, Locate all missing, enter by
// hand) and the admin card "Not on the map (N)". No DOM at import, so node --test can load the helpers.
import { ApiError, get, send } from "./api.js";
import { esc } from "./core.js";

export const renderProgress = (done, total) => `${done} of ${total}…`;

// A number within [min, max] from what was typed, or null.
export function parseCoord(raw, min, max) {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

const countLabel = n => `${n} ${n === 1 ? "show" : "shows"}`;

const renderVenueRow = v => `<li class="venue-row" data-id="${esc(v.id)}">
<div class="venue-head"><strong>${esc(v.name)}</strong> <span class="muted">${esc(v.city)}</span>
<span class="tag">${esc(countLabel(v.shows))}</span></div>
<div class="venue-actions"><button class="btn" type="button" data-act="locate">Locate</button>
<input type="text" inputmode="decimal" name="lat" placeholder="Latitude" aria-label="Latitude">
<input type="text" inputmode="decimal" name="lon" placeholder="Longitude" aria-label="Longitude">
<button class="btn" type="button" data-act="save">Save</button></div>
<p class="muted venue-msg" role="status"></p></li>`;

const renderVenueRows = list => list.map(renderVenueRow).join("");

const renderVenues = list => `<h1>Not on the map</h1>
<p class="muted">Venues without coordinates. Locate asks OpenStreetMap, at most once a second; or type them in.</p>
<p><button class="btn btn-primary" type="button" data-act="locate-all">Locate all missing</button>
<span class="muted" data-role="progress" role="status"></span></p>
<ul class="venue-list">${renderVenueRows(list)}</ul>
<p class="muted empty" hidden>Every venue is on the map.</p>`;

const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");

export async function renderVenuesPage(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Not on the map</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const { venues } = await get("/admin/api/venues/missing-coords");
  if (!ctx.alive()) return;
  view.innerHTML = renderVenues(venues);
  const empty = view.querySelector(".empty");
  const progress = view.querySelector("[data-role=progress]");
  const allBtn = view.querySelector("[data-act=locate-all]");
  const sync = () => {
    empty.hidden = view.querySelector(".venue-row") !== null;
    allBtn.hidden = !empty.hidden;
  };
  sync();

  // Locates one row; true when it found coordinates, false on a miss, throws on a failure.
  async function locate(li) {
    const msg = li.querySelector(".venue-msg");
    msg.textContent = "Looking…";
    const v = await send("POST", `/admin/api/venues/${li.dataset.id}/locate`);
    if (!ctx.alive()) return false;
    if (v.lat === null) {
      msg.textContent = "No match. Try typing the coordinates.";
      return false;
    }
    li.remove();
    sync();
    return true;
  }

  async function locateAll() {
    const rows = [...view.querySelectorAll(".venue-row")];
    allBtn.disabled = true;
    let done = 0;
    progress.textContent = renderProgress(done, rows.length);
    for (const li of rows) {
      try {
        await locate(li);
      } catch (e) {
        if (!ctx.alive()) return;
        li.querySelector(".venue-msg").textContent = message(e);
      }
      if (!ctx.alive()) return;
      done += 1;
      progress.textContent = renderProgress(done, rows.length);
    }
    progress.textContent = "";
    allBtn.disabled = false;
  }

  async function save(li) {
    const msg = li.querySelector(".venue-msg");
    const lat = parseCoord(li.querySelector("[name=lat]").value, -90, 90);
    const lon = parseCoord(li.querySelector("[name=lon]").value, -180, 180);
    if (lat === null || lon === null) {
      msg.textContent = "Latitude is -90 to 90 and longitude -180 to 180.";
      return;
    }
    await send("PUT", `/admin/api/venues/${li.dataset.id}/coords`, { lat, lon });
    if (!ctx.alive()) return;
    li.remove();
    sync();
  }

  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    const act = btn?.dataset.act;
    const li = btn?.closest(".venue-row");
    try {
      if (act === "locate-all") await locateAll();
      else if (act === "locate" && li) await locate(li);
      else if (act === "save" && li) await save(li);
    } catch (err) {
      if (li && ctx.alive()) li.querySelector(".venue-msg").textContent = message(err);
    }
  });
}

// Admin card "Not on the map (N)": hidden when every venue has coordinates.
export async function renderMissingCard(el) {
  const { venues } = await get("/admin/api/venues/missing-coords");
  if (!venues.length) return false;
  el.innerHTML = `<h2>Not on the map (${esc(venues.length)})</h2>
<p class="muted">These venues have no coordinates yet.</p>
<p><a class="btn" href="#/admin/venues">Locate them</a></p>`;
  return true;
}
