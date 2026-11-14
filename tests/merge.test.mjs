// Task 15: the merge screen's DOM-free helpers. `node --test tests/*.test.mjs`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderMergeConfirm, renderPick } from "../static/js/merge.js";

test("renderMergeConfirm says what moves where", () => {
  const keep = { id: 1, name: "The Bellwether", shows: 9 };
  assert.equal(renderMergeConfirm({ id: 2, name: "Bellwether", shows: 3 }, keep),
    "Merge Bellwether into The Bellwether? 3 shows move.");
  assert.equal(renderMergeConfirm({ id: 2, name: "Bellwether", shows: 1 }, keep),
    "Merge Bellwether into The Bellwether? 1 show moves.");
  assert.equal(renderMergeConfirm({ id: 2, name: "Bellwether", shows: 0 }, keep),
    "Merge Bellwether into The Bellwether? No shows move.");
});

test("renderMergeConfirm escapes names", () => {
  assert.equal(renderMergeConfirm({ name: "<b>A&B</b>", shows: 2 }, { name: "\"Q\"" }),
    "Merge &lt;b&gt;A&amp;B&lt;/b&gt; into &quot;Q&quot;? 2 shows move.");
});

test("renderPick labels artists and venues, escaped", () => {
  const artist = renderPick({ id: 4, name: "Ruth's Garden", shows: 2 });
  assert.match(artist, /data-id="4"/);
  assert.match(artist, /Ruth&#39;s Garden/);
  assert.match(artist, /2 shows/);
  const venue = renderPick({ id: 5, name: "The Bellwether", city: "Oakland", region: "CA", country: "US", shows: 1 });
  assert.match(venue, /The Bellwether/);
  assert.match(venue, /Oakland, CA/);
  assert.match(venue, /1 show\b/);
});
