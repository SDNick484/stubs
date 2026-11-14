// Frontend unit tests: `node --test tests/*.test.mjs`. The modules under static/js/ are ES modules with no
// top-level DOM access (main.js aside), so they load in Node as they are; only DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { esc, fmtDate, matchRoute, parseHash, pickRoute, placeLabel, renderShowRow } from "../static/js/core.js";
import { adminCards, registerAdminCard, registerShowPanel, showPanels } from "../static/js/registry.js";
import { NOTHING } from "../static/js/admin.js";

test("esc escapes the five", () => assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;"));

test("esc leaves nothing out", () => {
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(0), "0");
  assert.equal(esc("&amp;"), "&amp;amp;");
});

test("fmtDate by precision", () => {
  assert.equal(fmtDate("2027", "year"), "2027");
  assert.equal(fmtDate("2025-07", "month"), "Jul 2025");
  assert.equal(fmtDate("2025-07-19", "day"), "Jul 19, 2025");
});

test("fmtDate infers the precision, and escapes what it can't read", () => {
  assert.equal(fmtDate("2025-12-01"), "Dec 1, 2025");
  assert.equal(fmtDate("2025-01"), "Jan 2025");
  assert.equal(fmtDate(null), "");
  assert.equal(fmtDate("<b>soon</b>"), "&lt;b&gt;soon&lt;/b&gt;");
  assert.equal(fmtDate("2025-13", "month"), "2025-13");
});

test("registry keeps order", () => {
  registerShowPanel("later", { order: 20, editorOnly: false, render: async () => {} });
  registerShowPanel("first", { order: 10, editorOnly: false, render: async () => {} });
  registerShowPanel("notes", { order: 15, editorOnly: true, render: async () => {} });
  assert.deepEqual(showPanels({ editor: true }).map(p => p.name), ["first", "notes", "later"]);
  assert.deepEqual(showPanels({ editor: false }).map(p => p.name), ["first", "later"]);

  registerAdminCard("b", { order: 30, render: async () => {} });
  registerAdminCard("a", { order: 10, render: async () => {} });
  assert.deepEqual(adminCards().map(c => c.name), ["a", "b"]);
  registerAdminCard("b", { order: 5, render: async () => {} }); // the same name again replaces it
  assert.deepEqual(adminCards().map(c => c.name), ["b", "a"]);
});

test("registry rejects a panel without a render function", () => {
  assert.throws(() => registerShowPanel("x", { order: 1 }), /render/);
  assert.throws(() => registerAdminCard("x", { order: "1", render: async () => {} }), /order/);
});

test("parseHash", () => {
  assert.deepEqual(parseHash("#/show/12?x=1"), { parts: ["show", "12"], query: { x: "1" } });
  assert.deepEqual(parseHash(""), { parts: [], query: {} });
  assert.deepEqual(parseHash("#/?year=2009"), { parts: [], query: { year: "2009" } });
  assert.deepEqual(parseHash("#/city/US/CA/San%20Francisco"), { parts: ["city", "US", "CA", "San Francisco"], query: {} });
});

test("routes match by segment, literal segments first", () => {
  assert.deepEqual(matchRoute("show/:id", ["show", "12"]), { id: "12" });
  assert.equal(matchRoute("show/:id", ["show"]), null);
  assert.deepEqual(matchRoute("", []), {});
  const picked = pickRoute(["admin/:id", "admin/import", ""], ["admin", "import"]);
  assert.deepEqual(picked, { pattern: "admin/import", params: {} });
  assert.equal(pickRoute(["admin"], ["nope"]), null);
});

test("renderShowRow escapes and links", () => {
  const row = {
    id: 7, date: "2009-06-20", precision: "day", sort_date: "2009-06-20", billing: "Cold Harbor <b>+</b> Velvet Arcade",
    status: "attended", venue: { id: 3, label: "Summit Pavilion", city: "Concord", region: "CA", country: "US" },
    run: null, headliners: ["Cold Harbor", "Velvet Arcade"],
  };
  const html = renderShowRow(row);
  assert.match(html, /href="#\/show\/7"/);
  assert.match(html, /Jun 20, 2009/);
  assert.match(html, /Cold Harbor &lt;b&gt;\+&lt;\/b&gt; Velvet Arcade/);
  assert.match(html, /Summit Pavilion/);
  assert.match(html, /Concord, CA/);
  assert.doesNotMatch(html, /Upcoming/);
  assert.match(renderShowRow({ ...row, status: "upcoming" }), /Upcoming/);
  assert.match(renderShowRow({ ...row, role: "opener" }), /Opener/);
});

test("placeLabel", () => {
  assert.equal(placeLabel({ city: "Concord", region: "CA", country: "US" }), "Concord, CA");
  assert.equal(placeLabel({ city: "Lyon", region: null, country: "FR" }), "Lyon, FR");
  assert.equal(placeLabel({ city: "Lyon" }), "Lyon");
});

test("the admin screen's empty message", () => assert.equal(NOTHING, "Nothing needs you."));
