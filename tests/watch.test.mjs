// DOM-free helpers of static/js/watch.js (post-v1 #47).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  parseNumber, renderArtistWatch, renderCardTitle, renderLastfm, renderSighting, renderSources, renderWatchedRow,
  seenLabel,
} from "../static/js/watch.js";

const SIGHTING = { id: 4, artist: "<b>Marigold</b> Static", date: "2027-03-14", venue: "Old Mill Theatre", city: "Portland",
  source: "ticketmaster", url: "https://www.ticketmaster.com/event/Z7MS0001", note: null, have: null, status: "new" };

test("seenLabel says Never seen, once, twice and N times", () => {
  assert.deepEqual([0, 1, 2, 7].map(seenLabel), ["Never seen", "Seen once", "Seen twice", "Seen 7 times"]);
});

test("renderSighting lists artist, date and place, escaped, with Add and an inline Dismiss", () => {
  const html = renderSighting(SIGHTING);
  assert.match(html, /&lt;b&gt;Marigold&lt;\/b&gt; Static/);
  assert.doesNotMatch(html, /<b>Marigold/);
  assert.match(html, /Mar 14, 2027 · Old Mill Theatre, Portland/);
  assert.match(html, /data-act="add"/);
  assert.match(html, /Dismiss this concert\?/);
  assert.match(html, /href="https:\/\/www\.ticketmaster\.com\/event\/Z7MS0001"[^>]*>Ticketmaster</);
});

test("renderSighting: You have tickets, and no Add", () => {
  const html = renderSighting({ ...SIGHTING, have: 12 });
  assert.match(html, /href="#\/show\/12"[^>]*>You have tickets</);
  assert.doesNotMatch(html, /data-act="add"/);
});

test("renderSighting never links a URL that isn't http(s)", () => {
  const html = renderSighting({ ...SIGHTING, url: "javascript:alert(1)", source: "email" });
  assert.doesNotMatch(html, /javascript:/);
  assert.match(html, /<span>Email<\/span>/);
});

test("renderCardTitle counts the new concerts", () => assert.match(renderCardTitle(3), /New near you \(3\)/));

test("renderWatchedRow: never seen, switches, escaped name", () => {
  const html = renderWatchedRow({ artist_id: 5, name: "Cold <Harbor>", origin: "manual", anywhere: true, muted: false,
    last_checked: null, times: 0, last: null, sightings: [] });
  assert.match(html, /Never seen/);
  assert.match(html, /Cold &lt;Harbor&gt;/);
  assert.match(html, /data-act="anywhere" checked/);
  assert.doesNotMatch(html, /data-act="muted" checked/);
  assert.match(html, /Not checked yet/);
  assert.match(html, /Stop watching Cold &lt;Harbor&gt;\?/);
});

test("renderWatchedRow: times seen, last seen, last checked and sightings", () => {
  const html = renderWatchedRow({ artist_id: 5, name: "Cold Harbor", origin: "history", anywhere: false, muted: true,
    last_checked: "2026-11-14 09:00:00", times: 3, last: "2012-01-01",
    sightings: [{ date: "2027-02-20", venue: "Granite Hall", city: "Oakland", status: "added" }] });
  assert.match(html, /Seen 3 times · last Jan 1, 2012/);
  assert.match(html, /Checked Nov 14, 2026/);
  assert.match(html, /Feb 20, 2027 · Granite Hall, Oakland <span class="tag">added/);
});

test("renderLastfm escapes and links to Last.fm", () => {
  const html = renderLastfm([{ name: "Velvet <i>Arcade</i>", playcount: 120, seen: false, url: "https://www.last.fm/music/Velvet+Arcade" }]);
  assert.match(html, /Velvet &lt;i&gt;Arcade/);
  assert.match(html, /120 plays · never seen/);
  assert.match(html, /href="https:\/\/www\.last\.fm\/music\/Velvet\+Arcade"/);
  assert.match(renderLastfm([]), /No new suggestions/);
});

test("renderSources says what is on and never more than the push format", () => {
  const html = renderSources({ ticketmaster: true, lastfm: false, email: true, notify: "ntfy" });
  assert.match(html, /Ticketmaster on/);
  assert.match(html, /Last.fm suggestions off/);
  assert.match(html, /Push ntfy/);
  assert.match(renderSources({ ticketmaster: false, lastfm: false, email: true, notify: null }), /Push off/);
});

test("renderArtistWatch toggles between Watch and Watching", () => {
  assert.match(renderArtistWatch({ watching: false, sightings: [] }), />Watch</);
  const on = renderArtistWatch({ watching: true, sightings: [SIGHTING] });
  assert.match(on, />Watching</);
  assert.match(on, /Stop watching\?/);
  assert.match(on, /Old Mill Theatre, Portland/);
});

test("parseNumber keeps numbers in range only", () => {
  assert.equal(parseNumber(" 37.8 ", -90, 90), 37.8);
  assert.equal(parseNumber("91", -90, 90), null);
  assert.equal(parseNumber("", -90, 90), null);
  assert.equal(parseNumber("abc", -90, 90), null);
});
