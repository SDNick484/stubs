import assert from "node:assert/strict";
import { test } from "node:test";

import { renderSuggestions } from "../static/js/urlfetch.js";
import { showPanels } from "../static/js/registry.js";

const PAGE = "https://tickets.example.com/events/2009/cold-harbor";
const data = {
  url: PAGE,
  title: "<img src=x>",
  image: "https://tickets.example.com/images/cold-harbor-2009.jpg",
  event: { date: "2009-06-20", venue: "Granite <b>Hall</b>", performers: ["Cold Harbor", "Velvet Arcade"] },
  performers: [{ name: "Cold Harbor", on_lineup: true }, { name: "Velvet \"Arcade\"", on_lineup: false }],
  date_differs: true,
};
const show = { id: 7, date: "2009-06-21", precision: "day", editor: true };

test("renderSuggestions escapes what the page said", () => {
  const html = renderSuggestions(data, show);
  assert.match(html, /&lt;img src=x&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /Granite &lt;b&gt;Hall&lt;\/b&gt;/);
  assert.match(html, /data-value="Velvet &quot;Arcade&quot;"/);
});

test("a performer already on the lineup has no add button", () => {
  const html = renderSuggestions(data, show);
  assert.doesNotMatch(html, /data-kind="performer" data-value="Cold Harbor"/);
  assert.match(html, /Cold Harbor <span class="muted">already on the lineup<\/span>/);
  assert.match(html, /data-kind="performer" data-value="Velvet &quot;Arcade&quot;"/);
});

test("the poster, the source and a differing date are offered", () => {
  const html = renderSuggestions(data, show);
  assert.match(html, /data-kind="poster" data-value="https:\/\/tickets\.example\.com\/images\/cold-harbor-2009\.jpg"/);
  assert.match(html, /data-kind="source"/);
  assert.match(html, /Date says Jun 20, 2009/);
  assert.match(html, /this show: Jun 21, 2009/);
  const same = renderSuggestions({ ...data, date_differs: false }, show);
  assert.doesNotMatch(same, /this show:/);
});

test("only http(s) image addresses become links or poster buttons", () => {
  const html = renderSuggestions({ ...data, image: "javascript:alert(1)" }, show);
  assert.doesNotMatch(html, /javascript:/);
  assert.doesNotMatch(html, /data-kind="poster"/);
});

test("a page with nothing useful still offers itself as a source", () => {
  const html = renderSuggestions({ url: PAGE, title: null, image: null,
    event: { date: null, venue: null, performers: [] }, performers: [], date_differs: false }, show);
  assert.match(html, /Nothing else to use on that page/);
  assert.match(html, /data-kind="source"/);
});

test("the From a link panel is editor-only, at order 50", () => {
  assert.equal(showPanels({ editor: false }).find(p => p.name === "From a link"), undefined);
  const p = showPanels({ editor: true }).find(x => x.name === "From a link");
  assert.equal(p.order, 50);
  assert.equal(p.editorOnly, true);
});
