// DOM-free helpers of static/js/imports.js (Task 5).
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderGroups, renderImportRow } from "../static/js/imports.js";

const parsed = { billing: "Cold Harbor", date: "2009-06-20", venue: "Summit Pavilion", city: "Concord", region: "CA",
  country: "US", flags: [], acts: [{ name: "Cold Harbor", role: "headliner", confidence: "confirmed" }] };
const row = over => ({ id: 3, row_no: 4, verdict: "new", reason: "", include: true, parsed, match: null, differs: [], ...over });

test("billing is escaped and a check-acts flag shows", () => {
  const html = renderImportRow(row({ parsed: { ...parsed, billing: "<b>x</b>", flags: ["check acts"] } }));
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /check acts/);
});

test("a have row lists what differs and has no tick or role select", () => {
  const html = renderImportRow(row({ verdict: "have", include: false, differs: ["tour"],
    match: { id: 9, date: "2009-06-20", billing: "Cold Harbor", venue_label: "Summit Pavilion" } }));
  assert.match(html, /Differs: tour/);
  assert.match(html, /href="#\/show\/9"/);
  assert.doesNotMatch(html, /import-include/);
  assert.doesNotMatch(html, /<select/);
});

test("a new row has a ticked include box and editable acts", () => {
  const html = renderImportRow(row());
  assert.match(html, /import-include" checked/);
  assert.match(html, /<option value="headliner" selected>/);
});

test("groups carry their counts and empty groups are left out", () => {
  const html = renderGroups([row(), row({ id: 4, verdict: "skip", include: false, reason: "not attended" })]);
  assert.match(html, /New \(1\)/);
  assert.match(html, /Skipped \(1\)/);
  assert.doesNotMatch(html, /Already have/);
});
