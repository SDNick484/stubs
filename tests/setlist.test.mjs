import assert from "node:assert/strict";
import { test } from "node:test";

import { fromSetlistDate, renderAttribution, renderResults, renderSetlist } from "../static/js/setlist.js";
import { showPanels } from "../static/js/registry.js";

const URL = "https://www.setlist.fm/setlist/the-paper-lanterns/2025/granite-hall-oakland-ca-63ab1c4e.html";
const data = {
  available: true, url: URL,
  sets: [
    { name: "", encore: false, songs: ["Lantern Light", "Slow <b>Harbor</b>"] },
    { name: "", encore: true, songs: ["Last Match"] },
  ],
};

test("renderSetlist links to setlist.fm, followable, and marks the encore", () => {
  const html = renderSetlist(data);
  assert.ok(html.includes(`href="https://www.setlist.fm/`));
  assert.doesNotMatch(html, /nofollow/);
  assert.match(html, /Setlist from setlist\.fm/);
  assert.match(html, /Encore/);
  assert.match(html, /Lantern Light/);
});

test("renderSetlist escapes song titles", () => {
  const html = renderSetlist(data);
  assert.match(html, /Slow &lt;b&gt;Harbor&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>/);
});

test("unavailable says so, with the stored link when there is one", () => {
  assert.match(renderSetlist({ available: false, url: null, sets: [] }), /Setlist unavailable/);
  assert.doesNotMatch(renderSetlist({ available: false, url: null, sets: [] }), /href/);
  assert.ok(renderSetlist({ available: false, url: URL, sets: [] }).includes(`href="${URL}"`));
});

test("only setlist.fm links are drawn", () => {
  assert.equal(renderAttribution("https://evil.example.com/x"), "");
  assert.equal(renderAttribution("javascript:alert(1)"), "");
});

test("search results offer each match", () => {
  const html = renderResults({ available: true, results: [{ id: "63ab1c4e", url: URL, artist: "The Paper Lanterns", venue: "Granite Hall", city: "Oakland", date: "19-07-2025" }] });
  assert.match(html, /Jul 19, 2025/);
  assert.match(html, /data-id="63ab1c4e"/);
  assert.match(renderResults({ available: true, results: [] }), /Nothing on setlist\.fm/);
  assert.match(renderResults({ available: false, results: [] }), /can.t be searched/);
  assert.equal(fromSetlistDate("19-07-2025"), "2025-07-19");
});

test("the panel registers at order 20 for everyone", () => {
  const p = showPanels({ editor: false }).find(x => x.name === "Setlist");
  assert.equal(p.order, 20);
});
