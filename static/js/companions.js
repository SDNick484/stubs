// Companions (editor only): the "With" panel on a show and the #/admin/people list. The render* functions are
// DOM-free (tests/companions.test.mjs); the panel and page wire events inside functions, never at import.
import { get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const renderPerson = (p, ticked) => `<label class="person-tick"><input type="checkbox" value="${esc(p.id)}"${esc(ticked.has(p.id) ? " checked" : "")}> <span>${esc(p.name)}</span></label>`;

const renderBoxes = (people, ticked) => people.map(p => renderPerson(p, ticked)).join("");

// The tick boxes for `people` ([{id, name}]), those whose id is in `selected` ticked.
export function renderCompanions(people, selected) {
  const ticked = new Set(selected);
  if (!people.length) return `<div class="person-ticks"><span class="muted">No one yet.</span></div>`;
  return `<div class="person-ticks">${renderBoxes(people, ticked)}</div>`;
}

const renderAdd = () => `<form class="person-add" data-form="add-person">
  <input type="text" name="name" maxlength="60" placeholder="Add person" aria-label="Add person" autocomplete="off">
  <button class="btn" type="submit">Add</button></form>`;
const renderStatus = message => `<span class="${esc(message ? "error" : "muted")}">${esc(message)}</span>`;

// #/admin/people rows: each person, with how many shows, and a button that lists them.
export function renderPeopleList(people) {
  if (!people.length) return `<p class="muted">No one tagged yet. Add people from a show page.</p>`;
  return `<ul class="people">${renderRows(people)}</ul>`;
}

const renderRows = people => people.map(p => `<li class="person-row" data-person="${esc(p.id)}">
  <button class="btn person-toggle" type="button" data-act="shows">${esc(p.name)}</button>
  <span class="muted">${esc(plural(p.shows, "show"))}</span>
  <ul class="person-shows" hidden></ul></li>`).join("");

export const renderPersonShows = shows => (shows.length
  ? shows.map(s => `<li><a href="#/show/${esc(s.id)}">${fmtDate(s.date, s.precision)} · ${esc(s.billing)}</a>
  <span class="muted">${esc(s.venue_label)}</span></li>`).join("")
  : `<li class="muted">No shows.</li>`);

// The "With" show panel: tick boxes saved on change, and an inline add-person form.
export async function renderWithPanel(el, show) {
  el.innerHTML = `<h2>With</h2><div class="with-body"></div><p class="with-status" role="status"></p>`;
  const body = el.querySelector(".with-body");
  const status = el.querySelector(".with-status");
  const [all, mine] = await Promise.all([get("/admin/api/people"), get(`/admin/api/shows/${show.id}/people`)]);
  let people = all.people;
  let selected = mine.people.map(p => p.id);
  const draw = () => {
    body.innerHTML = `${renderCompanions(people, selected)}${renderAdd()}`;
  };
  const say = message => {
    status.innerHTML = renderStatus(message);
  };
  const save = async ids => {
    try {
      const r = await send("PUT", `/admin/api/shows/${show.id}/people`, { person_ids: ids });
      selected = r.people.map(p => p.id);
      say("");
    } catch (e) {
      say(e?.error ?? "Couldn't save. Try again.");
    }
  };
  draw();
  body.addEventListener("change", async e => {
    if (e.target.type !== "checkbox") return;
    const ids = [...body.querySelectorAll("input[type=checkbox]:checked")].map(i => Number(i.value));
    await save(ids);
    draw();
  });
  body.addEventListener("submit", async e => {
    e.preventDefault();
    const name = body.querySelector("input[name=name]").value.trim();
    if (!name) return;
    try {
      const p = await send("POST", "/admin/api/people", { name });
      if (!people.some(x => x.id === p.id)) people = [...people, { id: p.id, name: p.name, shows: 0 }];
      await save([...new Set([...selected, p.id])]);
      draw();
    } catch (err) {
      say(err?.error ?? "Couldn't add. Try again.");
    }
  });
}

// #/admin/people
export async function renderPeoplePage(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>People</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const { people } = await get("/admin/api/people");
  if (!ctx.alive()) return;
  view.innerHTML = `<h1>People</h1><p><a href="#/admin">Admin</a></p>${renderPeopleList(people)}`;
  view.addEventListener("click", async e => {
    const row = e.target.closest?.("[data-act=shows]")?.closest(".person-row");
    if (!row) return;
    const list = row.querySelector(".person-shows");
    if (!list.hidden) {
      list.hidden = true;
      return;
    }
    try {
      const { shows } = await get(`/admin/api/people/${row.dataset.person}/shows`);
      list.innerHTML = renderPersonShows(shows);
    } catch (err) {
      list.innerHTML = `<li class="error">${esc(err?.error ?? "Couldn't load.")}</li>`;
    }
    list.hidden = false;
  });
}
