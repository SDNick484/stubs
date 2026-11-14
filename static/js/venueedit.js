// Venue editing (Task 20): #/admin/venue/<id>/edit. The venue's name and place, then every name it has had with the
// number of shows that carry it on the ticket (Remove, "Remove? Yes / No" inline, when none does), then "Add a former
// name". Each change is sent at once and the page redraws from the venue the server returns. The render* helpers
// touch no DOM at import (tests/venueedit.test.mjs).
import { ApiError, get, send } from "./api.js";
import { esc } from "./core.js";

const api = id => `/admin/api/venues/${encodeURIComponent(id)}`;
const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");

export const renderShowCount = n => (n === 1 ? "1 show" : `${n} shows`);

const renderMax = n => ` maxlength="${esc(n)}"`;
const renderField = (name, label, value, max) =>
  `<label>${esc(label)}<input type="text" name="${esc(name)}" value="${esc(value ?? "")}"${renderMax(max)}></label>`;

export const renderVenueForm = v => `<form class="venue-edit-form" novalidate>
${renderField("name", "Name", v.name, "200")}
${renderField("city", "City", v.city, "100")}
${renderField("region", "Region", v.region, "40")}
${renderField("country", "Country (two letters)", v.country, "2")}
<div class="venue-edit-actions"><button class="btn btn-primary" type="submit">Save</button></div>
</form>`;

const renderRemove = n => (n.shows > 0 ? "" : `<button class="btn" type="button" data-act="remove" data-name="${esc(n.name)}">Remove</button>`);

export const renderConfirmRemove = name => `<span class="venue-name-confirm">Remove? <button class="btn" type="button" data-act="remove-yes" data-name="${esc(name)}">Yes</button> <button class="btn" type="button" data-act="remove-no">No</button></span>`;

const renderCurrent = n => (n.current ? `<span class="tag">current</span>` : "");

export const renderNameRow = n => `<li class="venue-name" data-name="${esc(n.name)}"><span class="venue-name-text">${esc(n.name)}</span> ${renderCurrent(n)} <span class="muted">${esc(renderShowCount(n.shows))} on the ticket</span> <span class="venue-name-actions">${renderRemove(n)}</span></li>`;

const renderNameRows = names => names.map(renderNameRow).join("");
export const renderNames = names => `<ul class="venue-names">${renderNameRows(names)}</ul>`;

export const renderAddName = () => `<form class="venue-add-form" novalidate>
<label>Add a former name<input type="text" name="newname" maxlength="200" placeholder="A name this venue has had"></label>
<button class="btn" type="submit">Add</button>
</form>`;

export const renderEditPage = v => `<h1>Edit venue</h1>
<p><a href="#/venue/${esc(v.id)}">Back to the venue</a></p>
${renderVenueForm(v)}
<p class="muted venue-edit-msg" role="status"></p>
<p class="venue-locate" hidden>The place changed and the venue keeps its map position. <button class="btn" type="button" data-act="locate">Locate again</button></p>
<h2>Names</h2>
<p class="muted">Every name this venue has had. A show's ticket name is one of them.</p>
${renderNames(v.names)}
${renderAddName()}`;

// The body PATCH takes from the form's values: only what differs from the venue as loaded.
export function changedFields(v, form) {
  const out = {};
  for (const key of ["name", "city", "region", "country"]) {
    const typed = String(form[key] ?? "").trim();
    const now = key === "country" ? typed.toUpperCase() : typed;
    if (now !== String(v[key] ?? "")) out[key] = now || null;
  }
  return out;
}

export async function renderEditVenue(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Edit venue</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const id = ctx.params.id;
  let venue = await get(`${api(id)}/names`);
  if (!ctx.alive()) return;

  const say = text => { view.querySelector(".venue-edit-msg").textContent = text; };

  function draw(keepNote) {
    view.innerHTML = renderEditPage(venue);
    if (keepNote) view.querySelector(".venue-locate").hidden = false;
  }

  async function run(action, done) {
    try {
      const out = await action();
      if (!ctx.alive()) return;
      venue = out;
      draw(out.coordinates_kept === true);
      say(done);
    } catch (e) {
      if (ctx.alive()) say(message(e));
    }
  }

  draw(false);

  view.addEventListener("submit", ev => {
    ev.preventDefault();
    const f = ev.target;
    if (f.classList.contains("venue-add-form")) {
      const name = f.elements.newname.value.trim();
      if (!name) return say("Type a name to add.");
      return run(() => send("POST", `${api(id)}/names`, { name }), "Name added.");
    }
    const body = changedFields(venue, Object.fromEntries(new FormData(f)));
    if (!Object.keys(body).length) return say("Nothing changed.");
    return run(() => send("PATCH", api(id), body), "Saved.");
  });

  view.addEventListener("click", async ev => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const row = btn.closest(".venue-name");
    switch (btn.dataset.act) {
      case "remove":
        row.querySelector(".venue-name-actions").innerHTML = renderConfirmRemove(btn.dataset.name);
        break;
      case "remove-no":
        draw(false);
        break;
      case "remove-yes":
        await run(() => send("DELETE", `${api(id)}/names`, { name: btn.dataset.name }), "Name removed.");
        break;
      case "locate":
        try {
          say("Looking…");
          const v = await send("POST", `${api(id)}/locate`);
          if (ctx.alive()) say(v.lat === null ? "No match. Try the Not on the map page." : "Located.");
        } catch (e) {
          if (ctx.alive()) say(message(e));
        }
        break;
      default:
    }
  });
}

// The venue page under /admin gets an "Edit venue" link under its heading (Task 8's showVenue draws the page).
export async function showVenueWithEdit(showVenue, ctx) {
  await showVenue(ctx);
  if (!ctx.alive() || !ctx.session.editor || !location.pathname.startsWith("/admin")) return;
  const link = document.createElement("a");
  link.className = "btn";
  link.href = `#/admin/venue/${encodeURIComponent(ctx.params.id)}/edit`;
  link.textContent = "Edit venue";
  ctx.view.querySelector("h1")?.after(link);
}
