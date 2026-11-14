// Shared helpers. No top-level DOM access, so Node can import this file (tests/*.test.mjs).
//
// The escaping rule: everything from the server reaches innerHTML only through esc(). Markup is built in template
// literals whose every ${...} is exactly one call to esc, fmtDate or a render* function (each of which returns markup
// it escaped itself). tests/test_frontend.py scans every module in static/js/ for this. It doesn't parse regex
// literals, so keep quote characters and backticks out of them (write \x22, \x27, \x60).

// ---------------------------------------------------------------- escaping
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>\x22\x27]/g, c => ESC[c]);
}

// ---------------------------------------------------------------- dates
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DATE = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/;

// A show date at its precision: ("2027", "year") -> "2027", ("2025-07", "month") -> "Jul 2025",
// ("2025-07-19", "day") -> "Jul 19, 2025". Without a precision it is read from the date's shape. The result is
// escaped, so it can go straight into markup; a date it can't read comes back as given (escaped).
export function fmtDate(date, precision) {
  if (date === null || date === undefined) return "";
  const m = DATE.exec(String(date));
  if (!m) return esc(date);
  const [, y, mo, d] = m;
  const p = precision || (d ? "day" : mo ? "month" : "year");
  const month = MONTHS[Number(mo) - 1];
  if (p === "year") return y;
  if (!month) return esc(date);
  if (p === "month") return `${month} ${y}`;
  if (!d) return esc(date);
  return `${month} ${Number(d)}, ${y}`;
}

// ---------------------------------------------------------------- places
// "Concord, CA" (US and Canadian cities carry a region), "Lyon, FR", or the city alone.
export function placeLabel(venue) {
  if (!venue) return "";
  const where = venue.region || venue.country;
  return where ? `${venue.city}, ${where}` : String(venue.city ?? "");
}

// ---------------------------------------------------------------- show rows
const ROLES = { headliner: "Headliner", coheadliner: "Co-headliner", opener: "Opener", performer: "Performer" };
export const roleLabel = role => ROLES[role] ?? "";

const renderRole = role => (ROLES[role] ? `<span class="show-role">${esc(ROLES[role])}</span>` : "");
const renderUpcoming = status => (status === "upcoming" ? `<span class="tag tag-upcoming">Upcoming</span>` : "");

// One show in a list (the show row shape of browse.show_row, plus an optional `role` on artist pages).
export function renderShowRow(row) {
  const venue = row.venue ?? {};
  return `<li class="show-row${esc(row.status === "upcoming" ? " is-upcoming" : "")}">
  <a class="show-link" href="#/show/${esc(row.id)}">
    <span class="show-date">${fmtDate(row.date, row.precision)}</span>
    <span class="show-billing">${esc(row.billing)}</span>
    <span class="show-place">${esc(venue.label)} · ${esc(placeLabel(venue))}</span>
  </a>${renderRole(row.role)}${renderUpcoming(row.status)}
</li>`;
}

// ---------------------------------------------------------------- routing
// "#/show/12?x=1" -> { parts: ["show", "12"], query: { x: "1" } }. Segments are URI-decoded.
export function parseHash(hash) {
  const [path, qs = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(p => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p;
    }
  });
  return { parts, query: Object.fromEntries(new URLSearchParams(qs)) };
}

// matchRoute("show/:id", ["show", "12"]) -> { id: "12" }; null when the pattern doesn't fit. "" is the home route.
export function matchRoute(pattern, parts) {
  const segs = pattern.split("/").filter(Boolean);
  if (segs.length !== parts.length) return null;
  const params = {};
  for (let i = 0; i < segs.length; i++) {
    if (segs[i].startsWith(":")) params[segs[i].slice(1)] = parts[i];
    else if (segs[i] !== parts[i]) return null;
  }
  return params;
}

// The best of several patterns for these parts: the match with the most literal segments, the first one on a tie.
export function pickRoute(patterns, parts) {
  let best = null;
  let bestScore = -1;
  for (const pattern of patterns) {
    const params = matchRoute(pattern, parts);
    if (!params) continue;
    const score = pattern.split("/").filter(s => s && !s.startsWith(":")).length;
    if (score > bestScore) {
      best = { pattern, params };
      bestScore = score;
    }
  }
  return best;
}
