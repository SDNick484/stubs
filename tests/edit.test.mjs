// Task 11: the show editor's DOM-free helpers. `node --test tests/*.test.mjs`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { collectLineup, collectShow, moveRow, renderLineupRow, renderSaveError } from "../static/js/edit.js";

const row = (over = {}) => ({ id: "", name: "", role: "opener", likely: false, note: "", rating: "", ...over });

test("collectLineup keeps order and ids", () => {
  const formState = {
    lineup: [
      row({ id: "12", name: " Cold Harbor ", role: "coheadliner", rating: "4" }),
      row({ name: "Ruth’s Garden", likely: true, note: " only on the poster " }),
      row({ id: "7", name: "Marigold Static", role: "headliner" }),
    ],
  };
  assert.deepEqual(collectLineup(formState), [
    { id: 12, name: "Cold Harbor", role: "coheadliner", confidence: "confirmed", confidence_note: null, rating: 4 },
    { name: "Ruth’s Garden", role: "opener", confidence: "likely", confidence_note: "only on the poster", rating: null },
    { id: 7, name: "Marigold Static", role: "headliner", confidence: "confirmed", confidence_note: null, rating: null },
  ]);
});

test("collectLineup drops blank rows and keeps a confirmed entry's note", () => {
  const formState = { lineup: [row({ name: "  " }), row({ id: "3", name: "Cold Harbor", note: " stub scan " })] };
  assert.deepEqual(collectLineup(formState), [
    { id: 3, name: "Cold Harbor", role: "opener", confidence: "confirmed", confidence_note: "stub scan", rating: null },
  ]);
  assert.deepEqual(collectLineup({ lineup: [] }), []);
  assert.deepEqual(collectLineup({}), []);
});

test("collectShow sends a picked venue by id and blanks as null", () => {
  const fields = collectShow({
    status: "attended", date: " 2019-05-04 ", billing: " Marigold Static ", venueId: "3", venueLabel: "Granite Hall",
    run: "", tour: " Static Tour ", notes: "", rating: "5", likely: false, confidenceNote: "x",
  });
  assert.deepEqual(fields, {
    status: "attended", date: "2019-05-04", billing: "Marigold Static", venue_id: 3, venue_label: "Granite Hall",
    run: null, tour: "Static Tour", notes: null, rating: 5, confidence: "confirmed", confidence_note: "x",
  });
});

test("collectShow sends a new venue by name and place", () => {
  const fields = collectShow({
    status: "upcoming", date: "2027", billing: "Cold Harbor", venueId: "", newVenue: " Rio Theatre ",
    city: " Santa Cruz ", region: "CA", country: "us", venueLabel: "", likely: true, confidenceNote: " guess ",
  });
  assert.equal(fields.venue_id, undefined);
  assert.deepEqual([fields.venue, fields.city, fields.region, fields.country], ["Rio Theatre", "Santa Cruz", "CA", "US"]);
  assert.equal(fields.venue_label, null);
  assert.deepEqual([fields.confidence, fields.confidence_note], ["likely", "guess"]);
});

test("moveRow swaps neighbours and ignores moves off either end", () => {
  const rows = ["a", "b", "c"];
  assert.deepEqual(moveRow(rows, 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveRow(rows, 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveRow(rows, 0, -1), rows);
  assert.deepEqual(moveRow(rows, 2, 1), rows);
  assert.deepEqual(rows, ["a", "b", "c"]); // the input is not changed
});

test("renderLineupRow escapes what it is given and keeps the id", () => {
  const html = renderLineupRow(row({ id: "12", name: "<b>Ruth's</b>", note: "\"x\"", likely: true, rating: "3" }), 0, 2);
  assert.match(html, /data-id="12"/);
  assert.match(html, /value="&lt;b&gt;Ruth&#39;s&lt;\/b&gt;"/);
  assert.match(html, /value="&quot;x&quot;"/);
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /<option value="3" selected>/);
  assert.match(html, /type="checkbox"[^>]*checked/);
});

test("renderSaveError offers Reload only for a stale save", () => {
  const stale = renderSaveError({ status: 409, error: "Someone else just changed this show. Reload to see it." });
  assert.match(stale, /Someone else just changed this show\. Reload to see it\./);
  assert.match(stale, /data-act="reload"/);
  const bad = renderSaveError({ status: 400, error: "rating must be 1 to 5 <b>" });
  assert.doesNotMatch(bad, /data-act="reload"/);
  assert.match(bad, /&lt;b&gt;/);
});
