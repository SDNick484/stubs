import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCompanions, renderPeopleList } from "../static/js/companions.js";

test("renderCompanions escapes names and ticks the chosen", () => {
  const html = renderCompanions([{ id: 1, name: "<Sam>" }, { id: 2, name: "Alex" }], [1]);
  assert.ok(html.includes("&lt;Sam&gt;"));
  assert.ok(!html.includes("<Sam>"));
  assert.match(html, /value="1" checked/);
  assert.doesNotMatch(html, /value="2" checked/);
});

test("renderCompanions with nobody says so", () => {
  assert.match(renderCompanions([], []), /No one yet/);
});

test("renderPeopleList escapes and links to shows", () => {
  const html = renderPeopleList([{ id: 3, name: "<b>Jordan</b>", shows: 2 }]);
  assert.ok(!html.includes("<b>Jordan"));
  assert.ok(html.includes("&lt;b&gt;Jordan"));
  assert.ok(html.includes("2 shows"));
});
