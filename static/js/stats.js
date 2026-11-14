// #/stats: totals, shows per year as a bar list, and the top artists, venues and cities. No DOM at import.
import { get } from "./api.js";
import { esc, placeLabel } from "./core.js";

// The bar for each year; the longest is 100% wide, the rest in proportion. The width is a data-pct attribute that
// applyBars() turns into a width (the CSP forbids inline styles in markup). Years with no shows are not in the list.
export function renderBars(perYear) {
  const max = Math.max(0, ...perYear.map(y => y.count));
  if (!max) return "";
  return perYear
    .map(y => `<li class="bar-row"><a class="bar-year" href="#/year/${esc(y.year)}">${esc(y.year)}</a>
  <span class="bar-track"><span class="bar" data-pct="${esc(Math.round((y.count / max) * 100))}"></span></span>
  <span class="bar-count">${esc(y.count)}</span></li>`)
    .join("\n");
}

// A missing region is "-" in the address.
export const cityHref = c =>
  `#/city/${encodeURIComponent(c.country || "-")}/${encodeURIComponent(c.region || "-")}/${encodeURIComponent(c.city)}`;

const artistHref = a => `#/artist/${a.artist_id}`;
const venueHref = v => `#/venue/${v.venue_id}`;

const renderTotal = (label, n) =>
  `<div class="stat"><span class="stat-n">${esc(n)}</span> <span class="stat-label">${esc(label)}</span></div>`;

const renderTopItem = (item, href, label) =>
  `<li><a href="${esc(href(item))}">${esc(label(item))}</a> <span class="muted">${esc(item.count)}</span></li>`;

const renderTop = (title, items, href, label) => `<section class="top-list"><h2>${esc(title)}</h2>
<ol>${renderTopItems(items, href, label)}</ol>
</section>`;

function renderTopItems(items, href, label) {
  return items.map(i => renderTopItem(i, href, label)).join("");
}

const renderTotals = t =>
  `<div class="stat-totals">${renderTotal("shows", t.shows)}${renderTotal("artists", t.artists)}${renderTotal("venues", t.venues)}${renderTotal("cities", t.cities)}</div>`;

export function renderStats(s) {
  return `<h1>Stats</h1>
${renderTotals(s.totals)}
<section><h2>Shows per year</h2><ul class="bars">${renderBars(s.per_year)}</ul></section>
${renderTop("Top artists", s.top_artists, artistHref, a => a.name)}
${renderTop("Top venues", s.top_venues, venueHref, v => v.name)}
${renderTop("Top cities", s.top_cities, cityHref, placeLabel)}`;
}

export async function showStats(ctx) {
  const data = await get("/api/stats");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = renderStats(data);
  for (const el of ctx.view.querySelectorAll(".bar[data-pct]")) el.style.width = `${el.dataset.pct}%`;
}
