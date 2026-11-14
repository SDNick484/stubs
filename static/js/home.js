// #/: the map of every venue with coordinates (clustered markers), the year strip and the show list for the
// selection (#/?year=2009). Leaflet is loaded on demand by leaflet-loader.js. No DOM at import.
import { get } from "./api.js";
import { esc, fmtDate, renderShowRow } from "./core.js";
import { loadLeaflet } from "./leaflet-loader.js";

const ATTRIBUTION = "© OpenStreetMap contributors";

const countLabel = n => (n === 1 ? "1 show" : `${n} shows`);
const renderCurrent = on => (on ? ` aria-current="true"` : "");
const pillClass = on => (on ? "year-pill is-selected" : "year-pill");

const renderPill = (href, label, title, on) =>
  `<a class="${esc(pillClass(on))}" href="${esc(href)}"${renderCurrent(on)} title="${esc(title)}">${esc(label)}</a>`;

const renderYearPills = (years, sel) =>
  years.map(y => renderPill(`#/?year=${y.year}`, y.year, countLabel(y.count), y.year === sel)).join("");

// "All" plus each year, the selected one marked (aria-current and is-selected). `selected` is a year (number or
// digit string) or null/undefined for All. Each year links to #/?year=YYYY.
export function renderYearStrip(years, selected) {
  const sel = selected === null || selected === undefined || selected === "" ? null : Number(selected);
  return `<nav class="year-strip" aria-label="Years">${renderPill("#/", "All", "All shows", sel === null)}${renderYearPills(years, sel)}</nav>`;
}

export const renderUnmapped = n => (n > 0 ? `<p class="muted unmapped">${esc(countLabel(n))} not on the map</p>` : "");

const renderPopupShow = s => `<li><a href="#/show/${esc(s.id)}">${fmtDate(s.date, s.precision)} · ${esc(s.billing)}</a></li>`;

const renderPopupShows = shows => shows.map(renderPopupShow).join("");

// The popup for one map point: the venue and its shows in the selection, each linking to its page.
export const renderPopup = (point, shows) => `<div class="map-popup"><strong>${esc(point.name)}</strong>
<span class="muted">${esc(point.city)}</span>
<ul>${renderPopupShows(shows)}</ul></div>`;

const renderRows = shows => shows.map(renderShowRow).join("\n");

const renderList = shows =>
  shows.length
    ? `<ul class="show-list">${renderRows(shows)}</ul>`
    : `<p class="muted">No shows yet.</p>`;

const heading = year => (year === null ? "All shows" : String(year));
const validYear = q => (/^\d{4}$/.test(q ?? "") ? Number(q) : null);

function drawMap(L, el, points, shows, tileUrl) {
  const map = L.map(el, { worldCopyJump: true });
  L.tileLayer(tileUrl, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(map);
  const byId = new Map(shows.map(s => [s.id, s]));
  const cluster = L.markerClusterGroup();
  for (const p of points) {
    const mine = p.show_ids.map(id => byId.get(id)).filter(Boolean);
    cluster.addLayer(L.marker([p.lat, p.lon], { title: p.name }).bindPopup(renderPopup(p, mine)));
  }
  map.addLayer(cluster);
  if (points.length) map.fitBounds(L.latLngBounds(points.map(p => [p.lat, p.lon])), { padding: [30, 30], maxZoom: 12 });
  else map.setView([20, 0], 2);
  return map;
}

export async function showHome(ctx) {
  const year = validYear(ctx.query.year);
  const q = year === null ? "" : `?year=${year}`;
  const [years, shows, pts] = await Promise.all([get("/api/years"), get(`/api/shows${q}`), get(`/api/map${q}`)]);
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<h1>Shows</h1>
${renderYearStrip(years, year)}
<div class="map" id="map" role="region" aria-label="Map of venues"></div>
<div class="map-note">${renderUnmapped(pts.unmapped)}</div>
<section class="home-list"><h2>${esc(heading(year))} <span class="muted">${esc(shows.length)}</span></h2>
${renderList(shows)}</section>`;
  const el = ctx.view.querySelector("#map");
  try {
    const L = await loadLeaflet();
    if (!ctx.alive()) return;
    drawMap(L, el, pts.points, shows, ctx.session.tile_url);
  } catch (e) {
    if (!ctx.alive()) return;
    console.error(e);
    el.textContent = "The map could not be loaded.";
  }
}
