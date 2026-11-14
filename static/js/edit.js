// Show editing (Task 11, spec 7.8): #/admin/show/new, #/admin/show/<id>/edit and the editor-only "Edit" show panel.
// One form holds the show's fields and its lineup; Save sends only what changed (PATCH the fields, PUT the lineup),
// each with the version the form was loaded with. A stale save (409) leaves the form as typed under the message and a
// Reload button. Sources are added and removed at once, outside the form; deleting the show asks "Delete? Yes / No"
// inline. collectLineup, collectShow, moveRow and the render* helpers touch no DOM (tests/edit.test.mjs).
import { get, send } from "./api.js";
import { esc, placeLabel } from "./core.js";

const ROLES = [["headliner", "Headliner"], ["coheadliner", "Co-headliner"], ["opener", "Opener"], ["performer", "Performer"]];
const STATUSES = [["attended", "Attended"], ["upcoming", "Upcoming"]];
const STARS = [["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]];
const RATINGS = [["", "No rating"], ...STARS];
const ACT_RATINGS = [["", "Rating"], ...STARS];

// ---------------------------------------------------------------- form state -> request bodies (no DOM)
const str = v => String(v ?? "").trim();
const orNull = v => str(v) || null;
const ratingOf = v => (str(v) ? Number(v) : null);

// formState.lineup: rows as read from the form ({id, name, role, likely, note, rating}, all strings but likely) ->
// the entries PUT /admin/api/shows/<id>/lineup takes, in the same order. A row without a name is left out; a row
// with an id keeps it (as a number), so the server updates that entry instead of making a new one.
export function collectLineup(formState) {
  return (formState?.lineup ?? []).filter(r => str(r.name)).map(r => {
    const entry = str(r.id) ? { id: Number(r.id) } : {};
    return Object.assign(entry, {
      name: str(r.name), role: r.role, confidence: r.likely ? "likely" : "confirmed",
      confidence_note: orNull(r.note), rating: ratingOf(r.rating),
    });
  });
}

// The show's fields as PATCH /admin/api/shows/<id> and POST /admin/api/shows take them: a picked venue by id, else
// a new venue by name and place. Empty text is null.
export function collectShow(s) {
  const fields = { status: s.status, date: str(s.date), billing: str(s.billing) };
  if (str(s.venueId)) fields.venue_id = Number(s.venueId);
  else {
    Object.assign(fields, { venue: orNull(s.newVenue), city: orNull(s.city), region: orNull(s.region),
      country: orNull(s.country)?.toUpperCase() ?? null });
  }
  return Object.assign(fields, {
    venue_label: orNull(s.venueLabel), run: orNull(s.run), tour: orNull(s.tour), notes: orNull(s.notes),
    rating: ratingOf(s.rating), confidence: s.likely ? "likely" : "confirmed", confidence_note: orNull(s.confidenceNote),
  });
}

// A copy of rows with rows[i] and rows[i + step] swapped; unchanged when that would leave the list.
export function moveRow(rows, i, step) {
  const to = i + step;
  const out = [...rows];
  if (i < 0 || i >= rows.length || to < 0 || to >= rows.length) return out;
  [out[i], out[to]] = [out[to], out[i]];
  return out;
}

const blankRow = role => ({ id: "", name: "", role, likely: false, note: "", rating: "" });
const ratingText = r => (r === null || r === undefined ? "" : String(r));

function stateFromShow(show) {
  return {
    status: show.status, date: show.date, billing: show.billing, venueId: String(show.venue.id),
    venueLabel: show.venue.label ?? "", newVenue: "", city: "", region: "", country: "", run: show.run ?? "",
    tour: show.tour ?? "", notes: show.notes ?? "", rating: ratingText(show.rating),
    likely: show.confidence === "likely", confidenceNote: show.confidence_note ?? "",
    lineup: show.lineup.map(e => ({ id: String(e.id), name: e.name, role: e.role, likely: e.confidence === "likely",
      note: e.confidence_note ?? "", rating: ratingText(e.rating) })),
  };
}

const blankState = () => ({
  status: "attended", date: "", billing: "", venueId: "", venueLabel: "", newVenue: "", city: "", region: "",
  country: "", run: "", tour: "", notes: "", rating: "", likely: false, confidenceNote: "", lineup: [blankRow("headliner")],
});

// ---------------------------------------------------------------- markup (no DOM)
const renderOption = ([value, label], current) => `<option value="${esc(value)}"${esc(String(current ?? "") === value ? " selected" : "")}>${esc(label)}</option>`;
const renderOptions = (list, current) => list.map(o => renderOption(o, current)).join("");
const renderChecked = on => esc(on ? " checked" : "");
const renderDisabled = off => esc(off ? " disabled" : "");

// One lineup row of the form. `row` is {id, name, role, likely, note, rating}; index and count place its arrows.
export function renderLineupRow(row, index, count) {
  return `<li class="lineup-row" data-id="${esc(row.id)}">
  <div class="lineup-main">
    <input name="name" value="${esc(row.name)}" placeholder="Act" aria-label="Act" maxlength="200" autocomplete="off">
    <select name="role" aria-label="Role">${renderOptions(ROLES, row.role)}</select>
  </div>
  <div class="lineup-more">
    <label class="edit-check"><input type="checkbox" name="likely"${renderChecked(row.likely)}> Likely</label>
    <input name="note" value="${esc(row.note)}" placeholder="Note" aria-label="Note" maxlength="500">
    <select name="rating" aria-label="Rating">${renderOptions(ACT_RATINGS, row.rating)}</select>
  </div>
  <div class="lineup-actions">
    <button class="btn" type="button" data-act="up" aria-label="Move up"${renderDisabled(index === 0)}>&uarr;</button>
    <button class="btn" type="button" data-act="down" aria-label="Move down"${renderDisabled(index === count - 1)}>&darr;</button>
    <button class="btn" type="button" data-act="remove">Remove</button>
  </div>
</li>`;
}

const renderLineupRows = rows => rows.map((r, i) => renderLineupRow(r, i, rows.length)).join("");

const renderReloadIf = stale => (stale ? `<button class="btn" type="button" data-act="reload">Reload</button>` : "");

// The message under which a failed save leaves the form; a stale save (409) also gets a Reload button.
export function renderSaveError(err) {
  return `<div class="notice error edit-error" role="alert"><p>${esc(err?.error ?? "Couldn't save. Try again.")}</p>${renderReloadIf(err?.status === 409)}</div>`;
}

const renderVenueText = venue => (venue
  ? `<strong>${esc(venue.label ?? venue.name)}</strong> <span class="muted">${esc(placeLabel(venue))}</span>`
  : `<span class="muted">No venue picked yet.</span>`);

const renderVenueOption = v => `<li><button class="venue-option" type="button" data-id="${esc(v.id)}">
  <span class="venue-option-name">${esc(v.name)}</span><span class="muted small">${esc(placeLabel(v))}</span></button></li>`;
const renderVenueOptions = list => (list.length ? list.map(renderVenueOption).join("") : `<li class="muted small">No match. Try New venue.</li>`);

const renderDatalist = (id, names) => `<datalist id="${esc(id)}">${renderNameOptions(names)}</datalist>`;
const renderNameOptions = names => names.map(n => `<option value="${esc(n)}"></option>`).join("");

const renderVenuePicker = (s, venue) => `<fieldset class="edit-group venue-pick">
  <legend>Venue</legend>
  <input type="hidden" name="venue_id" value="${esc(s.venueId)}">
  <p class="venue-chosen">${renderVenueText(venue)}</p>
  <div class="venue-find">
    <input type="search" class="venue-search" placeholder="Search venues" aria-label="Search venues" autocomplete="off">
    <button class="btn" type="button" data-act="new-venue">New venue</button>
  </div>
  <ul class="venue-results"></ul>
  <div class="venue-new" hidden>
    <label>Name <input name="new_venue" maxlength="200" autocomplete="off"></label>
    <label>City <input name="city" maxlength="100" autocomplete="off"></label>
    <div class="edit-pair">
      <label>State or province <input name="region" maxlength="40" placeholder="CA" autocomplete="off"></label>
      <label>Country <input name="country" maxlength="2" placeholder="US" autocomplete="off"></label>
    </div>
  </div>
  <label>Name on the ticket <input name="venue_label" value="${esc(s.venueLabel)}" maxlength="200" list="edit-venue-names" autocomplete="off"></label>
  ${renderDatalist("edit-venue-names", [])}
</fieldset>`;

const renderShowFields = s => `<fieldset class="edit-group">
  <legend>Show</legend>
  <div class="edit-pair">
    <label>Status <select name="status">${renderOptions(STATUSES, s.status)}</select></label>
    <label>Date <input name="date" value="${esc(s.date)}" placeholder="2025-07-19" inputmode="numeric" autocomplete="off" required></label>
  </div>
  <p class="muted small">A date can be a day (2025-07-19), a month (2025-07) or a year (2027).</p>
  <label>Billing <input name="billing" value="${esc(s.billing)}" maxlength="200" autocomplete="off" required></label>
</fieldset>`;

const renderMoreFields = (s, runs) => `<fieldset class="edit-group">
  <legend>Details</legend>
  <label>Run <input name="run" value="${esc(s.run)}" maxlength="200" list="edit-runs" placeholder="Harbor Days 2009" autocomplete="off"></label>
  ${renderDatalist("edit-runs", runs.map(r => r.name))}
  <label>Tour <input name="tour" value="${esc(s.tour)}" maxlength="200" autocomplete="off"></label>
  <label>Notes <textarea name="notes" rows="3" maxlength="5000">${esc(s.notes)}</textarea></label>
  <label>Rating <select name="rating">${renderOptions(RATINGS, s.rating)}</select></label>
  <label class="edit-check"><input type="checkbox" name="show_likely"${renderChecked(s.likely)}> Likely (not confirmed yet)</label>
  <label>Confidence note <input name="confidence_note" value="${esc(s.confidenceNote)}" maxlength="500" autocomplete="off"></label>
</fieldset>`;

const renderLineupFields = rows => `<fieldset class="edit-group">
  <legend>Lineup</legend>
  <ol class="lineup-rows">${renderLineupRows(rows)}</ol>
  <p><button class="btn" type="button" data-act="add-act">+ Add act</button></p>
</fieldset>`;

const renderSaveLabel = isNew => esc(isNew ? "Create show" : "Save");

// The whole form. `model` is {isNew, state, venue, runs}.
export function renderEditForm({ isNew, state, venue, runs }) {
  return `<form class="edit-form" novalidate>
  ${renderShowFields(state)}
  ${renderVenuePicker(state, venue)}
  ${renderLineupFields(state.lineup)}
  ${renderMoreFields(state, runs)}
  <div class="edit-errors"></div>
  <div class="edit-save">
    <button class="btn btn-primary" type="submit">${renderSaveLabel(isNew)}</button>
    <p class="edit-status" role="status"></p>
  </div>
</form>`;
}

// ---------------------------------------------------------------- sources (no DOM)
const safeUrl = url => (/^https?:\/\//i.test(String(url ?? "")) ? url : null);
const renderSourceLink = url => (safeUrl(url) ? ` <a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">link</a>` : "");
const renderFor = name => (name ? ` <span class="muted small">for ${esc(name)}</span>` : "");
const renderSourceButtons = (src, confirming) => (confirming
  ? `<span class="edit-confirm">Delete? <button class="btn" type="button" data-act="delete-yes">Yes</button> <button class="btn" type="button" data-act="delete-no">No</button></span>`
  : `<button class="btn" type="button" data-act="delete" aria-label="Remove source ${esc(src.label)}">Remove</button>`);

const renderSourceRow = (src, confirming) => `<li class="source-row" data-id="${esc(src.id)}">
  <span class="source-text">${esc(src.label)}${renderSourceLink(src.url)}${renderFor(src.forName)}</span>
  ${renderSourceButtons(src, confirming)}
</li>`;

// Every source of a show detail: the show's own first, then each lineup entry's, with whom it backs.
export function allSources(show) {
  const own = (show.sources ?? []).map(s => ({ ...s, forName: null }));
  const acts = (show.lineup ?? []).flatMap(e => (e.sources ?? []).map(s => ({ ...s, forName: e.name })));
  return [...own, ...acts];
}

export function renderSources(show, confirmId) {
  const list = allSources(show);
  if (!list.length) return `<li class="muted">No sources yet.</li>`;
  return list.map(s => renderSourceRow(s, s.id === confirmId)).join("");
}

const renderBacksOptions = show => [["", "The show"], ...(show.lineup ?? []).map(e => [String(e.id), e.name])]
  .map(o => renderOption(o, "")).join("");

const renderSourcesCard = show => `<section class="card edit-sources">
  <h2>Sources</h2>
  <ul class="source-list">${renderSources(show, null)}</ul>
  <form class="source-add">
    <label>Label <input name="label" maxlength="200" placeholder="Ticketmaster" autocomplete="off" required></label>
    <label>Link <input name="url" type="url" maxlength="2000" placeholder="https://" autocomplete="off"></label>
    <label>Backs <select name="lineup_id">${renderBacksOptions(show)}</select></label>
    <p class="error source-error" role="alert" hidden></p>
    <p><button class="btn" type="submit">Add source</button></p>
  </form>
</section>`;

const renderDeleteAsk = () => `<button class="btn btn-danger" type="button" data-act="delete">Delete this show</button>`;
const renderDeleteConfirm = () => `<span class="edit-confirm">Delete? <button class="btn btn-danger" type="button" data-act="delete-yes">Yes</button> <button class="btn" type="button" data-act="delete-no">No</button></span>`;

const renderDeleteCard = () => `<section class="card edit-delete">
  <h2>Delete</h2>
  <p class="muted small">The show goes with its lineup, sources, pictures and companions. Artists and the venue stay.</p>
  <div class="delete-zone">${renderDeleteAsk()}</div>
  <p class="error delete-error" role="alert" hidden></p>
</section>`;

const renderEditorsOnly = (title, hash) => `<h1>${esc(title)}</h1>
<div class="notice"><p>Editors only.</p><p><a class="btn" href="/admin${esc(hash)}">Sign in</a></p></div>`;

const renderBack = id => `<p><a href="#/show/${esc(id)}">Back to the show</a></p>`;

// ---------------------------------------------------------------- the form, wired (DOM)
function readRow(li) {
  const q = name => li.querySelector(`[name="${name}"]`);
  return { id: li.dataset.id ?? "", name: q("name").value, role: q("role").value, likely: q("likely").checked,
    note: q("note").value, rating: q("rating").value };
}

const readLineup = form => [...form.querySelectorAll(".lineup-row")].map(readRow);

function readForm(form) {
  const v = name => form.elements.namedItem(name)?.value ?? "";
  return {
    status: v("status"), date: v("date"), billing: v("billing"), venueId: v("venue_id"), newVenue: v("new_venue"),
    city: v("city"), region: v("region"), country: v("country"), venueLabel: v("venue_label"), run: v("run"),
    tour: v("tour"), notes: v("notes"), rating: v("rating"), likely: form.elements.namedItem("show_likely").checked,
    confidenceNote: v("confidence_note"), lineup: readLineup(form),
  };
}

// The venue picker inside a form: search as you type (GET /admin/api/venues?q=), pick one, or open "New venue".
function wireVenuePicker(form, ctx) {
  const box = form.querySelector(".venue-pick");
  const search = box.querySelector(".venue-search");
  const results = box.querySelector(".venue-results");
  const chosen = box.querySelector(".venue-chosen");
  const fresh = box.querySelector(".venue-new");
  const names = box.querySelector("datalist");
  let found = [];
  let asked = 0;
  let timer = null;

  const loadNames = async id => {
    names.innerHTML = "";
    if (!id) return;
    try {
      const venue = await get(`/admin/api/venues/${encodeURIComponent(id)}`);
      if (ctx.alive()) names.innerHTML = renderNameOptions(venue.names ?? []);
    } catch {
      // The suggestions are a convenience; the field still takes any name.
    }
  };

  const find = async q => {
    const mine = ++asked;
    if (!q.trim()) {
      results.innerHTML = "";
      return;
    }
    try {
      const list = await get(`/admin/api/venues?q=${encodeURIComponent(q)}`);
      if (!ctx.alive() || mine !== asked) return;
      found = list;
      results.innerHTML = renderVenueOptions(list);
    } catch (e) {
      if (ctx.alive() && mine === asked) results.innerHTML = `<li class="error small">${esc(e?.error ?? "Search failed.")}</li>`;
    }
  };

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => find(search.value), 200);
  });
  search.addEventListener("keydown", e => {
    if (e.key === "Enter") e.preventDefault(); // Enter here searches; it doesn't save the form
  });
  box.addEventListener("click", e => {
    const option = e.target.closest(".venue-option");
    if (option) {
      const venue = found.find(v => String(v.id) === option.dataset.id);
      if (!venue) return;
      form.elements.namedItem("venue_id").value = String(venue.id);
      form.elements.namedItem("venue_label").value = venue.name;
      for (const name of ["new_venue", "city", "region", "country"]) form.elements.namedItem(name).value = "";
      fresh.hidden = true;
      chosen.innerHTML = renderVenueText(venue);
      search.value = "";
      results.innerHTML = "";
      asked++;
      loadNames(venue.id);
    } else if (e.target.closest("[data-act=new-venue]")) {
      form.elements.namedItem("venue_id").value = "";
      form.elements.namedItem("venue_label").value = "";
      chosen.innerHTML = `<span class="muted">A new venue:</span>`;
      names.innerHTML = "";
      fresh.hidden = false;
      results.innerHTML = "";
      form.elements.namedItem("new_venue").focus();
    }
  });
  loadNames(form.elements.namedItem("venue_id").value);
}

// Add, move and remove lineup rows. Rows are read back from the form first, so typed text stays.
function wireLineup(form) {
  const list = form.querySelector(".lineup-rows");
  const redraw = rows => {
    list.innerHTML = renderLineupRows(rows);
  };
  form.addEventListener("click", e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "add-act") {
      const rows = readLineup(form);
      redraw([...rows, blankRow(rows.length ? "opener" : "headliner")]);
      list.querySelector(".lineup-row:last-child input[name=name]")?.focus();
      return;
    }
    const li = e.target.closest(".lineup-row");
    if (!li || !["up", "down", "remove"].includes(act)) return;
    const rows = readLineup(form);
    const i = [...list.children].indexOf(li);
    if (act === "remove") redraw(rows.filter((_, n) => n !== i));
    else redraw(moveRow(rows, i, act === "up" ? -1 : 1));
  });
}

async function loadRuns() {
  try {
    return await get("/admin/api/runs");
  } catch {
    return [];
  }
}

function venueOf(show) {
  return { label: show.venue.label, city: show.venue.city, region: show.venue.region, country: show.venue.country };
}

// The sources card: add (POST) and remove (DELETE, after an inline Yes / No) at once; the list redraws from the
// server. These never touch the show's version, so the form above stays saveable.
function wireSources(card, showId, ctx) {
  const list = card.querySelector(".source-list");
  const form = card.querySelector(".source-add");
  const error = card.querySelector(".source-error");
  let show = null;
  let confirmId = null;
  const fail = e => {
    error.textContent = e?.error ?? "That didn't work. Try again.";
    error.hidden = false;
  };
  const refresh = async () => {
    show = await get(`/admin/api/shows/${showId}`);
    if (!ctx.alive()) return;
    list.innerHTML = renderSources(show, confirmId);
    form.elements.namedItem("lineup_id").innerHTML = renderBacksOptions(show);
  };
  const repaint = () => {
    if (show) list.innerHTML = renderSources(show, confirmId);
  };
  card.addEventListener("click", async e => {
    const btn = e.target.closest("[data-act]");
    const row = btn?.closest(".source-row");
    if (!row) return;
    error.hidden = true;
    const id = Number(row.dataset.id);
    try {
      if (btn.dataset.act === "delete") {
        if (!show) show = await get(`/admin/api/shows/${showId}`);
        confirmId = id;
        repaint();
      } else if (btn.dataset.act === "delete-no") {
        confirmId = null;
        repaint();
      } else if (btn.dataset.act === "delete-yes") {
        await send("DELETE", `/admin/api/sources/${id}`);
        confirmId = null;
        await refresh();
      }
    } catch (err) {
      fail(err);
    }
  });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    error.hidden = true;
    const lineupId = form.elements.namedItem("lineup_id").value;
    const body = { label: form.elements.namedItem("label").value, url: form.elements.namedItem("url").value || null,
      lineup_id: lineupId ? Number(lineupId) : null };
    try {
      await send("POST", `/admin/api/shows/${showId}/sources`, body);
      form.reset();
      await refresh();
    } catch (err) {
      fail(err);
    }
  });
  return { refresh };
}

function wireDelete(card, showId, ctx) {
  const zone = card.querySelector(".delete-zone");
  const error = card.querySelector(".delete-error");
  card.addEventListener("click", async e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "delete") zone.innerHTML = renderDeleteConfirm();
    else if (act === "delete-no") zone.innerHTML = renderDeleteAsk();
    else if (act === "delete-yes") {
      try {
        await send("DELETE", `/admin/api/shows/${showId}`);
        if (ctx.alive()) location.hash = "#/";
      } catch (err) {
        zone.innerHTML = renderDeleteAsk();
        error.textContent = err?.error ?? "Couldn't delete. Try again.";
        error.hidden = false;
      }
    }
  });
}

// Draw the edit form for `show` (a GET /admin/api/shows/<id> answer) into `host` and wire it. Saving redraws it from
// the server's answer, so new lineup entries get their ids and the form carries the new version.
function drawEditForm(host, show, runs, ctx, sources, message = "") {
  const state = stateFromShow(show);
  host.innerHTML = renderEditForm({ isNew: false, state, venue: venueOf(show), runs });
  const form = host.querySelector(".edit-form");
  const status = form.querySelector(".edit-status");
  const errors = form.querySelector(".edit-errors");
  status.textContent = message;
  let version = show.version;
  let savedFields = JSON.stringify(collectShow(state));
  const savedLineup = JSON.stringify(collectLineup(state));
  wireVenuePicker(form, ctx);
  wireLineup(form);
  form.addEventListener("click", e => {
    if (e.target.closest("[data-act=reload]")) ctx.rerender();
  });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const now = readForm(form);
    const fields = collectShow(now);
    const lineup = collectLineup(now);
    errors.innerHTML = "";
    status.textContent = "";
    const fieldsChanged = JSON.stringify(fields) !== savedFields;
    const lineupChanged = JSON.stringify(lineup) !== savedLineup;
    if (!fieldsChanged && !lineupChanged) {
      status.textContent = "Nothing to save.";
      return;
    }
    button.disabled = true;
    let fresh = null;
    try {
      if (fieldsChanged) {
        fresh = await send("PATCH", `/admin/api/shows/${show.id}`, { version, ...fields });
        version = fresh.version;
        savedFields = JSON.stringify(fields);
      }
      if (lineupChanged) fresh = await send("PUT", `/admin/api/shows/${show.id}/lineup`, { version, lineup });
      if (!ctx.alive()) return;
      drawEditForm(host, fresh, runs, ctx, sources, "Saved.");
      sources?.refresh().catch(() => {}); // the list is a convenience here; the save itself is done
    } catch (err) {
      if (!ctx.alive()) return;
      errors.innerHTML = renderSaveError(err);
      button.disabled = false;
    }
  });
}

// #/admin/show/<id>/edit
export async function renderEditShow(ctx) {
  const { view, params, session } = ctx;
  if (!session.editor) {
    view.innerHTML = renderEditorsOnly("Edit show", `#/admin/show/${params.id}/edit`);
    return;
  }
  const [show, runs] = await Promise.all([get(`/admin/api/shows/${encodeURIComponent(params.id)}`), loadRuns()]);
  if (!ctx.alive()) return;
  view.innerHTML = `<h1>Edit show</h1>${renderBack(show.id)}<div class="edit-host"></div>${renderSourcesCard(show)}${renderDeleteCard()}`;
  const sources = wireSources(view.querySelector(".edit-sources"), show.id, ctx);
  wireDelete(view.querySelector(".edit-delete"), show.id, ctx);
  drawEditForm(view.querySelector(".edit-host"), show, runs, ctx, sources);
}

// #/admin/show/new
export async function renderNewShow(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = renderEditorsOnly("New show", "#/admin/show/new");
    return;
  }
  const runs = await loadRuns();
  if (!ctx.alive()) return;
  view.innerHTML = `<h1>New show</h1>${renderEditForm({ isNew: true, state: blankState(), venue: null, runs })}`;
  const form = view.querySelector(".edit-form");
  const errors = form.querySelector(".edit-errors");
  wireVenuePicker(form, ctx);
  wireLineup(form);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const now = readForm(form);
    const body = collectShow(now);
    const lineup = collectLineup(now);
    if (lineup.length) body.lineup = lineup; // none typed: the billing is the headliner
    errors.innerHTML = "";
    button.disabled = true;
    try {
      const { id } = await send("POST", "/admin/api/shows", body);
      if (ctx.alive()) location.hash = `#/show/${id}`;
    } catch (err) {
      if (!ctx.alive()) return;
      errors.innerHTML = renderSaveError(err);
      button.disabled = false;
    }
  });
}

// The "Edit" show panel (editors only, order 0): a link to this show's edit form.
export async function renderEditPanel(el, show) {
  el.innerHTML = `<p class="edit-panel"><a class="btn" href="#/admin/show/${esc(show.id)}/edit">Edit show</a></p>`;
}
