// Entry point: the hash router, the header and document.title. Feature tasks add their `import` line and their
// route(...), headerLink(...), registerShowPanel(...) and registerAdminCard(...) calls under their own
// `// ==== Task N` marker below, so parallel changes touch different parts of this file. Nothing else imports main.js.
//
//   route(pattern, render)  pattern is "" (home) or segments such as "show/:id" ("#/show/12" gives params.id "12").
//                           The most literal match wins; registering a pattern again replaces it. render(ctx) may be
//                           async; ctx = { view, params, query, session, alive, rerender }: `view` is a fresh element
//                           for this visit, `alive()` is false once the person has moved on, `rerender()` draws the
//                           same route again. A thrown ApiError shows its message (a 404 shows "not found").
//   headerLink(href, label, {editorOnly, order})  a link in the header, in order.
//
// The session comes from /api/session, or /admin/api/session on pages under /admin (Cloudflare Access guards that
// path, so only those requests carry the editor's sign-in).
import { ApiError, get } from "./api.js";
import { esc, pickRoute, parseHash } from "./core.js";
import { renderAdmin } from "./admin.js";
// Imported here once so task sections below can call them without another import line.
import { registerAdminCard, registerShowPanel } from "./registry.js";

const routes = new Map();
const links = new Map();

export function route(pattern, render) {
  routes.set(pattern, render);
}

export function headerLink(href, label, { editorOnly = false, order = 50 } = {}) {
  links.set(href, { href, label, editorOnly, order });
}

const renderNotFound = () => `<div class="notice"><p>There's nothing at this address.</p>
<p><a class="btn" href="#/">Go home</a></p></div>`;
const renderFailure = message => `<div class="notice error" role="alert"><p>${esc(message)}</p>
<p><button class="btn" type="button" data-act="retry">Try again</button></p></div>`;
const renderLink = l => `<a class="nav-link" href="${esc(l.href)}">${esc(l.label)}</a>`;
const renderNav = (session, list) => list.filter(l => session.editor || !l.editorOnly).map(renderLink).join("");
const renderFoot = session => (session.editor ? "" : `<a href="/admin#/admin">Editor sign-in</a>`);

// ==== Task 1: Scaffold — the placeholder home and #/admin
route("", ({ view }) => {
  view.innerHTML = `<h1>Shows</h1><p class="muted">Nothing here yet.</p>`;
});
route("admin", renderAdmin);
headerLink("#/admin", "Admin", { editorOnly: true, order: 90 });


// ==== Task 2: Records


// ==== Task 3: CSV parser


// ==== Task 4: Import batches


// ==== Task 5: Import screens


// ==== Task 6: Browse API


// ==== Task 7: Show page


// ==== Task 8: Artist, venue, year and city pages


// ==== Task 9: Home — map, year strip and list


// ==== Task 10: Stats
import { showStats } from "./stats.js";
route("stats", showStats);
headerLink("#/stats", "Stats", { order: 60 });


// ==== Task 11: Show editing


// ==== Task 12: Companions
import { renderPeoplePage, renderWithPanel } from "./companions.js";
route("admin/people", renderPeoplePage);
registerShowPanel("With", { order: 40, editorOnly: true, render: renderWithPanel });


// ==== Task 13: To-confirm list
import { renderConfirmCard, renderConfirmPage } from "./confirm.js";
route("admin/confirm", renderConfirmPage);
registerAdminCard("To confirm", { order: 20, render: renderConfirmCard });


// ==== Task 14: Upcoming and "Did you go?"


// ==== Task 15: Merge artists and venues
import { renderMerge } from "./merge.js";
route("admin/merge", renderMerge);
headerLink("#/admin/merge", "Merge", { editorOnly: true, order: 85 });


// ==== Task 16: Venue coordinates
import { renderMissingCard, renderVenuesPage } from "./venues.js";
route("admin/venues", renderVenuesPage);
registerAdminCard("Not on the map", { order: 40, render: renderMissingCard });


// ==== Task 17: Images
import { renderPicturesPanel } from "./images.js";
registerShowPanel("Pictures", { order: 10, editorOnly: false, render: renderPicturesPanel });


// ==== Task 18: setlist.fm panel
import "./setlist.js"; // registers the Setlist show panel (order 20)


// ==== Task 19: Fetch from a URL


// ---------------------------------------------------------------- the router (runs after every route is registered)
const host = document.getElementById("app");
const brand = document.getElementById("appName");
const nav = document.getElementById("nav");
const foot = document.getElementById("foot");
const SESSION = location.pathname.startsWith("/admin") ? "/admin/api/session" : "/api/session";
let session = null;
let visit = 0;

async function loadSession() {
  if (!session) {
    session = await get(SESSION);
    document.title = session.app_name;
    brand.textContent = session.app_name;
    const list = [...links.values()].sort((a, b) => a.order - b.order);
    nav.innerHTML = renderNav(session, list);
    foot.innerHTML = renderFoot(session);
  }
  return session;
}

async function draw() {
  const mine = ++visit;
  const { parts, query } = parseHash(location.hash);
  const picked = pickRoute([...routes.keys()], parts);
  const view = document.createElement("div");
  view.className = "view";
  view.dataset.route = picked ? picked.pattern : "?";
  host.replaceChildren(view);
  window.scrollTo(0, 0);
  const alive = () => visit === mine && view.isConnected;
  view.addEventListener("click", e => {
    if (e.target.closest?.("[data-act]")?.dataset.act === "retry") draw();
  });
  try {
    const s = await loadSession();
    if (!alive()) return;
    if (!picked) {
      view.innerHTML = renderNotFound();
      return;
    }
    await routes.get(picked.pattern)({ view, params: picked.params, query, session: s, alive, rerender: draw });
  } catch (e) {
    if (!alive()) return;
    if (e instanceof ApiError && e.status === 404) view.innerHTML = renderNotFound();
    else view.innerHTML = renderFailure(e instanceof ApiError ? e.error : "Something went wrong. Reload the page.");
    if (!(e instanceof ApiError)) console.error(e);
  }
}

window.addEventListener("hashchange", draw);
draw();
