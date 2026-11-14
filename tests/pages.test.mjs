import assert from "node:assert/strict";
import { test } from "node:test";

import { renderArtistSummary, renderVenueSummary } from "../static/js/pages.js";

const artist = (times, by_role, first = "2005-04-02", last = "2016-12-01") => ({ id: 1, name: "X", times, first, last, by_role });

test("renderArtistSummary says Seen once for one show", () => {
  assert.match(renderArtistSummary(artist(1, { headliner: 1 })), /Seen once/);
});

test("renderArtistSummary says Seen 3 times with the first and last dates", () => {
  const html = renderArtistSummary(artist(3, { headliner: 2, opener: 1 }));
  assert.match(html, /Seen 3 times/);
  assert.match(html, /first Apr 2, 2005/);
  assert.match(html, /last Dec 1, 2016/);
});

test("renderArtistSummary lists only the roles that are not zero", () => {
  const html = renderArtistSummary(artist(3, { headliner: 2, opener: 1, performer: 0 }));
  assert.match(html, /Headliner/);
  assert.match(html, /Opener/);
  assert.doesNotMatch(html, /Performer|Co-headliner/);
});

test("renderArtistSummary escapes the dates it can't read", () => {
  assert.doesNotMatch(renderArtistSummary(artist(2, {}, "<b>", "<i>")), /<b>|<i>/);
});

const venue = names => ({ id: 2, name: names[0], names, city: "Oakland", region: "CA", country: "US", times: 2 });

test("renderVenueSummary omits Also known as with one name", () => {
  const html = renderVenueSummary(venue(["Granite Hall"]));
  assert.doesNotMatch(html, /Also known as/);
  assert.match(html, /Oakland, CA/);
  assert.match(html, /Seen twice/);
});

test("renderVenueSummary lists former names and links the city", () => {
  const html = renderVenueSummary(venue(["Granite Hall", "Old <Armory>"]));
  assert.match(html, /Also known as Old &lt;Armory&gt;/);
  assert.match(html, /href="#\/city\/US\/CA\/Oakland"/);
});
