// DOM-free helpers of static/js/confirm.js (Task 13).
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderConfirmItem } from "../static/js/confirm.js";

const show = { id: 4, date: "2015-03-02", precision: "day", billing: "The Paper Lanterns", venue_label: "Granite Hall" };

test("a lineup item says who was the opener", () => {
  const html = renderConfirmItem({ kind: "lineup", id: 7, show, artist: "Ninefold", note: "maybe them", sources: [] });
  assert.match(html, /Opener: Ninefold/);
  assert.match(html, /maybe them/);
  assert.match(html, /The Paper Lanterns/);
});

test("a show item and escaping", () => {
  const html = renderConfirmItem({ kind: "show", id: 4, show: { ...show, billing: "<b>x</b>" }, artist: null, note: null, sources: [{ label: "scan", url: null }] });
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /scan/);
  assert.doesNotMatch(html, /Opener/);
});
