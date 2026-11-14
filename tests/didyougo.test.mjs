// DOM-free helpers of static/js/didyougo.js (Task 14).
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderDue, renderUpcoming } from "../static/js/didyougo.js";

test("renderDue says Nothing to check for an empty list", () => assert.match(renderDue([]), /Nothing to check/));

test("renderDue gives each show Yes and No, escaped", () => {
  const html = renderDue([{ id: 3, date: "2026-12-05", precision: "day", billing: "<b>Juniper</b>", venue_label: "The Bellwether" }]);
  assert.match(html, /data-act="yes"/);
  assert.match(html, /Delete this show\?/);
  assert.match(html, /&lt;b&gt;Juniper/);
  assert.doesNotMatch(html, /<b>Juniper/);
});

test("renderUpcoming lists the shows", () => {
  assert.match(renderUpcoming([{ id: 1, date: "2027", precision: "year", billing: "Marigold Static", venue_label: "" }]), /Marigold Static/);
});
