import assert from "node:assert/strict";
import { test } from "node:test";

import {
  defaultPick, fromSetlistDate, renderAttribution, renderPanelBody, renderResults, renderSetlist, renderShowMatches,
} from "../static/js/setlist.js";
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

// ---------------------------------------------------------------- every act on the bill
const OPENER_URL = "https://www.setlist.fm/setlist/marigold-static/2025/granite-hall-oakland-ca-2b7f9a10.html";
const panel = {
  acts: [
    { lineup_id: 7, name: "Marigold <i>Static</i>", role: "opener", available: true, url: OPENER_URL,
      sets: [{ name: "", encore: false, songs: ["Static Bloom"] }] },
    { lineup_id: 8, name: "The Paper Lanterns", role: "headliner", available: false, url: URL, sets: [] },
    { lineup_id: 9, name: "Cold Harbor", role: "performer", ...data, url: "https://evil.example.com/x" },
  ],
  unlinked: [{ lineup_id: 10, name: "Ninefold", role: "opener" }],
};

test("the panel draws each linked act in order, under its name, each with its own attribution", () => {
  const html = renderPanelBody(panel, false);
  const names = [...html.matchAll(/<h3 class="setlist-act-name">(.*?)<\/h3>/g)].map(m => m[1]);
  assert.deepEqual(names, ["Marigold &lt;i&gt;Static&lt;/i&gt;", "The Paper Lanterns", "Cold Harbor"]);
  assert.ok(html.includes(`href="${OPENER_URL}"`) && html.includes(`href="${URL}"`));
  assert.equal(html.match(/Setlist from setlist\.fm/g).length, 2);  // not for the non-setlist.fm link
  assert.doesNotMatch(html, /nofollow/);
  assert.match(html, /Static Bloom/);
  assert.match(html, /Lantern Light/);
});

test("one act's set being unavailable leaves the others drawn", () => {
  const html = renderPanelBody(panel, false);
  const [opener, head, last] = html.split('<section class="setlist-act"').slice(1);
  assert.match(opener, /Static Bloom/);
  assert.match(head, /Setlist unavailable/);
  assert.ok(head.includes(`href="${URL}"`));
  assert.match(last, /Lantern Light/);
});

test("the public sees no tools and no unlinked acts; an empty panel says so", () => {
  const html = renderPanelBody(panel, false);
  assert.doesNotMatch(html, /<button/);
  assert.doesNotMatch(html, /Ninefold/);
  assert.match(renderPanelBody({ acts: [] }, false), /No setlist yet/);
  assert.match(renderPanelBody(null, false), /Setlist unavailable/);
});

test("editors get Find per act, Remove link per linked act, the unlinked acts and Find setlists", () => {
  const html = renderPanelBody(panel, true);
  assert.equal(html.match(/data-act="find"/g).length, 4);
  assert.equal(html.match(/data-act="unlink"/g).length, 3);
  assert.match(html, /data-act="find-all"[^>]*>Find setlists/);
  assert.match(html, /<li class="setlist-act" data-lineup="10">.*Ninefold/);
  assert.match(renderPanelBody({ acts: [], unlinked: [] }, true), /Find setlists/);
});

const searchAll = {
  available: true,
  acts: [
    { lineup_id: 7, name: "Marigold Static", role: "opener", linked: null,
      results: [{ id: "2b7f9a10", url: OPENER_URL, artist: "Marigold Static", venue: "Granite Hall", city: "Oakland", date: "19-07-2025" }] },
    { lineup_id: 8, name: "The Paper Lanterns", role: "headliner", linked: "zzz999",
      results: [{ id: "63ab1c4e", url: URL, artist: "The Paper Lanterns", venue: "Granite Hall", city: "Oakland", date: "19-07-2025" }] },
    { lineup_id: 10, name: "Ninefold", role: "opener", linked: null, results: [] },
  ],
  unmatched: [{ id: "4e1d8c22", url: "https://www.setlist.fm/setlist/cold-harbor/x.html", artist: "Cold <b>Harbor</b>", venue: "Granite Hall", city: "Oakland", date: "19-07-2025" }],
};

test("defaultPick: the linked one, else keep a different link, else the first", () => {
  assert.equal(defaultPick(searchAll.acts[0]), 0);
  assert.equal(defaultPick(searchAll.acts[1]), -1);
  assert.equal(defaultPick({ ...searchAll.acts[1], linked: "63ab1c4e" }), 0);
  assert.equal(defaultPick(searchAll.acts[2]), -1);
});

test("the whole-show search offers one choice per act, lists the rest, and links no stranger", () => {
  const html = renderShowMatches(searchAll);
  const groups = html.split('<fieldset class="setlist-match"').slice(1);
  assert.equal(groups.length, 2);
  assert.match(groups[0], /data-lineup="7"/);
  assert.match(groups[0], /value="2b7f9a10" data-url="[^"]+" checked/);
  assert.match(groups[0], /value="">.*Don&#39;t link/);
  assert.match(groups[1], /value="63ab1c4e" data-url="[^"]+">/);  // not ticked: it would replace another link
  assert.match(groups[1], /value="" checked>.*Keep the current link/);
  assert.match(html, /Nothing found for Ninefold/);
  assert.match(html, /Cold &lt;b&gt;Harbor&lt;\/b&gt;/);
  assert.doesNotMatch(html, /value="4e1d8c22"/);
  assert.match(html, /type="submit"[^>]*>Link selected/);
});

test("the whole-show search when there is nothing, or setlist.fm is down", () => {
  assert.match(renderShowMatches({ available: true, acts: [{ ...searchAll.acts[2] }], unmatched: [] }), /Nothing on setlist\.fm/);
  assert.match(renderShowMatches({ available: false, acts: [], unmatched: [] }), /can.t be searched/);
  const strangersOnly = renderShowMatches({ available: true, acts: [], unmatched: searchAll.unmatched });
  assert.match(strangersOnly, /Not on this show/);
  assert.doesNotMatch(strangersOnly, /Link selected/);
});
