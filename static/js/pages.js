// #/artist/<id>, #/venue/<id>, #/year/<yyyy> and #/city/<country>/<region>/<city>: a summary over a list of shows, each
// row a renderShowRow. Pages under /admin read /admin/api/ (editors also see upcoming shows, first); the rest read
// /api/. No DOM at import.
import { get } from "./api.js";
import { esc, fmtDate, placeLabel, renderShowRow, roleLabel } from "./core.js";
import { cityHref } from "./stats.js";

const ROLE_ORDER = ["headliner", "coheadliner", "opener", "performer"];

const apiBase = () => (location.pathname.startsWith("/admin") ? "/admin/api" : "/api");

const TIMES = { 0: "Not seen yet", 1: "Seen once", 2: "Seen twice" };
const timesLabel = n => TIMES[n] ?? `Seen ${n} times`;

const countLabel = n => (n === 1 ? "1 show" : n + " shows");
const renderPart = html => "<span>" + html + "</span>";
const renderDated = (word, date) => (date ? renderPart(`${esc(word)} ${fmtDate(date)}`) : "");

const renderRoleCount = (role, n) => `<li>${esc(roleLabel(role))} <span class="muted">${esc(n)}</span></li>`;
const renderRoleList = items => `<ul class="role-counts">` + items + "</ul>";
const renderRoleCounts = byRole => {
  const roles = ROLE_ORDER.filter(r => byRole && byRole[r] > 0);
  return roles.length ? renderRoleList(roles.map(r => renderRoleCount(r, byRole[r])).join("")) : "";
};

// "Seen 3 times · first Apr 2, 2005 · last Dec 1, 2016" and the count for each role that is not zero.
export function renderArtistSummary(a) {
  return `<p class="summary">${renderPart(esc(timesLabel(a.times)))}${renderDated("first", a.first)}${renderDated("last", a.last)}</p>
${renderRoleCounts(a.by_role)}`;
}

const renderCityLink = v =>
  v.city ? `<a href="${esc(cityHref(v))}">${esc(placeLabel(v))}</a>` : "";

const renderAka = names => (names.length > 1 ? `<p class="aka muted">Also known as ${esc(names.slice(1).join(", "))}</p>` : "");

// City, times seen, and "Also known as" with the former names (the first of `names` is the current one).
export function renderVenueSummary(v) {
  return `<p class="summary">${renderPart(renderCityLink(v))}${renderPart(esc(timesLabel(v.times)))}</p>
${renderAka(v.names ?? [v.name])}`;
}

const renderRows = rows => rows.map(renderShowRow).join("\n");
const renderList = rows => (rows.length ? `<ul class="show-list">${renderRows(rows)}</ul>` : "");
const renderUpcomingList = rows =>
  rows && rows.length ? `<section><h2>Coming up</h2>${renderList(rows)}</section>` : "";
const renderPast = (rows, empty) => (rows.length ? renderList(rows) : `<p class="muted">${esc(empty)}</p>`);

const renderHeading = title => `<h1>${esc(title)}</h1>`;
const renderPage = (title, summary, d, empty) =>
  [renderHeading(title), summary, renderUpcomingList(d.upcoming), renderPast(d.shows, empty)].join("\n");

export async function showArtist(ctx) {
  const a = await get(`${apiBase()}/artists/${encodeURIComponent(ctx.params.id)}`);
  if (!ctx.alive()) return;
  ctx.view.innerHTML = renderPage(a.name, renderArtistSummary(a), a, "No shows yet.");
}

export async function showVenue(ctx) {
  const v = await get(`${apiBase()}/venues/${encodeURIComponent(ctx.params.id)}`);
  if (!ctx.alive()) return;
  ctx.view.innerHTML = renderPage(v.name, renderVenueSummary(v), v, "No shows yet.");
}

export async function showYear(ctx) {
  const year = ctx.params.yyyy;
  const rows = await get(`${apiBase()}/shows?year=${encodeURIComponent(year)}`);
  if (!ctx.alive()) return;
  const upcoming = rows.filter(r => r.status === "upcoming");
  const done = rows.filter(r => r.status !== "upcoming");
  const summary = `<p class="summary">${renderPart(esc(countLabel(done.length)))}</p>`;
  ctx.view.innerHTML = renderPage(year, summary, { shows: done, upcoming }, `No shows in ${year}.`);
}

export async function showCity(ctx) {
  const { country, region, city } = ctx.params;
  const path = [country, region, city].map(encodeURIComponent).join("/");
  const c = await get(`${apiBase()}/cities/${path}`);
  if (!ctx.alive()) return;
  const summary = `<p class="summary">${renderPart(esc(timesLabel(c.times)))}</p>`;
  ctx.view.innerHTML = renderPage(placeLabel(c), summary, c, "No shows yet.");
}
