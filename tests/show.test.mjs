// Task 7 frontend helpers: node --test tests/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderLineup, renderSources } from "../static/js/show.js";

const lineup = [
  { artist_id: 4, name: "Opener <b>One</b>", role: "opener", confidence: "likely", confidence_note: "from a poster", rating: 3, sources: [] },
  { artist_id: 1, name: "Cold Harbor", role: "headliner", confidence: "confirmed", rating: 5, sources: [{ label: "Ticket", url: "https://concerts.example.com/t" }] },
  { artist_id: 2, name: "Marigold Static", role: "coheadliner", confidence: "confirmed" },
  { artist_id: 3, name: "Festival Act", role: "performer", confidence: "confirmed" },
];

test("renderLineup groups by role in order", () => {
  const html = renderLineup(lineup, false);
  const at = ["Headliner", "Co-headliners", "Openers", "Performers"].map(t => html.indexOf(`<h3>${t}</h3>`));
  assert.ok(at.every(i => i >= 0));
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.match(html, /href="#\/artist\/1"/);
});

test("renderLineup leaves out empty groups", () => {
  const html = renderLineup([lineup[1]], false);
  assert.doesNotMatch(html, /Openers/);
});

test("renderLineup escapes names", () => {
  const html = renderLineup(lineup, true);
  assert.match(html, /Opener &lt;b&gt;One&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>One/);
});

test("likely, ratings and sources only for editors", () => {
  const visitor = renderLineup(lineup, false);
  assert.doesNotMatch(visitor, /likely|★|Ticket|from a poster/);
  const editor = renderLineup(lineup, true);
  assert.match(editor, /class="tag tag-likely" title="from a poster"/);
  assert.match(editor, /★ 5\/5/);
  assert.match(editor, /Ticket/);
});

test("renderSources links only http(s)", () => {
  const html = renderSources([{ label: "Bad", url: "javascript:alert(1)" }, { label: "Good", url: "https://concerts.example.com/x" }]);
  assert.doesNotMatch(html, /javascript:/);
  assert.match(html, /href="https:\/\/concerts\.example\.com\/x"/);
  assert.equal(renderSources([]), "");
});
